import Foundation
import AVFoundation
import Speech

func errorOut(_ message: String) {
    FileHandle.standardError.write(Data((message + "\n").utf8))
}

let args = CommandLine.arguments
let localeIndex = args.firstIndex(of: "--locale")
let localeID = localeIndex.flatMap { $0 + 1 < args.count ? args[$0 + 1] : nil } ?? "en-US"

if args.contains("--check") {
    let data: [String: Any] = [
        "locale": localeID,
        "locale_supported": SFSpeechRecognizer.supportedLocales().contains(Locale(identifier: localeID)),
        "speech_authorization": [0: "not_determined", 1: "denied", 2: "restricted", 3: "authorized"][SFSpeechRecognizer.authorizationStatus().rawValue] ?? "unknown",
        "microphone_authorization": [0: "not_determined", 1: "restricted", 2: "denied", 3: "authorized"][AVCaptureDevice.authorizationStatus(for: .audio).rawValue] ?? "unknown"
    ]
    let json = try JSONSerialization.data(withJSONObject: data, options: [.sortedKeys])
    print(String(data: json, encoding: .utf8)!)
    exit(0)
}

final class Listener {
    let engine = AVAudioEngine()
    let request = SFSpeechAudioBufferRecognitionRequest()
    var task: SFSpeechRecognitionTask?
    var silenceTimer: Timer?
    var deadline: Timer?
    var lastText = ""
    var finished = false
    var installedTap = false

    func start() {
        SFSpeechRecognizer.requestAuthorization { status in
            DispatchQueue.main.async {
                guard status == .authorized else {
                    self.finish(error: "Allow GroceryVoice to use speech recognition in System Settings.")
                    return
                }
                AVCaptureDevice.requestAccess(for: .audio) { granted in
                    DispatchQueue.main.async {
                        guard granted else {
                            self.finish(error: "Allow GroceryVoice to use the microphone in System Settings.")
                            return
                        }
                        self.record()
                    }
                }
            }
        }
    }

    func record() {
        guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: localeID)), recognizer.isAvailable else {
            finish(error: "Speech recognition is unavailable for this language; check the language and network settings.")
            return
        }
        request.shouldReportPartialResults = true
        request.taskHint = .confirmation
        if recognizer.supportsOnDeviceRecognition {
            request.requiresOnDeviceRecognition = true
        }
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0 && format.channelCount > 0 else {
            finish(error: "No microphone is available.")
            return
        }
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            self.request.append(buffer)
        }
        installedTap = true
        task = recognizer.recognitionTask(with: request) { result, error in
            DispatchQueue.main.async {
                guard !self.finished else { return }
                if let result = result {
                    self.lastText = result.bestTranscription.formattedString
                    self.silenceTimer?.invalidate()
                    if result.isFinal {
                        self.finish()
                        return
                    }
                    self.silenceTimer = Timer.scheduledTimer(withTimeInterval: 1.8, repeats: false) { _ in self.finish() }
                }
                if let error = error {
                    self.finish(error: self.lastText.isEmpty ? error.localizedDescription : nil)
                }
            }
        }
        do {
            engine.prepare()
            try engine.start()
            deadline = Timer.scheduledTimer(withTimeInterval: 15, repeats: false) { _ in self.finish() }
        } catch {
            finish(error: error.localizedDescription)
        }
    }

    func finish(error: String? = nil) {
        guard !finished else { return }
        finished = true
        silenceTimer?.invalidate()
        deadline?.invalidate()
        engine.stop()
        if installedTap { engine.inputNode.removeTap(onBus: 0) }
        request.endAudio()
        task?.cancel()
        if let error = error {
            errorOut(error)
            exit(1)
        }
        print(lastText)
        exit(0)
    }
}

let listener = Listener()
listener.start()
RunLoop.main.run()

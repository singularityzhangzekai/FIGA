from __future__ import annotations
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from .catalog import CATEGORIES, LABELS, format_observed_count


class Voice:
    def __init__(self, root, mode="scripted", speak=False):
        self.root = Path(root)
        self.mode = mode
        self.speak_enabled = speak or mode == "mic"
        self.locale = os.getenv("VOICE_LOCALE", "en-US")
        self.english = self.locale.lower().startswith("en")
        self.output_voice = os.getenv("MACOS_VOICE") or None
        self.voice_resolved = bool(self.output_voice)
        self.script = iter(["Add two unsweetened yogurts.", "That's all."] if self.english else ["再加两盒无糖酸奶", "就这些"])

    def message(self, chinese, english):
        return english if self.english else chinese

    def say(self, text):
        print("Assistant: " + text, flush=True)
        if not self.speak_enabled:
            return
        if sys.platform != "darwin" or not shutil.which("say"):
            raise RuntimeError("Speech output requires macOS say; omit --speak for development checks")
        args = ["/usr/bin/say"]
        if self.english and not self.voice_resolved:
            listed = subprocess.run(["/usr/bin/say", "-v", "?"], capture_output=True, text=True, check=True, timeout=15)
            matches = [match.group(1).strip() for line in listed.stdout.splitlines()
                       if (match := re.match(r"^(.+?)\s+en_US\s+#", line))]
            self.output_voice = next((name for name in matches if name.startswith("Samantha")), matches[0] if matches else None)
            self.voice_resolved = True
        if self.output_voice:
            args += ["-v", self.output_voice]
        subprocess.run(args + [text], check=True, timeout=90)

    def listen(self):
        if self.mode == "scripted":
            text = next(self.script, "That is all.")
            print("User (scripted replay): " + text, flush=True)
            return text
        if self.mode == "text":
            return input("Developer input (not the demo interface): ").strip()
        executable = build_voice(self.root)
        result = subprocess.run([str(executable), "--locale", self.locale],
                                capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise RuntimeError("Microphone recognition failed: " + result.stderr.strip())
        text = result.stdout.strip()
        print("User (microphone transcript): " + text, flush=True)
        return text


def describe_observation(frame, locale="en-US"):
    english = locale.lower().startswith("en")
    clear, uncertain, assumed = [], [], []
    for item in frame.get("items", []):
        category = item.get("category")
        if category not in CATEGORIES:
            continue
        quantity = item.get("quantity")
        if item.get("assumed_empty") is True:
            assumed.append(category.replace("_", " "))
        elif quantity is not None and item.get("clear") and item.get("confidence", 0) >= .8:
            clear.append(format_observed_count(category, quantity, locale))
        else:
            uncertain.append(category.replace("_", " ") if english else LABELS[category])
    is_photo = frame.get("source") == "deepseek image inference"
    if english:
        lead = "From this photo, I estimate " if is_photo else "In this demo inventory, there are "
        text = lead + ", ".join(clear) + "." if clear else "I could not reliably count the supported foods in this image."
        if frame.get("capacity_level") == "full":
            text = ("The visible area looks full. " if is_photo else "This is the fully stocked demo. ") + text
        if uncertain:
            text += " I could not confirm the amount of " + ", ".join(uncertain) + "."
        if assumed:
            text += " Under your photo policy, I am treating these unseen foods as empty: " + ", ".join(assumed) + "."
        return text
    text = ("根据照片，我估计有" if is_photo else "这份示例库存中有") + "、".join(clear) + "。" if clear else "这张图片中可支持的食物数量均无法可靠确认。"
    if uncertain:
        text += "无法确认" + "、".join(uncertain) + "的数量。"
    return text


def build_voice(root):
    if sys.platform != "darwin" or not shutil.which("swiftc"):
        raise RuntimeError("Microphone input requires macOS and Xcode Command Line Tools (swiftc)")
    root = Path(root)
    source = root / "native/VoiceListener.swift"
    contents = root / "runtime/GroceryVoice.app/Contents"
    executable = contents / "MacOS/GroceryVoice"
    if executable.exists() and executable.stat().st_mtime >= source.stat().st_mtime:
        verified = subprocess.run(["codesign", "--verify", str(contents.parent)], capture_output=True)
        if verified.returncode == 0:
            return executable
    executable.parent.mkdir(parents=True, exist_ok=True)
    info = {"CFBundleIdentifier": "local.invisiblegrocery.voice", "CFBundleName": "GroceryVoice",
            "CFBundleExecutable": "GroceryVoice", "CFBundlePackageType": "APPL", "LSUIElement": True,
            "NSMicrophoneUsageDescription": "Listen to your requests during the pre-checkout additions conversation.",
            "NSSpeechRecognitionUsageDescription": "Transcribe grocery additions for the household grocery assistant."}
    plist = contents / "Info.plist"
    plist.write_bytes(plistlib.dumps(info))
    cache = root / "runtime/swift-cache"
    subprocess.run(["swiftc", "-swift-version", "5", "-module-cache-path", str(cache),
                    "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT", "-Xlinker", "__info_plist", "-Xlinker", str(plist),
                    str(source), "-o", str(executable)], check=True, timeout=180)
    if shutil.which("xattr"):
        # Strip metadata only from our generated app, never from user source files.
        subprocess.run(["xattr", "-cr", str(contents.parent)], check=True, timeout=30)
    if shutil.which("codesign"):
        subprocess.run(["codesign", "--force", "--sign", "-", str(contents.parent)], check=True,
                       stdout=subprocess.DEVNULL, timeout=30)
    return executable

# Hackathon recording monitor

Local, read-only presentation of the existing grocery application. The family still interacts only by voice; this page is an observer for judges. No hosted service or frontend framework is required.

## Start the offline recording demo

Double-click `run-monitor.command` from Finder, or run from this directory:

```sh
python3 -m grocery.monitor --mode replay --voice scripted --speak --pace 1.1 --start-delay 10
```

The browser opens at `http://127.0.0.1:8765`. The workflow starts after ten seconds so you can begin recording. The offline workflow uses the application's actual inventory, memory, cart, purchase-list, order-signal and simulated-checkout functions. Its perception and user replies are explicitly synthetic. `--speak` reads the assistant's real output through the speakers; scripted user replies are displayed and labelled, not recorded microphone audio.

The three columns show:

1. Reference/analyzed refrigerator frame and inventory, including uncertainty and assumed-empty labels.
2. Actual backend operation start/completion/failure events, tool names, factual summaries and inspectable results.
3. Markdown preferences, the current cart version and the verified simulated order result.

The bottom strip follows voice turns. The browser has no purchase, edit, upload, or inventory controls. Inspecting an event only changes its local disclosure state. All HTTP mutation methods return 405; unrelated file paths are not served.

## Live DeepSeek with spoken additions

Use `run-monitor-live.command`, or:

```sh
python3 -m grocery.monitor --mode deepseek --voice mic --pace 0.6 --start-delay 10
```

This uses the existing local `.env` and the existing microphone/speech implementation. The agent really calls DeepSeek and its real tools. Camera observations still come from the labelled restock fixture; the UI identifies this mixed mode. For a first model-only rehearsal, replace `--voice mic` with `--voice scripted`.

To analyze the real photo already in this project:

```sh
python3 -m grocery.monitor --mode deepseek --voice mic --image data/my_fridge.jpg --unseen-policy unknown
```

The photo is then sent to DeepSeek, shown as an analyzed frame, and its actual results drive the workflow. With `unknown`, unseen or ambiguous foods remain unconfirmed; a full-looking image does not guarantee confirmed inventory. If no confirmed shortage is calculated, the monitor finishes without an order. It never invents a shortage to complete the recording story. The application's existing `empty` policy is also available and its assumptions remain labelled.

All Whole Foods checkout is still local simulation. Live model usage does not imply live retailer prices, payment or delivery.

## Recording tips

- Use a 1440×900 or 1920×1080 browser viewport for larger inventory and event panes; the entire monitor also fits 1280×720.
- Record the camera event, memory read, cart creation, spoken additions, final purchase list, order signal and simulated order result.
- Start one monitor process per port. Stop it with Control-C before starting another on 8765, or pass a different `--port`.
- `--pace` is an explicitly disclosed presentation delay per instrumented tool. It does not fabricate events or model reasoning; displayed execution milliseconds exclude this delay. Set `--pace 0` to show normal execution timing.
- Record a short audio test first, confirming the recorder captures both your voice and the assistant's speaker output. The monitor does not record or store audio itself.

## Isolation and event data

Each launch creates `runtime/monitor/<session>/` with its own database, preferences, JSONL events and purchase exports. It does not reset the existing demo or household state. The original application source remains untouched; instrumentation subclasses and callbacks are installed only inside the monitor process and restored after the run.

Only result summaries and tool metadata are shown. API keys, prompts and private reasoning are excluded. A callback from a function whose start was not instrumented is displayed as a completed result with no invented duration.

Stop the server when finished; the interface intentionally has no remote execution or restart API.

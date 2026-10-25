# Invisible Grocery Assistant

**A voice-first grocery assistant that turns refrigerator observations into a personalized restocking plan.**

Instead of maintaining a shopping list by hand, imagine an assistant that notices when essentials are running low, remembers your household's preferences, and asks whether you need anything else. Invisible Grocery Assistant explores that experience through a working hackathon prototype: image-based inventory, persistent memory, conversational cart updates, and a traceable simulated purchase workflow.

The household interacts through voice. A separate, read-only browser monitor lets developers and judges inspect what the application is doing.

> **Prototype status:** Checkout is a local simulation. This project does not place real Whole Foods orders, process payments, or arrange delivery. Product listings, prices, and seeded demo consumption history are synthetic.

## The experience

1. **Observe the fridge.** Read a refrigerator photo through the configured DeepSeek model, or replay a labelled inventory fixture for an offline demonstration.
2. **Identify restocking needs.** Compare inventory with estimated consumption, a two-day planning horizon, and incoming deliveries.
3. **Apply household memory.** Read explicit preferences from Markdown and use recorded purchase and consumption history to select products.
4. **Talk through the cart.** Hear the initial proposal, then add or remove products, change quantities, save a long-term preference, or cancel.
5. **Save the final list.** Read the complete cart aloud and export a Markdown purchase record.
6. **Request simulated checkout.** Validate the cart, emit a versioned order signal, and run the local checkout simulator.

For example, after hearing a restocking proposal, you can say:

- “Add two unsweetened yogurts.”
- “Remove milk and add two apples.”
- “Set apples to three.”
- “From now on, always buy regular milk.”
- “That's all.”
- “Cancel this order.”

One-time additions affect the current cart. Explicit long-term preferences update the household's Markdown memory.

## What is implemented

| Capability | Current implementation |
| --- | --- |
| Refrigerator perception | Photo input through the DeepSeek adapter, with structured quantities, visibility, and uncertainty; offline JSON fixtures are also supported |
| Inventory and planning | SQLite inventory, estimated consumption history, incoming deliveries, and restocking calculations |
| Household memory | Editable Markdown preferences, with explicit requests kept separate from inferred consumption summaries |
| Conversational agent | Tool calls for reading context, building and editing carts, remembering preferences, and handling unavailable products |
| Voice interaction | macOS speech recognition through a Swift helper and speech output through the system `say` command |
| Product catalog | 31 food categories and 32 synthetic product choices across dairy, produce, proteins, bread, drinks, and condiments |
| Purchase records | Final Markdown lists, versioned JSON order signals, and idempotent local simulated checkout |
| Demo monitor | Local browser view of inventory, backend events, memory, voice turns, cart state, and simulated order results |

## Quick start

### Requirements

- Python **3.10 or newer**.
- For microphone input and spoken output: **macOS**; microphone input also requires Xcode Command Line Tools (`swiftc`).
- For live model calls: a DeepSeek API key and access to a model compatible with the adapter's image, JSON-output, and tool-call requests.

The offline replay can run without an API key or third-party Python packages. Install the declared dependencies for normal setup:

```sh
git clone https://github.com/singularityzhangzekai/FIGA.git
cd FIGA
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

### Run the offline demo

```sh
python3 -m grocery doctor
python3 -m grocery demo --reset
```

The demo seeds synthetic household history, replays a low-stock observation, and uses scripted replies to add yogurt and finish the cart. It exports a purchase list and creates a simulated order. No model or microphone is used.

`--reset` clears only the demo database and demo preference copy under `runtime/`.

### Open the demo monitor

```sh
python3 -m grocery.monitor --mode replay --voice scripted --pace 1.1 --start-delay 10
```

The browser opens at `http://127.0.0.1:8765`. After a ten-second setup delay, the monitor shows the workflow as it runs. Add `--speak` on macOS to hear the assistant.

For the spoken recording demo on macOS, you can also double-click `run-monitor.command`. The monitor is an observer interface: it has no purchasing or inventory-editing controls. Each launch uses an isolated session directory.

See [MONITOR.md](MONITOR.md) for live demonstrations, recording guidance, and event details.

## Use a live model and microphone

Create a local configuration file if one does not already exist:

```sh
cp -n .env.example .env
```

Fill in your API key in `.env`:

```dotenv
DEEPSEEK_API_KEY=your_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
VOICE_LOCALE=en-US
MACOS_VOICE=
```

`deepseek-flash` is the adapter's configured default; choose a model available to your account with the required capabilities. The application calls the HTTPS API directly and does not require an OpenAI SDK. `.env` is excluded from Git.

Build the macOS voice helper, then start a live conversation:

```sh
python3 -m grocery voice-build
python3 -m grocery demo --reset --mode deepseek --voice mic
```

This uses live model calls and microphone input, while refrigerator observations still come from synthetic fixtures. macOS may request microphone and speech-recognition permissions. Speech recognition may use Apple's online service when on-device recognition is unavailable. The application does not save raw audio; recognized text is included in workflow records.

### Analyze a refrigerator photo

Replace the example image path with your own photo:

```sh
python3 -m grocery demo --reset --mode deepseek --voice mic \
  --image data/my_fridge.jpg --unseen-policy unknown --describe
```

The image is sent to the configured model endpoint, and its returned observations drive inventory updates. The demo still uses synthetic consumption history. `--describe` reads the estimated quantities and uncertainty aloud. If the planner finds no confirmed restocking need, it finishes without creating an order.

For the same photo-driven workflow with the browser monitor:

```sh
python3 -m grocery.monitor --mode deepseek --voice mic \
  --image data/my_fridge.jpg --unseen-policy unknown
```

### Watch an image directory

```sh
python3 -m grocery watch --mode deepseek --voice mic --frames-dir data/live_frames
```

This polls a directory for new images or structured JSON observations. A separate camera process can write frames there; direct camera hardware integration is not included. `watch` uses the household database and does not seed synthetic consumption history. Without recorded history, it does not invent restocking targets.

## Demonstration scenarios

| Scenario | Command |
| --- | --- |
| Standard restocking with scripted replies | `python3 -m grocery demo --reset` |
| Milk already arriving; avoid duplicate replenishment | `python3 -m grocery demo --reset --inbound-milk` |
| Fully stocked fridge; no restocking needed | `python3 -m grocery demo --reset --scenario full --describe` |
| Simulated away status; automatic simulated checkout | `python3 -m grocery demo --reset --presence away` |
| Real voice with offline rule-based interpretation | `python3 -m grocery demo --reset --mode replay --voice mic` |

Replay uses deterministic fixtures and limited rules. It demonstrates application behavior, not live image recognition or model understanding. Presence is supplied by a command-line flag rather than a physical sensor. By default, silence during a home-mode conversation holds the cart; `--silence-policy continue` enables a preset continuation policy.

## How it works

```text
Photo / structured observation
              |
      Inventory + history (SQLite)
              |
       Restocking calculation <--- Incoming deliveries
              |
        Tool-calling agent <------ Household preferences (Markdown)
              |
       Voice cart adjustments
              |
     Final purchase list (Markdown)
              |
       Validated order signal (JSON)
              |
        Local checkout simulator
```

Python owns inventory updates, planning, cart validation, and checkout state. The model interacts through declared tools. The monitor displays execution events and factual results rather than private model reasoning.

Cart versions and checkout idempotency keys help prevent stale or duplicate simulated orders. Cancellation, unfinished conversations, unresolved item requests, and failed validation prevent order-signal generation. Products outside the catalog must be explicitly skipped or the order remains on hold.

After an order signal is saved, the assistant says `ordered`. This acknowledges the simulated order request; it is not confirmation of a real retailer purchase. Prices and totals are recorded, but the prototype has no spending-limit enforcement.

## Data and project structure

```text
grocery/
  __main__.py             CLI and workflow controller
  providers.py            DeepSeek HTTP and photo-input adapter
  core.py                 Inventory, history, planning, carts, and checkout
  agent.py                Tool-calling loop and offline interaction rules
  catalog.py              Shared categories, units, products, and demo prices
  voice.py                Speech input/output integration
  purchase.py             Markdown lists and JSON order signals
  monitor.py              Local read-only monitor server
native/VoiceListener.swift macOS speech-recognition helper
monitor_web/              Monitor HTML, CSS, and JavaScript
data/                     Demo fixtures, preferences, and example photo
tests/                    Automated tests
scripts/test_live.py       Optional live-model interaction check
```

Household and runtime data locations (the `runtime/` directory is generated locally and excluded from Git):

- `runtime/demo.sqlite` and `runtime/demo_preferences.md`: demo state and editable demo memory.
- `runtime/household.sqlite`: separate state for `run`, `watch`, and `status`.
- `data/household_preferences.md`: household preference template used outside demo mode.
- `runtime/purchase_list.md`: latest exported purchase list.
- `runtime/purchase_lists/`: versioned purchase records.
- `runtime/order_signals/`: simulated order-request events.
- `runtime/events.jsonl`: backend workflow log.
- `runtime/monitor/<session>/`: isolated monitor-session state and exports.

## Current limitations

- **Visual counts are estimates.** The prototype cannot measure liquid remaining in opaque packaging, infer hidden contents, or determine freshness and expiry dates. Fridge fullness is a qualitative visible-area estimate.
- **Unseen does not mean empty.** The default photo policy, `--unseen-policy empty`, treats unseen supported categories as assumed empty and labels them accordingly. Use `--unseen-policy unknown` to leave them unconfirmed. Visibly ambiguous items remain uncertain.
- **History needs evidence.** Decreases in observed inventory are consumption estimates, not proof that food was eaten. Demo history is synthetic, and household learning has not been validated with real usage data.
- **Retail integration is unfinished.** There is no live product catalog, retailer authentication, payment, or delivery integration.
- **Deployment is local.** The prototype runs as a single backend process and does not provide production household-account isolation or multi-process preference-write coordination.

## Validation

Run the automated tests from the repository root:

```sh
python3 -m unittest discover -s tests -v
```

An optional live-model check exercises a spoken-request transcript without microphone input or a real purchase:

```sh
python3 scripts/test_live.py \
  --request "Please add two apples" \
  --expect-sku apples_each --expect-quantity 2
```

The live check requires a configured API key. See [MONITOR_VALIDATION.md](MONITOR_VALIDATION.md) for the recorded monitor validation notes.

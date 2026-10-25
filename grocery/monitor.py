"""Read-only recording monitor. Reuses the real application in an isolated session.

No HTTP endpoint can change inventory, preferences, carts, or orders. The worker
instruments existing functions inside this process; no application source is patched.
"""
from __future__ import annotations
import argparse
import copy
import json
import mimetypes
import os
import shutil
import threading
import time
import uuid
import webbrowser
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from . import __main__ as app
from .agent import Agent
from .core import Grocery, iso, now
from .providers import DeepSeek, load_env
from .voice import Voice, build_voice

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "monitor_web"
TITLES = {
    "observe_fridge": "Observe the fridge", "update_inventory": "Reconcile inventory",
    "seed_history": "Load consumption history", "get_household_context": "Read household memory",
    "build_cart": "Prepare a personal restock", "edit_cart": "Update the grocery cart",
    "remember_preference": "Save a lasting preference", "finish_additions": "Finish voice additions",
    "cancel_cart": "Cancel this purchase", "checkout": "Submit simulated order",
    "llm_request": "Consult DeepSeek", "speak": "Speak to the household", "listen": "Listen for additions",
    "report_unavailable_item": "Check an unavailable item", "skip_unavailable_item": "Resolve the request",
    "initial_restock_proposal": "Publish the initial restock", "final_purchase_list": "Finalize the purchase list",
    "save_purchase_list": "Save the complete purchase list", "order_signal": "Generate the order signal",
}


def safe(value):
    """Never ship credentials, prompts, or private reasoning to the browser."""
    blocked = {"api_key", "key", "authorization", "reasoning_content", "messages", "system_prompt", "chain_of_thought"}
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items() if str(k).lower() not in blocked}
    if isinstance(value, (list, tuple)):
        return [safe(v) for v in value]
    if isinstance(value, Path):
        return value.name
    if isinstance(value, str):
        return value[:14000]
    return value


def summary(name, result):
    if name == "seed_history":
        return "Loaded labelled synthetic consumption records and refreshed the inferred Markdown summary."
    if name == "get_household_context":
        c = result.get("context", {})
        return f"Read Markdown preferences, {len(c.get('inventory', []))} inventory categories and the consumption evidence window."
    if name in ("build_cart", "edit_cart", "initial_restock_proposal", "final_purchase_list"):
        return f"{len(result.get('lines', []))} product lines · ${result.get('total_cents', 0)/100:.2f} · cart v{result.get('version', 1)}."
    if name == "observe_fridge":
        return f"Received {len(result.get('items', []))} category observations. Source: {result.get('source', 'model response')}."
    if name == "update_inventory":
        return f"Updated {len(result.get('updated', []))} categories; {len(result.get('uncertain', []))} remain uncertain."
    if name == "checkout":
        return f"{result.get('order_id', '')} · ${result.get('total_cents', 0)/100:.2f}. Local simulation; no payment or delivery."
    if name == "llm_request":
        tools = [c.get("function", {}).get("name", "unknown") for c in result.get("tool_calls", [])]
        return "Requested tools: " + ", ".join(tools) + "." if tools else "Returned a final response to the application."
    if name == "remember_preference":
        return "Saved an explicit long-term instruction to Markdown; temporary additions remain separate."
    if name == "finish_additions":
        return "The household has finished adding items. The workflow may continue automatically."
    return "Completed using the application's real result."


class EventBus:
    def __init__(self, folder, mode, voice_mode, presence, image=None, pace=0):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.pace = pace
        self.image = Path(image).resolve() if image else None
        self.state = {
            "run_id": self.folder.name, "status": "waiting", "started_at": iso(),
            "mode": mode, "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash") if mode == "deepseek" else "Offline replay",
            "perception": "live_image" if image and mode == "deepseek" else "synthetic_replay",
            "history_source": "synthetic demo history", "prices_source": "synthetic catalog",
            "presence": presence, "presence_simulated": True, "voice_mode": voice_mode,
            "checkout_simulated": True, "pace_seconds": pace, "steps": [], "dialogue": [],
            "context": {}, "cart": None, "order": None, "frame": None, "error": None,
        }
        self.reference = self.image or (ROOT / "data/my_fridge.jpg")

    def _log(self, event):
        with (self.folder / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(safe(event), ensure_ascii=False) + "\n")

    def update(self, **changes):
        with self.lock:
            self.state.update(safe(changes))
            self._log({"type": "state", "at": iso(), **changes})

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def report(self, name, result):
        # A callback emitted after an existing function returns has no observable
        # start time. Report it as completed, never fabricate a running span.
        step = {"id": uuid.uuid4().hex[:10], "name": name, "title": TITLES.get(name, name.replace('_', ' ').capitalize()),
                "status": "completed", "started_at": iso(), "completed_at": iso(), "execution_ms": None,
                "summary": summary(name, result), "input": {}, "result": safe(result),
                "source": "application result callback; start not instrumented", "presentation_delay_ms": 0}
        with self.lock:
            self.state["steps"].append(step)
            self._log({"type": "step_reported", **step})

    def call(self, name, fn, refresh=None, args=None):
        step = {"id": uuid.uuid4().hex[:10], "name": name, "title": TITLES.get(name, name.replace('_', ' ').capitalize()),
                "status": "running", "started_at": iso(), "summary": "Waiting for the application result.",
                "input": safe(args or {}), "presentation_delay_ms": round(self.pace*1000)}
        with self.lock:
            self.state["steps"].append(step)
            self._log({"type": "step_started", **step})
        if self.pace:
            time.sleep(self.pace)
        started = time.monotonic()
        try:
            result = fn()
            details = result
            if name == "llm_request":
                details = {"requested_tools": [c.get("function", {}).get("name") for c in result.get("tool_calls", [])],
                           "has_final_response": bool(result.get("content"))}
            if name == "speak":
                details = {"text": args.get("text", "")}
            if name == "listen":
                details = {"transcript": result, "source": self.state["voice_mode"]}
            with self.lock:
                step.update(status="completed", completed_at=iso(), execution_ms=round((time.monotonic()-started)*1000),
                            result=safe(details), summary=summary(name, result) if isinstance(result, dict) or name == "seed_history" else "Completed.")
                if name in ("speak", "listen"):
                    text = args["text"] if name == "speak" else result
                    step["summary"] = text or "No speech detected."
                    self.state["dialogue"].append({"role": "assistant" if name == "speak" else "user", "text": text,
                        "source": "scripted reply" if name == "listen" and self.state["voice_mode"] == "scripted" else self.state["voice_mode"], "at": iso()})
                if name == "observe_fridge":
                    self.state["frame"] = safe(result)
                if name == "checkout":
                    self.state["order"] = safe(result)
                self._log({"type": "step_completed", **step})
            if refresh:
                refresh()
            return result
        except Exception as exc:
            with self.lock:
                step.update(status="failed", completed_at=iso(), execution_ms=round((time.monotonic()-started)*1000), summary=str(exc), result={"error": str(exc)})
                self._log({"type": "step_failed", **step})
            raise


def make_handler(bus):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, body, mime="application/json; charset=utf-8", status=200):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode()
            if isinstance(body, str):
                body = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api/state":
                self.send(bus.snapshot())
            elif path == "/api/events":
                self.send(bus.snapshot()["steps"])
            elif path == "/api/frame":
                if bus.reference.is_file():
                    self.send(bus.reference.read_bytes(), mimetypes.guess_type(bus.reference.name)[0] or "image/jpeg")
                else:
                    self.send({"error": "No reference frame supplied"}, status=404)
            elif path in ("/", "/index.html", "/monitor.css", "/monitor.js"):
                file = WEB / ("index.html" if path in ("/", "/index.html") else path[1:])
                self.send(file.read_bytes(), mimetypes.guess_type(file.name)[0] or "text/plain")
            else:
                self.send({"error": "Not found"}, status=404)

        def do_POST(self):
            self.send({"error": "This monitor is read-only"}, status=405)
        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST
    return Handler


def worker(bus, options):
    g = None
    original_agent, original_trace, original_root = app.Agent, app.trace, app.ROOT
    try:
        time.sleep(options.start_delay)
        bus.update(status="running")
        prefs = bus.folder / "preferences.md"
        shutil.copyfile(ROOT / "data/demo_preferences.md", prefs)
        g = Grocery(bus.folder / "state.sqlite", prefs)
        active_cart = [None]

        def refresh():
            state = {"context": g.context()}
            if active_cart[0]:
                state["cart"] = g.cart(active_cart[0])
            bus.update(**state)

        class MonitoredAgent(Agent):
            def _call(self, name, args, stage):
                def execute():
                    result = super(MonitoredAgent, self)._call(name, args, stage)
                    active_cart[0] = self.active_cart
                    return result
                return bus.call(name, execute, refresh, args)

        class MonitoredDeepSeek(DeepSeek):
            def chat(self, messages, tools=None, **kwargs):
                return bus.call("llm_request", lambda: super(MonitoredDeepSeek, self).chat(messages, tools=tools, **kwargs),
                    args={"model": self.model, "available_tools": [t["function"]["name"] for t in (tools or [])],
                          "image_request": any(isinstance(m.get("content"), list) for m in messages)})

        class MonitoredVoice(Voice):
            def say(self, text):
                return bus.call("speak", lambda: super(MonitoredVoice, self).say(text), args={"text": text})
            def listen(self):
                return bus.call("listen", lambda: super(MonitoredVoice, self).listen())

        app.Agent = MonitoredAgent
        app.ROOT = bus.folder  # Isolate purchase-list and order-signal exports too.
        # Agent._call emits results through this callback; the wrapper above owns
        # the paired started/completed events, avoiding duplicate timeline entries.
        instrumented = {"observe_fridge", "checkout", "tool_error"}
        instrumented.update(t["function"]["name"] for stage in ("prepare", "interact") for t in Agent(g).tools(stage))
        def application_trace(name, result):
            print(f"[monitor] {name}", flush=True)
            if name not in instrumented:
                bus.report(name, result)
        app.trace = application_trace
        bus.call("seed_history", g.seed_history, refresh)
        if options.image is None and options.scenario == "restock":
            baseline = app.observation(ROOT / "data/frames/full.json", None, iso(now()-timedelta(minutes=5)))
            g.record(baseline)
            refresh()
        if options.inbound_milk:
            with g.db:
                g.db.execute("INSERT INTO inbound VALUES (?,?,?,?,?)", ("monitor-milk", "milk", 1, iso(now()+timedelta(hours=12)), "simulated"))
        provider = MonitoredDeepSeek() if options.mode == "deepseek" else None
        voice = MonitoredVoice(ROOT, options.voice, options.speak)
        if options.voice == "mic":
            build_voice(ROOT)
        path = options.image or ROOT / "data/frames" / ("full.json" if options.scenario == "full" else "low.json")
        frame = bus.call("observe_fridge", lambda: app.observation(path, provider, unseen_policy=options.unseen_policy), args={
            "input": path.name, "source": "DeepSeek image analysis" if options.image else "synthetic perception replay"})
        record = g.record
        g.record = lambda f: bus.call("update_inventory", lambda: record(f), refresh)
        checkout = g.checkout
        g.checkout = lambda *a, **kw: bus.call("checkout", lambda: checkout(*a, **kw), refresh,
                                            {"cart_id": a[0], "expected_version": a[1], "simulated": True})
        order = app.cycle(g, provider, frame, options, voice)
        refresh()
        current_cart = bus.snapshot().get("cart")
        outcome = "completed" if order else "cancelled" if current_cart and current_cart.get("status") == "cancelled" else "held" if current_cart else "no_restock"
        bus.update(status=outcome, completed_at=iso())
    except Exception as exc:
        bus.update(status="failed", error=str(exc), completed_at=iso())
        print(f"[monitor error] {exc}", flush=True)
    finally:
        app.Agent, app.trace, app.ROOT = original_agent, original_trace, original_root
        if g is not None:
            g.close()


def main():
    p = argparse.ArgumentParser(description="Local, read-only screen-recording monitor for the actual grocery workflow")
    p.add_argument("--mode", choices=["replay", "deepseek"], default="replay")
    p.add_argument("--voice", choices=["scripted", "mic", "text"], default="scripted")
    p.add_argument("--speak", action="store_true")
    p.add_argument("--image", type=Path)
    p.add_argument("--presence", choices=["home", "away"], default="home")
    p.add_argument("--scenario", choices=["restock", "full"], default="restock")
    p.add_argument("--unseen-policy", choices=["empty", "unknown"], default="empty")
    p.add_argument("--silence-policy", choices=["hold", "continue"], default="hold")
    p.add_argument("--inbound-milk", action="store_true")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--pace", type=float, default=1.1, help="Disclosed presentation delay per tool; does not fabricate events")
    p.add_argument("--start-delay", type=float, default=6)
    p.add_argument("--no-open", action="store_true")
    options = p.parse_args()
    if options.pace < 0 or options.start_delay < 0:
        p.error("Delays must be nonnegative")
    if options.image and options.mode != "deepseek":
        p.error("A real image requires --mode deepseek")
    if options.image:
        options.image = options.image.resolve()
        if not options.image.is_file():
            p.error("Image does not exist")
    load_env(ROOT / ".env")
    session = ROOT / "runtime/monitor" / (now().astimezone(ZoneInfo("America/Los_Angeles")).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    bus = EventBus(session, options.mode, options.voice, options.presence, options.image, options.pace)
    server = ThreadingHTTPServer(("127.0.0.1", options.port), make_handler(bus))
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Monitor: {url}\nSession: {session}\nRead-only HTTP; orders, prices, presence and history are simulated.\n", flush=True)
    threading.Thread(target=worker, args=(bus, options), daemon=True).start()
    if not options.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

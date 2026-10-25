from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from .agent import Agent
from .core import Grocery, CATALOG, cart_summary, iso, now
from .providers import DeepSeek, load_env
from .purchase import save_purchase_list, emit_order_signal
from .voice import Voice, build_voice, describe_observation

ROOT = Path(__file__).resolve().parents[1]


def arguments():
    parser = argparse.ArgumentParser(description="Voice-only fridge grocery assistant. All checkout operations are labelled simulations.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("demo", "run", "watch"):
        p = sub.add_parser(name)
        p.add_argument("--mode", choices=["replay", "deepseek"], default="replay" if name == "demo" else "deepseek")
        p.add_argument("--voice", choices=["scripted", "mic", "text"], default="scripted" if name == "demo" else "mic")
        p.add_argument("--speak", action="store_true")
        p.add_argument("--presence", choices=["home", "away"], default="home", help="Simulated presence sensor")
        p.add_argument("--silence-policy", choices=["hold", "continue"], default="hold", help="Preset silence policy for simulated checkout")
        p.add_argument("--image", type=Path, help="Real photo; requires --mode deepseek")
        p.add_argument("--unseen-policy", choices=["empty", "unknown"], default="empty",
                       help="Treat supported foods not seen in a photo as empty (default) or unknown")
        if name == "demo":
            p.add_argument("--describe", action="store_true", help="Read the photo recognition result aloud for testing")
            p.add_argument("--scenario", choices=["restock", "full"], default="restock", help="Synthetic scene: usual restock or fully stocked fridge")
            p.add_argument("--reset", action="store_true", help="Reset only the synthetic demo database and preferences in runtime")
            p.add_argument("--inbound-milk", action="store_true", help="Add one synthetic milk delivery arriving tomorrow")
        if name == "run":
            p.add_argument("--frame", type=Path, help="Labelled structured perception replay JSON")
        if name == "watch":
            p.add_argument("--frames-dir", type=Path, default=ROOT/"data/live_frames")
            p.add_argument("--interval", type=float, default=3)
            p.add_argument("--once", action="store_true")
    sub.add_parser("doctor")
    sub.add_parser("voice-build")
    sub.add_parser("status")
    return parser.parse_args()


def trace(name, result):
    runtime = ROOT / "runtime"
    runtime.mkdir(exist_ok=True)
    event = {"at": iso(), "tool": name, "result": result}
    with (runtime/"events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
    if name == "tool_error":
        print(f"[tool_error] {result.get('tool_name', 'unknown')}: {result.get('error', 'unknown error')}", flush=True)
    else:
        print(f"[background] {name}", flush=True)


def observation(path, provider, captured_at=None, unseen_policy="empty"):
    if path.suffix.lower() == ".json":
        frame = json.loads(path.read_text(encoding="utf-8"))
        frame.setdefault("captured_at", captured_at or iso())
        print("[perception replay] " + frame.get("source", "structured simulated observation"), flush=True)
        return frame
    if not provider:
        raise ValueError("Real photos require --mode deepseek; replay accepts only labelled JSON observations")
    frame = provider.observe(path, unseen_policy=unseen_policy)
    frame.update({"frame_id": hashlib.sha256(path.read_bytes()).hexdigest() + "-unseen-" + unseen_policy, "captured_at": captured_at or iso(),
                  "source": "deepseek image inference", "image_path": str(path.resolve())})
    print(f"[perception] Unseen food policy: {unseen_policy}; "
          f"{sum(item.get('assumed_empty') is True for item in frame['items'])} categories assumed empty.", flush=True)
    return frame


def cycle(g, provider, frame, args, voice):
    result = g.record(frame)
    trace("observe_fridge", {"frame_id": frame["frame_id"], **result})
    if result["duplicate"]:
        return None
    if getattr(args, "describe", False):
        voice.say(describe_observation(frame, voice.locale))
    needs = g.plan()
    if not needs:
        if getattr(args, "describe", False):
            voice.say("Based on the demo consumption history, these items have no confirmed restock need. No order will be placed.")
        print("[background] No confirmed restock need: stock may be sufficient, uncertain, or missing consumption history.", flush=True)
        return None
    print("[decision] Restock needs: " + "; ".join(
        f"{need['category'].replace('_', ' ')}: {need['on_hand']:g} on hand, "
        f"{need['inbound_units']:g} incoming, target {need['target_units']:g}" +
        (" (assumed empty from photo)" if need['assumed_empty'] else "")
        for need in needs), flush=True)
    agent = Agent(g, provider, trace)
    agent.run("prepare")
    if not agent.active_cart:
        return None
    cart = g.cart(agent.active_cart)
    initial_cart = cart
    trace("initial_restock_proposal", initial_cart)
    updates = []
    completion = "Automatic policy: household is away"
    if args.presence == "home":
        voice.say(f"My initial restock suggestion is {cart_summary(cart, voice.locale)}. "
                  f"The estimated total is {cart['total_cents']/100:.2f} dollars.")
        voice.say("Would you like to add anything? You can also remove items or change quantities. Say that's all when you are finished.")
        for _ in range(4):
            utterance = voice.listen()
            if not utterance:
                if args.silence_policy == "continue":
                    agent.finished = True
                    completion = "Preset silence policy: continue"
                else:
                    voice.say("I have saved the cart for later.")
                    return None
            else:
                updates.append(utterance)
                agent.run("interact", utterance)
                if agent.finished:
                    completion = ("Scripted demo response finished additions and adjustments" if voice.mode == "scripted" else
                                  "User explicitly finished additions and adjustments")
            if agent.cancelled:
                voice.say("I have cancelled this order.")
                return None
            if agent.pending_issue:
                if agent.issue_kind == "unavailable":
                    voice.say(f"I could not add {agent.pending_issue} because it is not in the demo catalog. Would you like to skip it or cancel this order?")
                else:
                    voice.say("I could not complete that cart change, so I have not submitted the order. Please repeat the request, or say skip it.")
                continue
            if agent.finished:
                break
            updated = g.cart(agent.active_cart)
            summary = cart_summary(updated, voice.locale) if updated["lines"] else "no items"
            voice.say(f"Your cart now contains {summary}. Any other additions or changes?")
        if not agent.finished:
            voice.say("I have saved the cart while you finish adding items.")
            return None
    cart = g.cart(agent.active_cart)
    trace("final_purchase_list", cart)
    summary = cart_summary(cart, voice.locale) if cart["lines"] else "no items"
    voice.say(f"Your complete purchase list is {summary}. The estimated total is {cart['total_cents']/100:.2f} dollars.")
    blocked_reason = None
    try:
        g.validate_checkout(cart["id"], cart["version"])
    except ValueError as exc:
        blocked_reason = str(exc)
    purchase_list = save_purchase_list(ROOT, cart, initial_cart, updates, completion, blocked_reason)
    trace("save_purchase_list", {"path": str(purchase_list), "cart_id": cart["id"], "cart_version": cart["version"]})
    print(f"[purchase list] {purchase_list}", flush=True)
    if blocked_reason:
        voice.say(f"The complete list is saved, but checkout is on hold: {blocked_reason}. No order signal was generated.")
        return None
    signal_path, signal = emit_order_signal(ROOT, g, cart, purchase_list)
    trace("order_signal", {"path": str(signal_path), **signal})
    print(f"[order signal] {signal_path}", flush=True)
    voice.say("ordered")
    order = g.checkout(signal["cart_id"], signal["cart_version"], signal["idempotency_key"])
    trace("checkout", order)
    return order


def main():
    args = arguments()
    load_env(ROOT/".env")
    runtime = ROOT/"runtime"
    runtime.mkdir(exist_ok=True)
    if args.command == "doctor":
        print(json.dumps({"project": str(ROOT), "python": sys.version.split()[0],
                          "deepseek_key_configured": bool(os.getenv("DEEPSEEK_API_KEY")),
                          "deepseek_model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
                          "macos_speech": sys.platform == "darwin", "swiftc": bool(shutil.which("swiftc")),
                          "checkout": "simulation only; no Whole Foods integration"}, ensure_ascii=False, indent=2))
        return
    if args.command == "voice-build":
        print(build_voice(ROOT))
        return
    is_demo = args.command == "demo"
    db = runtime / ("demo.sqlite" if is_demo else "household.sqlite")
    prefs = runtime/"demo_preferences.md" if is_demo else ROOT/"data/household_preferences.md"
    if is_demo and args.reset:
        if db.exists():
            db.unlink()
        if prefs.exists():
            prefs.unlink()
    if is_demo and not prefs.exists():
        shutil.copyfile(ROOT/"data/demo_preferences.md", prefs)
    g = Grocery(db, prefs)
    try:
        if args.command == "status":
            print(json.dumps(g.context(), ensure_ascii=False, indent=2))
            return
        provider = DeepSeek() if args.mode == "deepseek" else None
        voice = Voice(ROOT, args.voice, args.speak)
        print(f"[mode] {'Live DeepSeek model' if provider else 'Deterministic offline replay (no LLM call)'}; "
              f"simulated presence={args.presence}; checkout=simulated Whole Foods order", flush=True)
        if args.voice == "mic":
            # Build first so compilation does not interrupt the spoken conversation.
            build_voice(ROOT)
        if is_demo:
            print("[data] Synthetic household history, demo products and prices; not a real household or live Whole Foods catalog.", flush=True)
            g.seed_history()
            if not args.image and args.scenario != "full":
                g.record(observation(ROOT/"data/frames/full.json", None, iso(now()-timedelta(minutes=5))))
            if args.inbound_milk:
                with g.db:
                    g.db.execute("INSERT OR IGNORE INTO inbound VALUES (?,?,?,?,?)",
                                 ("demo-external-milk", "milk", 1, iso(now()+timedelta(hours=12)), "simulated"))
            frame_path = args.image or ROOT/"data/frames"/("full.json" if args.scenario == "full" else "low.json")
            order = cycle(g, provider, observation(frame_path, provider, unseen_policy=getattr(args, "unseen_policy", "empty")), args, voice)
            if order:
                print(json.dumps(order, ensure_ascii=False, indent=2))
            return
        if args.command == "run":
            if not args.image and not args.frame:
                raise ValueError("run requires --image or --frame")
            cycle(g, provider, observation(args.image or args.frame, provider, unseen_policy=args.unseen_policy), args, voice)
            return
        args.frames_dir.mkdir(parents=True, exist_ok=True)
        if args.interval < .1:
            raise ValueError("interval must be at least 0.1 seconds")
        seen = set()
        print(f"[background] Waiting for camera files: {args.frames_dir}", flush=True)
        while True:
            for path in sorted(args.frames_dir.iterdir(), key=lambda p: p.stat().st_mtime):
                if path.suffix.lower() not in (".json", ".jpg", ".jpeg", ".png", ".webp"):
                    continue
                key = (str(path), path.stat().st_mtime_ns, path.stat().st_size)
                if key in seen:
                    continue
                try:
                    cycle(g, provider, observation(path, provider, unseen_policy=args.unseen_policy), args, voice)
                    seen.add(key)
                except (ValueError, RuntimeError) as exc:
                    print(f"[background error] {path.name}: {exc}", file=sys.stderr)
            if args.once:
                break
            time.sleep(args.interval)
    finally:
        g.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print("Error: " + str(exc), file=sys.stderr)
        sys.exit(1)

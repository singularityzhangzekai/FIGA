"""Isolated live DeepSeek smoke test; no microphone, real purchase, or persistent state changes."""
import argparse
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from grocery.providers import load_env, DeepSeek
from grocery.core import Grocery
from grocery.voice import Voice
from grocery.__main__ import observation, cycle


def main():
    parser = argparse.ArgumentParser(description="Isolated live grocery smoke test")
    parser.add_argument("--image", type=Path, help="Use a real fridge photo")
    parser.add_argument("--unseen-policy", choices=["empty", "unknown"], default="empty")
    parser.add_argument("--inspect-only", action="store_true", help="Validate image recognition and report restock needs without ordering")
    parser.add_argument("--request", default="Add two unsweetened yogurts.", help="Scripted English add-on request")
    parser.add_argument("--follow-up", action="append", default=[], help="Additional scripted request before finishing; repeat for more turns")
    parser.add_argument("--output-dir", type=Path, help="Keep purchase artifacts under this directory; otherwise use the temporary test directory")
    parser.add_argument("--expect-sku", default="yogurt_unsweetened")
    parser.add_argument("--expect-quantity", type=int, default=2)
    parser.add_argument("--expect-absent-sku", action="append", default=[], help="Assert a removed SKU is absent from the final order")
    args_cli = parser.parse_args()
    load_env(ROOT / ".env")
    provider = DeepSeek()
    if urlparse(provider.base).hostname != "api.deepseek.com":
        raise RuntimeError("This smoke test only sends the key to api.deepseek.com")
    with tempfile.TemporaryDirectory(prefix="grocery-live-test-") as directory:
        directory = Path(directory)
        preferences = directory / "preferences.md"
        source = ROOT / "runtime/demo_preferences.md"
        if not source.exists():
            source = ROOT / "data/demo_preferences.md"
        preferences.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        grocery = Grocery(directory / "test.sqlite", preferences)
        try:
            grocery.seed_history()
            print("Live DeepSeek test: " + ("real image input" if args_cli.image else "synthetic camera replay") + ", scripted English responses, simulated checkout.", flush=True)
            voice = Voice(ROOT, mode="scripted", speak=False)
            voice.script = iter([args_cli.request, *args_cli.follow_up, "That's all."])
            args = SimpleNamespace(presence="home", silence_policy="hold")
            frame = observation(args_cli.image or ROOT / "data/frames/low.json", provider if args_cli.image else None,
                                unseen_policy=args_cli.unseen_policy)
            if args_cli.inspect_only:
                recorded = grocery.record(frame)
                print("IMAGE_OBSERVATION", json.dumps(frame, ensure_ascii=False), flush=True)
                print("RESTOCK_NEEDS_WITH_DEMO_HISTORY", json.dumps(grocery.plan(), ensure_ascii=False), flush=True)
                print("IMAGE_TEST_PASSED", json.dumps({"updated": recorded["updated"], "uncertain": recorded["uncertain"], "order_placed": False}), flush=True)
                (ROOT / "runtime").mkdir(exist_ok=True)
                (ROOT / "runtime/last_image_observation.json").write_text(json.dumps(frame, ensure_ascii=False, indent=2), encoding="utf-8")
                return
            def trace_tool(name, result):
                print("[tool]", name, json.dumps(result, ensure_ascii=False) if name == "tool_error" else "", flush=True)
            with patch("grocery.__main__.trace", trace_tool), patch("grocery.__main__.ROOT", args_cli.output_dir or directory):
                order = cycle(grocery, provider, frame, args, voice)
            if not order or not order.get("simulated"):
                raise RuntimeError("The test did not produce a simulated order")
            quantity = sum(line["packs"] for line in order["lines"] if line["sku"] == args_cli.expect_sku)
            if quantity != args_cli.expect_quantity:
                raise RuntimeError(f"The English request was not applied correctly: expected {args_cli.expect_quantity} of {args_cli.expect_sku}, got {quantity}")
            for sku in args_cli.expect_absent_sku:
                if any(line["sku"] == sku for line in order["lines"]):
                    raise RuntimeError(f"The removal request was not applied: {sku} remains in the final order")
            print("LIVE_TEST_PASSED", json.dumps({"status": order["status"], "total_cents": order["total_cents"]}), flush=True)
        finally:
            grocery.close()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as error:
        print("LIVE_TEST_FAILED:", str(error), file=sys.stderr)
        sys.exit(1)

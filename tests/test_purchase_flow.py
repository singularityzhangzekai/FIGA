import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from grocery.core import Grocery, iso
from grocery.agent import Agent
from grocery.purchase import save_purchase_list, emit_order_signal
from grocery.voice import Voice
import grocery.__main__ as app

ROOT = Path(__file__).resolve().parents[1]


class PurchaseFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        prefs = self.root / "preferences.md"
        prefs.write_text((ROOT / "data/demo_preferences.md").read_text())
        self.g = Grocery(self.root / "test.sqlite", prefs)
        self.g.seed_history()
        self.frame = {"frame_id": "purchase-flow", "captured_at": iso(), "items": [
            {"category": "eggs", "quantity": 2, "confidence": .99, "clear": True},
            {"category": "milk", "quantity": 0, "confidence": .99, "clear": True}]}
        self.voice = Voice(self.root, mode="scripted", speak=False)
        self.args = SimpleNamespace(presence="home", silence_policy="hold")
        self.events = []

    def tearDown(self):
        self.g.close()
        self.tmp.cleanup()

    def run_flow(self, replies):
        self.voice.script = iter(replies)
        self.output = io.StringIO()
        def trace(name, result):
            self.events.append((name, result))
            if name == "order_signal":
                # The list is already on disk and was spoken before publishing the event.
                self.assertTrue(Path(result["purchase_list_path"]).exists())
                self.assertIn("Your complete purchase list is", self.output.getvalue())
                self.assertEqual(self.g.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)
        with patch.object(app, "ROOT", self.root), patch.object(app, "trace", trace), redirect_stdout(self.output):
            return app.cycle(self.g, None, self.frame, self.args, self.voice)

    def signals(self):
        return list((self.root / "runtime/order_signals").glob("*.json"))

    def test_add_remove_adjust_then_read_save_signal_and_simulated_checkout(self):
        order = self.run_flow(["Remove milk and add two apples", "Set apples to three", "That's all"])
        self.assertEqual({line["sku"]: line["packs"] for line in order["lines"]}, {"eggs_6": 1, "apples_each": 3})
        self.assertEqual(order["total_cents"], 766)
        names = [name for name, _ in self.events]
        self.assertLess(names.index("finish_additions"), names.index("save_purchase_list"))
        self.assertLess(names.index("save_purchase_list"), names.index("order_signal"))
        self.assertLess(names.index("order_signal"), names.index("checkout"))
        signal = json.loads(self.signals()[0].read_text())
        self.assertEqual(signal["lines"], order["lines"])
        self.assertEqual(signal["cart_version"], order["cart_version"])
        self.assertEqual(signal["total_cents"], 766)
        self.assertTrue(signal["simulated"])
        document = Path(signal["purchase_list_path"]).read_text()
        self.assertEqual(hashlib.sha256(document.encode()).hexdigest(), signal["purchase_list_sha256"])
        self.assertEqual(document, (self.root / "runtime/purchase_list.md").read_text())
        final = document.split("## Final purchase list")[1]
        self.assertIn("apples_each | 3", final)
        self.assertIn("$7.66 USD", final)
        self.assertNotIn("oat_unsweetened", final)
        self.assertIn("Remove milk and add two apples", document)
        speech = self.output.getvalue()
        self.assertLess(speech.index("My initial restock suggestion"), speech.index("Would you like to add anything?"))
        self.assertIn("complete purchase list is 1 pack of six-count eggs, 3 apples", speech)

    def test_dont_buy_one_food_removes_it_without_cancelling_the_cart(self):
        order = self.run_flow(["Don't buy milk", "That's all"])
        self.assertEqual(order["total_cents"], 499)
        self.assertEqual(len(self.signals()), 1)
        self.assertNotIn("above your", self.output.getvalue())

    def test_large_final_list_is_saved_and_signalled_without_a_budget_check(self):
        order = self.run_flow(["Add five packs of fish", "That's all"])
        self.assertIsNotNone(order)
        self.assertEqual(len(self.signals()), 1)
        document = (self.root / "runtime/purchase_list.md").read_text()
        self.assertIn("Status: Ready for simulated checkout", document)
        self.assertIn("fish_pack | 5", document)
        self.assertIn("Budget: Unlimited", document)
        self.assertGreater(order["total_cents"], 2500)
        self.assertEqual(self.g.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 1)
        self.assertEqual([line for line in self.output.getvalue().splitlines() if line.startswith("Assistant:")][-1], "Assistant: ordered")

    def test_empty_final_cart_saved_without_signal(self):
        order = self.run_flow(["Remove milk and remove eggs", "That's all"])
        self.assertIsNone(order)
        self.assertEqual(self.signals(), [])
        self.assertIn("final cart is empty", (self.root / "runtime/purchase_list.md").read_text())

    def test_cancelled_conversation_generates_no_final_list_or_signal(self):
        self.assertIsNone(self.run_flow(["Cancel this order"]))
        self.assertFalse((self.root / "runtime/purchase_list.md").exists())
        self.assertEqual(self.signals(), [])

    def test_unfinished_conversation_generates_no_signal(self):
        self.assertIsNone(self.run_flow([""]))
        self.assertEqual(self.signals(), [])
        self.assertEqual(self.g.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)

    def test_standalone_finish_does_not_depend_on_model_calling_a_finish_tool(self):
        self.g.record(self.frame)
        agent = Agent(self.g)
        agent.run("prepare")
        class Model:
            def chat(self, *args, **kwargs):
                raise AssertionError("Standalone completion should not need a model call")
        agent.provider = Model()
        agent.run("interact", "That's all.")
        self.assertTrue(agent.finished)
        self.assertIsNone(agent.pending_issue)

    def test_unresolved_adjustment_cannot_finish_or_publish_signal(self):
        self.assertIsNone(self.run_flow(["Change something unusual", "That's all", "That's all", "That's all"]))
        self.assertEqual(self.signals(), [])
        self.assertEqual(self.g.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)

    def test_document_write_failure_prevents_signal_and_checkout(self):
        with patch.object(app, "save_purchase_list", side_effect=OSError("Disk is full")):
            with self.assertRaises(OSError):
                self.run_flow(["That's all"])
        self.assertEqual(self.signals(), [])
        self.assertEqual(self.g.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)

    def test_signal_is_idempotent_and_rejects_a_stale_cart(self):
        self.g.record(self.frame)
        cart = self.g.build_cart()
        document = save_purchase_list(self.root, cart, cart, ["That's all"], "Scripted completion")
        first_path, first = emit_order_signal(self.root, self.g, cart, document)
        second_path, second = emit_order_signal(self.root, self.g, cart, document)
        self.assertEqual(first, second)
        self.assertEqual(first_path, second_path)
        self.assertEqual(len(self.signals()), 1)
        self.g.edit_cart(cart["id"], cart["version"], "apples_each", 2)
        with self.assertRaises(ValueError):
            emit_order_signal(self.root, self.g, cart, document)

    def test_wrong_sku_or_zero_add_cannot_silently_ignore_a_removal(self):
        self.g.record(self.frame)
        cart = self.g.build_cart()
        with self.assertRaisesRegex(ValueError, "oat_unsweetened"):
            self.g.edit_cart(cart["id"], cart["version"], "dairy_milk", 0, "set")
        with self.assertRaisesRegex(ValueError, "operation=set"):
            self.g.edit_cart(cart["id"], cart["version"], "oat_unsweetened", 0, "add")
        self.assertEqual(self.g.cart(cart["id"])["version"], cart["version"])

    def test_no_ordered_announcement_when_signal_cannot_be_saved(self):
        with patch.object(app, "emit_order_signal", side_effect=OSError("Disk is full")):
            with self.assertRaises(OSError):
                self.run_flow(["That's all"])
        self.assertNotIn("Assistant: ordered", self.output.getvalue())
        self.assertEqual(self.g.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()

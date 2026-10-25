import json
import io
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from grocery.agent import Agent
from grocery.core import Grocery, iso, now, cart_summary
from grocery.providers import load_env, DeepSeek
from grocery.voice import Voice, describe_observation
from grocery.__main__ import cycle
from grocery.catalog import CATEGORIES, CATALOG, DEMO_FULL_STOCK, format_product_count
import grocery.__main__ as app

ROOT = Path(__file__).resolve().parents[1]


class GroceryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        folder = Path(self.tmp.name)
        self.output_root = folder
        self.output_patch = patch.object(app, "ROOT", folder)
        self.output_patch.start()
        self.preferences = folder / "preferences.md"
        self.preferences.write_text((ROOT/"data/demo_preferences.md").read_text(), encoding="utf-8")
        self.g = Grocery(folder/"state.sqlite", self.preferences)
        self.g.seed_history()
        self.sequence = 0

    def tearDown(self):
        self.g.close()
        self.output_patch.stop()
        self.tmp.cleanup()

    def frame(self, **quantities):
        self.sequence += 1
        return {"frame_id": f"test-{self.sequence}", "captured_at": iso(now()+timedelta(microseconds=self.sequence)),
                "items": [{"category": c, "quantity": q, "confidence": .99, "clear": True} for c, q in quantities.items()]}

    def low(self):
        frame = self.frame(eggs=2, milk=0, yogurt=2)
        self.g.record(frame)
        return frame

    def test_observation_generates_pack_rounded_personalized_cart(self):
        self.low()
        c = self.g.build_cart()
        self.assertEqual({l["sku"]: l["packs"] for l in c["lines"]}, {"eggs_6": 1, "oat_unsweetened": 1})
        self.assertEqual(c["total_cents"], 948)

    def test_duplicate_observation_is_not_another_consumption(self):
        frame = self.low()
        result = self.g.record(frame)
        self.assertTrue(result["duplicate"])
        self.assertEqual(len(self.g.context()["inventory"]), 3)
        self.assertEqual(next(r["quantity"] for r in self.g.context()["inventory"] if r["category"] == "eggs"), 2)

    def test_stale_frame_does_not_replace_newer_count(self):
        self.low()
        older = self.frame(eggs=100)
        older["captured_at"] = iso(now()-timedelta(hours=1))
        self.g.record(older)
        self.assertEqual(next(r["quantity"] for r in self.g.context()["inventory"] if r["category"] == "eggs"), 2)

    def test_omitted_item_does_not_become_zero(self):
        self.g.record(self.frame(eggs=8, milk=2))
        self.g.record(self.frame(eggs=8))
        self.assertEqual(next(r["quantity"] for r in self.g.context()["inventory"] if r["category"] == "milk"), 2)
        self.assertEqual(self.g.plan(), [])

    def test_uncertain_new_observation_blocks_stale_shortage(self):
        self.low()
        f = self.frame(milk=None)
        f["items"][0]["clear"] = False
        self.g.record(f)
        self.assertNotIn("milk", {n["category"] for n in self.g.plan()})
        self.assertIn("milk", self.g.context()["uncertain_categories"])
        self.g.record(self.frame(milk=0))
        self.assertIn("milk", {n["category"] for n in self.g.plan()})

    def test_inbound_before_horizon_avoids_duplicate_milk(self):
        self.low()
        with self.g.db:
            self.g.db.execute("INSERT INTO inbound VALUES (?,?,?,?,?)", ("external", "milk", 1, iso(now()+timedelta(hours=12)), "confirmed"))
        self.assertEqual({n["category"] for n in self.g.plan()}, {"eggs"})

    def test_late_inbound_does_not_hide_shortage(self):
        self.low()
        with self.g.db:
            self.g.db.execute("INSERT INTO inbound VALUES (?,?,?,?,?)", ("external", "milk", 1, iso(now()+timedelta(days=5)), "confirmed"))
        self.assertIn("milk", {n["category"] for n in self.g.plan()})

    def test_checkout_is_idempotent_and_does_not_add_on_hand_stock(self):
        self.low()
        c = self.g.build_cart()
        first = self.g.checkout(c["id"], c["version"], "one")
        second = self.g.checkout(c["id"], c["version"], "one")
        self.assertEqual(first, second)
        self.assertTrue(first["simulated"])
        self.assertEqual(self.g.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 1)
        self.assertEqual(next(r["quantity"] for r in self.g.context()["inventory"] if r["category"] == "milk"), 0)
        self.assertEqual(self.g.plan(), [])

    def test_latest_cart_version_required_at_checkout(self):
        self.low()
        c = self.g.build_cart()
        changed = self.g.edit_cart(c["id"], c["version"], "yogurt_unsweetened", 2)
        with self.assertRaises(ValueError):
            self.g.checkout(c["id"], c["version"], "one")
        order = self.g.checkout(changed["id"], changed["version"], "one")
        self.assertEqual(order["total_cents"], 1346)

    def test_new_observation_invalidates_draft(self):
        self.low()
        c = self.g.build_cart()
        self.g.record(self.frame(eggs=8, milk=2, yogurt=2))
        with self.assertRaises(ValueError):
            self.g.checkout(c["id"], c["version"], "one")

    def test_large_cart_is_allowed_with_unlimited_budget(self):
        self.low()
        c = self.g.build_cart()
        c = self.g.edit_cart(c["id"], c["version"], "fish_pack", 20)
        order = self.g.checkout(c["id"], c["version"], "one")
        self.assertGreater(order["total_cents"], 2500)
        self.assertIsNone(self.g.context()["budget_cents"])
        self.assertEqual(self.g.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 1)

    def test_temporary_voice_addition_does_not_change_long_term_preference(self):
        self.low()
        a = Agent(self.g)
        a.run("prepare")
        before = self.preferences.read_text()
        a.run("interact", "再加两盒无糖酸奶")
        self.assertEqual(self.preferences.read_text(), before)
        self.assertEqual(next(l["packs"] for l in self.g.cart(a.active_cart)["lines"] if l["sku"] == "yogurt_unsweetened"), 2)

    def test_explicit_preference_is_saved_and_changes_next_selection(self):
        self.low()
        a = Agent(self.g)
        a.run("prepare")
        a.run("interact", "以后牛奶都买普通牛奶")
        self.assertIn("以后牛奶都买普通牛奶", self.preferences.read_text())
        self.assertEqual(self.g.default_sku("milk"), "dairy_milk")
        self.assertNotIn("- [milk] 牛奶优先买无糖燕麦奶。", self.preferences.read_text())

    def test_unauthorized_memory_tool_is_rejected(self):
        a = Agent(self.g)
        a.latest_utterance = "今天加两盒酸奶"
        with self.assertRaises(ValueError):
            a.dispatch("remember_preference", {"category": "yogurt", "preference": "以后总买酸奶"}, "interact")

    def test_cancel_cannot_be_interpreted_as_checkout(self):
        self.low()
        a = Agent(self.g)
        a.run("prepare")
        a.run("interact", "不要下单，取消")
        self.assertTrue(a.cancelled)
        self.assertFalse(a.finished)
        with self.assertRaises(ValueError):
            self.g.checkout(a.active_cart, 1, "one")

    def test_missing_history_does_not_invent_demand(self):
        self.g.db.execute("DELETE FROM history")
        self.g.db.commit()
        self.low()
        self.assertEqual(self.g.plan(), [])

    def test_invalid_count_rejected_atomically(self):
        for quantity in [-1, float("nan"), True]:
            with self.assertRaises(ValueError):
                self.g.record(self.frame(milk=quantity))
        self.assertEqual(self.g.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 0)

    def test_real_agent_tool_loop_executes_application_tools(self):
        self.low()
        class FakeModel:
            def __init__(self):
                self.calls = 0
            def chat(self, messages, tools=None):
                self.calls += 1
                if self.calls == 1:
                    return {"content": None, "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "get_household_context", "arguments": "{}"}}]}
                if self.calls == 2:
                    assert messages[-1]["role"] == "tool"
                    assert "restock_needs" in messages[-1]["content"]
                    return {"content": None, "tool_calls": [{"id": "t2", "type": "function", "function": {"name": "build_cart", "arguments": json.dumps({"selections": [{"category": "milk", "sku": "oat_unsweetened"}]})}}]}
                return {"content": "购物车已建立。"}
        model = FakeModel()
        a = Agent(self.g, model)
        a.run("prepare")
        self.assertIsNotNone(a.active_cart)
        self.assertEqual(model.calls, 3)
        self.assertEqual(self.g.cart(a.active_cart)["total_cents"], 948)


    def test_english_voice_replay_completes_the_full_order(self):
        args = SimpleNamespace(presence="home", silence_policy="hold")
        output = io.StringIO()
        with patch.dict("os.environ", {"VOICE_LOCALE": "en-US"}), patch("grocery.__main__.trace"), redirect_stdout(output):
            voice = Voice(ROOT)
            order = cycle(self.g, None, self.frame(eggs=2, milk=0, yogurt=2), args, voice)
        self.assertTrue(order["simulated"])
        self.assertEqual(order["total_cents"], 1346)
        spoken = [line for line in output.getvalue().splitlines() if line.startswith("Assistant: ")]
        self.assertTrue(any("Would you like to add anything?" in line for line in spoken))
        self.assertEqual(spoken[-1], "Assistant: ordered")
        self.assertFalse(any("\u4e00" <= char <= "\u9fff" for line in spoken for char in line))

    def test_english_addition_and_curly_apostrophe_finish(self):
        self.low()
        a = Agent(self.g)
        a.run("prepare")
        before = self.preferences.read_text()
        a.run("interact", "Add two unsweetened yogurts. That’s all.")
        self.assertTrue(a.finished)
        self.assertEqual(self.g.cart(a.active_cart)["total_cents"], 1346)
        self.assertEqual(self.preferences.read_text(), before)

    def test_english_long_term_preference_changes_future_selection(self):
        self.low()
        a = Agent(self.g)
        a.run("prepare")
        a.run("interact", "From now on, always buy regular milk.")
        self.assertIn("From now on, always buy regular milk.", self.preferences.read_text())
        self.assertEqual(self.g.default_sku("milk"), "dairy_milk")

    def test_english_do_not_order_cancels_without_submitting(self):
        self.low()
        a = Agent(self.g)
        a.run("prepare")
        a.run("interact", "Don’t order. Cancel this order.")
        self.assertTrue(a.cancelled)
        self.assertFalse(a.finished)
        self.assertEqual(self.g.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)



    def test_photo_description_reports_uncertainty_without_forcing_an_order(self):
        args = SimpleNamespace(presence="home", silence_policy="hold", describe=True)
        output = io.StringIO()
        with patch.dict("os.environ", {"VOICE_LOCALE": "en-US"}), patch("grocery.__main__.trace"), redirect_stdout(output):
            voice = Voice(ROOT)
            frame = self.frame(eggs=None, milk=1, yogurt=3)
            order = cycle(self.g, None, frame, args, voice)
        self.assertIsNone(order)
        self.assertIn("1 carton of milk", output.getvalue())
        self.assertIn("3 packs of yogurt", output.getvalue())
        self.assertIn("could not confirm the amount of eggs", output.getvalue())
        self.assertIn("No order will be placed", output.getvalue())
        self.assertEqual(self.g.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)



    def test_apple_voice_request_adds_two_individual_apples(self):
        self.low()
        a = Agent(self.g)
        a.run("prepare")
        before = self.preferences.read_text()
        a.run("interact", "Please add two apples")
        cart = self.g.cart(a.active_cart)
        apples = next(line for line in cart["lines"] if line["sku"] == "apples_each")
        self.assertEqual(apples["packs"], 2)
        self.assertIn("2 apples", cart_summary(cart, "en-US"))
        self.assertNotIn("packs of apples", cart_summary(cart, "en-US"))
        self.assertEqual(self.preferences.read_text(), before)
        self.assertIsNone(a.pending_issue)
        a.run("interact", "That's all")
        order = self.g.checkout(cart["id"], cart["version"], "apple-test")
        self.assertTrue(order["simulated"])
        self.assertEqual(order["total_cents"], 1126)

    def test_usual_produce_supports_explicit_voice_additions(self):
        self.low()
        a = Agent(self.g)
        a.run("prepare")
        for request in ["Add two onions", "Add two tomatoes", "Add two garlic", "Add two avocados"]:
            a.run("interact", request)
        lines = {line["sku"]: line["packs"] for line in self.g.cart(a.active_cart)["lines"]}
        for sku in ["onions_each", "tomatoes_each", "garlic_bulb", "avocados_each"]:
            self.assertEqual(lines[sku], 2)

    def test_invalid_model_tool_is_logged_and_can_recover_with_edit_cart(self):
        self.low()
        traces = []
        a = Agent(self.g, trace=lambda name, result: traces.append((name, result)))
        a.run("prepare")
        class Model:
            def __init__(self):
                self.calls = 0
            def chat(self, messages, tools=None):
                self.calls += 1
                if self.calls == 1:
                    name, args = "add_to_cart", {"item": "apples", "quantity": 2}
                elif self.calls == 2:
                    self.error = messages[-1]["content"]
                    name, args = "edit_cart", {"sku": "apples_each", "packs": 2, "operation": "add"}
                else:
                    return {"content": "Two apples have been added."}
                return {"content": None, "tool_calls": [{"id": f"t{self.calls}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
        a.provider = Model()
        a.run("interact", "Please add two apples")
        self.assertIsNone(a.pending_issue)
        self.assertIn("edit_cart", a.provider.error)
        self.assertEqual(next(line["packs"] for line in self.g.cart(a.active_cart)["lines"] if line["sku"] == "apples_each"), 2)
        self.assertTrue(any(name == "tool_error" and result["tool_name"] == "add_to_cart" for name, result in traces))

    def test_unavailable_item_cannot_be_silently_skipped_at_checkout(self):
        self.low()
        a = Agent(self.g)
        a.run("prepare")
        a.run("interact", "Add two starfruit. That's all.")
        self.assertIsNotNone(a.pending_issue)
        self.assertFalse(a.finished)
        a.latest_utterance = "That's all"
        with self.assertRaises(ValueError):
            a.dispatch("finish_additions", {}, "interact")
        a.run("interact", "Skip it, that's all")
        self.assertIsNone(a.pending_issue)
        self.assertTrue(a.finished)
        self.assertEqual(self.g.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)

    def test_failed_tool_is_explained_in_voice_and_order_stays_on_hold(self):
        class Model:
            def __init__(self):
                self.responses = iter([
                    ("build_cart", {"selections": []}), None,
                    ("report_unavailable_item", {"item_name": "starfruit"}), None,
                    ("finish_additions", {}), None,
                    ("cancel_cart", {}), None,
                ])
                self.number = 0
            def chat(self, messages, tools=None):
                response = next(self.responses)
                self.number += 1
                if response is None:
                    return {"content": "Processed."}
                name, args = response
                return {"content": None, "tool_calls": [{"id": f"call{self.number}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
        args = SimpleNamespace(presence="home", silence_policy="hold")
        output = io.StringIO()
        with patch.dict("os.environ", {"VOICE_LOCALE": "en-US"}), patch("grocery.__main__.trace"), redirect_stdout(output):
            voice = Voice(ROOT)
            voice.script = iter(["Add two starfruit", "That's all", "Cancel this order"])
            order = cycle(self.g, Model(), self.frame(eggs=2, milk=0, yogurt=2), args, voice)
        self.assertIsNone(order)
        self.assertIn("I could not add starfruit", output.getvalue())
        self.assertIn("not in the demo catalog", output.getvalue())
        self.assertIn("cancelled this order", output.getvalue())
        self.assertEqual(self.g.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)



    def test_fully_stocked_scene_covers_catalog_without_restocking(self):
        frame = json.loads((ROOT/"data/frames/full.json").read_text())
        frame["captured_at"] = iso()
        self.g.record(frame)
        self.assertEqual({row["category"] for row in self.g.context()["inventory"]}, set(CATEGORIES))
        self.assertEqual(len(CATEGORIES), 31)
        self.assertEqual(self.g.plan(), [])
        self.assertEqual(self.g.context()["capacity"]["level"], "full")
        self.assertFalse(self.g.context()["capacity"]["calibrated_volume"])
        self.assertEqual(self.g.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)

    def test_large_restock_scene_only_buys_the_depleted_staples(self):
        frame = json.loads((ROOT/"data/frames/low.json").read_text())
        frame["captured_at"] = iso()
        self.g.record(frame)
        self.assertEqual(len(self.g.context()["inventory"]), 31)
        self.assertEqual({need["category"] for need in self.g.plan()}, {"eggs", "milk"})

    def test_newly_supported_food_can_trigger_learned_restock(self):
        frame = json.loads((ROOT/"data/frames/full.json").read_text())
        frame["captured_at"] = iso(now()-timedelta(seconds=1))
        self.g.record(frame)
        self.g.record(self.frame(spinach=0))
        self.assertEqual({need["category"] for need in self.g.plan()}, {"spinach"})
        cart = self.g.build_cart()
        self.assertEqual([(line["sku"], line["packs"]) for line in cart["lines"]], [("spinach_bag", 1)])

    def test_new_food_voice_requests_use_correct_product_units(self):
        self.low()
        agent = Agent(self.g)
        agent.run("prepare")
        for request in ["Add two bags of spinach", "Add two heads of broccoli", "Add two packs of cheese"]:
            agent.run("interact", request)
        cart = self.g.cart(agent.active_cart)
        lines = {line["sku"]: line["packs"] for line in cart["lines"]}
        self.assertEqual(lines["spinach_bag"], 2)
        self.assertEqual(lines["broccoli_head"], 2)
        self.assertEqual(lines["cheese_pack"], 2)
        summary = cart_summary(cart, "en-US")
        self.assertIn("2 bags of spinach", summary)
        self.assertIn("2 heads of broccoli", summary)
        for sku in CATALOG:
            self.assertTrue(format_product_count(sku, 1, "en-US"))
            self.assertTrue(format_product_count(sku, 2, "en-US"))

    def test_orange_juice_is_not_added_as_both_oranges_and_juice(self):
        self.low()
        agent = Agent(self.g)
        agent.run("prepare")
        agent.run("interact", "Add two cartons of orange juice")
        lines = {line["sku"]: line["packs"] for line in self.g.cart(agent.active_cart)["lines"]}
        self.assertEqual(lines["juice_carton"], 2)
        self.assertNotIn("oranges_each", lines)

    def test_vision_omissions_become_unknown_and_full_capacity_is_qualitative(self):
        image = Path(self.tmp.name)/"sample.jpg"
        image.write_bytes(b"fake image bytes; HTTP transport is stubbed in this test")
        provider = object.__new__(DeepSeek)
        calls = []
        def chat(messages, **kwargs):
            calls.append((messages, kwargs))
            return {"content": json.dumps({"items": [{"category": "lemons", "quantity": 3, "confidence": .95, "clear": True}], "capacity_level": "high"})}
        provider.chat = chat
        frame = provider.observe(image, unseen_policy="unknown")
        self.assertEqual({item["category"] for item in frame["items"]}, set(CATEGORIES))
        self.assertEqual(next(item["quantity"] for item in frame["items"] if item["category"] == "lemons"), 3)
        self.assertTrue(all(item["quantity"] is None for item in frame["items"] if item["category"] != "lemons"))
        self.assertIn("spinach", calls[0][0][0]["content"])
        self.assertGreaterEqual(calls[0][1]["max_tokens"], 4096)

    def test_photo_unseen_foods_default_to_assumed_empty_but_visible_uncertainty_remains(self):
        image = Path(self.tmp.name)/"sample.jpg"
        image.write_bytes(b"test image; transport is stubbed")
        provider = object.__new__(DeepSeek)
        provider.chat = lambda *args, **kwargs: {"content": json.dumps({"items": [
            {"category": "milk", "visibility": "present", "quantity": 2, "confidence": .95, "clear": True},
            {"category": "eggs", "visibility": "not_seen", "quantity": None, "confidence": 0, "clear": False},
            {"category": "yogurt", "visibility": "uncertain", "quantity": 5, "confidence": .95, "clear": True}
        ]})}
        frame = provider.observe(image)
        frame.update(frame_id="photo-assumptions", captured_at=iso(), source="deepseek image inference")
        self.g.record(frame)
        items = {item["category"]: item for item in frame["items"]}
        self.assertEqual(items["eggs"]["quantity"], 0)
        self.assertEqual(items["apples"]["quantity"], 0)  # Omitted category also counts as empty.
        self.assertTrue(items["apples"]["assumed_empty"])
        self.assertEqual(items["apples"]["confidence"], 0)  # Do not fake visual certainty.
        self.assertIsNone(items["yogurt"]["quantity"])
        inventory = {item["category"]: item for item in self.g.context()["inventory"]}
        self.assertEqual(inventory["milk"]["quantity"], 2)
        self.assertTrue(inventory["apples"]["assumed_empty"])
        needs = {need["category"]: need for need in self.g.plan()}
        self.assertIn("eggs", needs)
        self.assertIn("apples", needs)
        self.assertNotIn("milk", needs)
        self.assertNotIn("yogurt", needs)
        self.assertIn("assumed empty", needs["apples"]["reason"])
        self.assertIn("unseen foods as empty", describe_observation(frame))

    def test_assumed_empty_does_not_create_consumption_history(self):
        frame = self.frame(eggs=12)
        frame["captured_at"] = iso(now()-timedelta(days=2))
        self.g.record(frame)
        before = self.g.db.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        empty = self.frame(eggs=0)
        empty["unseen_food_policy"] = "empty"
        empty["items"][0].update(assumed_empty=True, visibility="not_seen", confidence=0, clear=False)
        self.g.record(empty)
        self.assertEqual(self.g.db.execute("SELECT COUNT(*) FROM history").fetchone()[0], before)
        self.assertTrue(next(need for need in self.g.plan() if need["category"] == "eggs")["assumed_empty"])

    def test_invalid_assumed_empty_is_rejected_before_inventory_changes(self):
        frame = self.frame(eggs=5)
        frame["items"][0].update(assumed_empty=True, visibility="not_seen")
        frame["unseen_food_policy"] = "empty"
        with self.assertRaises(ValueError):
            self.g.record(frame)
        self.assertEqual(self.g.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 0)

    def test_english_demo_output_and_model_context(self):
        frame = self.low()
        voice = Voice(ROOT, mode="scripted", speak=False)
        args = SimpleNamespace(presence="home", silence_policy="hold")
        # Fresh frame so the complete cycle runs instead of deduplicating.
        frame["frame_id"] += "-english"
        output = io.StringIO()
        with patch.object(app, "ROOT", Path(self.tmp.name)), redirect_stdout(output):
            order = cycle(self.g, None, frame, args, voice)
        self.assertTrue(order["simulated"])
        self.assertIn("[decision]", output.getvalue())
        self.assertNotRegex(output.getvalue(), r"[\u4e00-\u9fff]")
        self.assertNotRegex(json.dumps(self.g.context(), ensure_ascii=False), r"[\u4e00-\u9fff]")
        self.assertNotRegex(json.dumps(Agent(self.g).tools("interact"), ensure_ascii=False), r"[\u4e00-\u9fff]")

    def test_photo_demo_does_not_preload_synthetic_full_fridge_counts(self):
        temporary_root = Path(self.tmp.name)/"photo-demo"
        (temporary_root/"data").mkdir(parents=True)
        (temporary_root/"data/demo_preferences.md").write_text((ROOT/"data/demo_preferences.md").read_text())
        args = SimpleNamespace(command="demo", reset=True, mode="deepseek", voice="scripted", speak=False,
                               image=temporary_root/"photo.jpg", scenario="restock", inbound_milk=False,
                               presence="home", silence_policy="hold", describe=False)
        image_frame = self.frame(milk=1)
        inventory = []
        def inspect_cycle(grocery, provider, frame, args, voice):
            grocery.record(frame)
            inventory.extend(row["category"] for row in grocery.context()["inventory"])
        with patch.object(app, "ROOT", temporary_root), patch.object(app, "arguments", return_value=args), \
             patch.object(app, "DeepSeek", return_value=object()), patch.object(app, "observation", return_value=image_frame) as observe, \
             patch.object(app, "cycle", side_effect=inspect_cycle), redirect_stdout(io.StringIO()):
            app.main()
        self.assertEqual(observe.call_count, 1)
        self.assertEqual(inventory, ["milk"])


if __name__ == "__main__":
    unittest.main()

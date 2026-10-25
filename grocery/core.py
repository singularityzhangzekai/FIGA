from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .catalog import (CATEGORIES, VISION_CATEGORIES, LABELS, CATALOG,
                      DEMO_DAILY_USAGE, format_product_count)


def now():
    return datetime.now(timezone.utc)


def iso(value=None):
    return (value or now()).isoformat()


def stamp(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return dt


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


class Grocery:
    def __init__(self, db_path, preferences_path, horizon_days=2):
        self.db = sqlite3.connect(str(db_path))
        self.db.row_factory = sqlite3.Row
        self.preferences_path = Path(preferences_path)
        self.horizon = horizon_days
        self.db.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS observations (
          frame_id TEXT PRIMARY KEY, captured_at TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inventory (
          category TEXT PRIMARY KEY, quantity REAL NOT NULL, captured_at TEXT NOT NULL,
          frame_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS uncertain_inventory (
          category TEXT PRIMARY KEY, captured_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS history (
          event_id TEXT PRIMARY KEY, category TEXT NOT NULL, consumed REAL NOT NULL,
          sku TEXT NOT NULL, day TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inbound (
          id TEXT PRIMARY KEY, category TEXT NOT NULL, units REAL NOT NULL,
          arrives_at TEXT NOT NULL, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS carts (
          id TEXT PRIMARY KEY, version INTEGER NOT NULL, status TEXT NOT NULL,
          lines TEXT NOT NULL, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS orders (
          id TEXT PRIMARY KEY, cart_id TEXT UNIQUE NOT NULL, idempotency_key TEXT UNIQUE NOT NULL,
          payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS preferences_events (
          id TEXT PRIMARY KEY, category TEXT NOT NULL, value TEXT NOT NULL,
          source TEXT NOT NULL, created_at TEXT NOT NULL);
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def seed_history(self):
        """Explicitly synthetic data; never mixed with a real household database."""
        with self.db:
            for day in range(14):
                date = (now() - timedelta(days=day + 1)).date().isoformat()
                for cat, units in DEMO_DAILY_USAGE.items():
                    sku = next(sku for sku, product in CATALOG.items() if product["category"] == cat)
                    self.db.execute("INSERT OR IGNORE INTO history VALUES (?,?,?,?,?)",
                                    (f"synthetic-{date}-{cat}", cat, units, sku, date))
        self.refresh_habits()

    def habits(self):
        result = {}
        for cat in CATEGORIES:
            rows = self.db.execute("SELECT * FROM history WHERE category=? ORDER BY day DESC LIMIT 30", (cat,)).fetchall()
            if not rows:
                result[cat] = {"daily_units": None, "sample_days": 0, "frequent_sku": None}
                continue
            first = min(datetime.fromisoformat(r["day"]).date() for r in rows)
            last = max(datetime.fromisoformat(r["day"]).date() for r in rows)
            days = max(1, (last - first).days + 1)
            sku_counts = {}
            for r in rows:
                sku_counts[r["sku"]] = sku_counts.get(r["sku"], 0) + 1
            result[cat] = {"daily_units": round(sum(r["consumed"] for r in rows) / days, 3),
                           "sample_days": days, "frequent_sku": max(sku_counts, key=sku_counts.get)}
        return result

    def refresh_habits(self):
        text = self.preferences_path.read_text(encoding="utf-8")
        lines = [f"- {cat.replace('_', ' ')}: estimated daily use {h['daily_units']} units; evidence window {h['sample_days']} days; frequent SKU {h['frequent_sku']}."
                 for cat, h in self.habits().items() if h["daily_units"] is not None]
        block = "<!-- inferred:start -->\n" + "\n".join(lines) + "\n<!-- inferred:end -->"
        if "<!-- inferred:start -->" not in text:
            text += "\n## Inferred consumption history\n" + block + "\n"
        else:
            text = re.sub(r"<!-- inferred:start -->.*?<!-- inferred:end -->", lambda _: block, text, flags=re.S)
        atomic_write(self.preferences_path, text)

    def record(self, frame):
        """A scene observation is an absolute count, never a consumption instruction."""
        frame_id = frame.get("frame_id")
        if not isinstance(frame_id, str) or not frame_id:
            raise ValueError("Missing frame_id")
        captured = stamp(frame["captured_at"])
        if captured > now() + timedelta(minutes=5):
            raise ValueError("Frame timestamp is in the future")
        items = frame.get("items")
        if not isinstance(items, list):
            raise ValueError("items must be an array")
        seen = set()
        for item in items:
            cat, q = item.get("category"), item.get("quantity")
            confidence = item.get("confidence")
            if cat not in CATEGORIES or cat in seen:
                raise ValueError("Unknown or duplicate food category")
            seen.add(cat)
            if not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError("confidence must be between 0 and 1")
            if q is not None and (isinstance(q, bool) or not isinstance(q, (int, float)) or not math.isfinite(q) or q < 0 or q > 1000):
                raise ValueError("Invalid inventory quantity")
            if item.get("assumed_empty") and (frame.get("unseen_food_policy") != "empty" or
                                              item.get("visibility") != "not_seen" or q != 0):
                raise ValueError("Assumed empty requires the empty policy, not_seen visibility, and zero quantity")
        existing = self.db.execute("SELECT frame_id FROM observations WHERE frame_id=?", (frame_id,)).fetchone()
        if existing:
            return {"duplicate": True, "updated": [], "uncertain": []}
        updated, uncertain = [], []
        with self.db:
            self.db.execute("INSERT INTO observations VALUES (?,?,?)", (frame_id, iso(captured), json.dumps(frame, ensure_ascii=False)))
            for item in items:
                cat = item["category"]
                assumed_empty = item.get("assumed_empty") is True
                if not assumed_empty and (item.get("quantity") is None or item["confidence"] < .8 or not item.get("clear", False)):
                    uncertain.append(cat)
                    old_uncertain = self.db.execute("SELECT captured_at FROM uncertain_inventory WHERE category=?", (cat,)).fetchone()
                    if not old_uncertain or stamp(old_uncertain["captured_at"]) < captured:
                        self.db.execute("INSERT OR REPLACE INTO uncertain_inventory VALUES (?,?)", (cat, iso(captured)))
                    continue
                old = self.db.execute("SELECT * FROM inventory WHERE category=?", (cat,)).fetchone()
                if old and stamp(old["captured_at"]) >= captured:
                    continue
                quantity = float(item["quantity"])
                unresolved = self.db.execute("SELECT captured_at FROM uncertain_inventory WHERE category=?", (cat,)).fetchone()
                if unresolved and stamp(unresolved["captured_at"]) <= captured:
                    self.db.execute("DELETE FROM uncertain_inventory WHERE category=?", (cat,))
                self.db.execute("INSERT OR REPLACE INTO inventory VALUES (?,?,?,?)", (cat, quantity, iso(captured), frame_id))
                if old and old["quantity"] > quantity and not assumed_empty:
                    observed_days = (captured - stamp(old["captured_at"])).total_seconds() / 86400
                    # Only full-day intervals contribute to a daily consumption estimate.
                    # The event is a visible depletion estimate, not proof of eating.
                    previous_frame = self.db.execute("SELECT payload FROM observations WHERE frame_id=?", (old["frame_id"],)).fetchone()
                    previous_items = json.loads(previous_frame["payload"]).get("items", []) if previous_frame else []
                    previous_assumed = any(i.get("category") == cat and i.get("assumed_empty") for i in previous_items)
                    if observed_days >= 1 and not previous_assumed:
                        self.db.execute("INSERT OR IGNORE INTO history VALUES (?,?,?,?,?)",
                                        (f"depletion-{frame_id}-{cat}", cat, (old["quantity"]-quantity)/observed_days,
                                         self.default_sku(cat), captured.date().isoformat()))
                updated.append(cat)
            if updated or uncertain:
                self.db.execute("UPDATE carts SET status='stale' WHERE status='draft'")
        if updated:
            self.refresh_habits()
        return {"duplicate": False, "updated": updated, "uncertain": uncertain}

    def default_sku(self, cat):
        text = self.preferences_path.read_text(encoding="utf-8")
        if cat == "milk":
            explicit = re.search(r"^- \[milk\] (.+)$", text, flags=re.M)
            if explicit and ("无糖燕麦" in explicit.group(1) or "unsweetened oat" in explicit.group(1).lower()):
                return "oat_unsweetened"
            if explicit and ("普通牛奶" in explicit.group(1) or re.search(r"regular (?:dairy )?milk|dairy milk|whole milk", explicit.group(1), flags=re.I)):
                return "dairy_milk"
        frequent = self.habits().get(cat, {}).get("frequent_sku")
        if frequent in CATALOG and CATALOG[frequent]["category"] == cat:
            return frequent
        return next(s for s, p in CATALOG.items() if p["category"] == cat)

    def plan(self):
        current = now()
        horizon_at = current + timedelta(days=self.horizon)
        result = []
        for row in self.db.execute("SELECT * FROM inventory"):
            cat = row["category"]
            unresolved = self.db.execute("SELECT captured_at FROM uncertain_inventory WHERE category=?", (cat,)).fetchone()
            if unresolved and stamp(unresolved["captured_at"]) >= stamp(row["captured_at"]):
                continue
            age = (current - stamp(row["captured_at"])).total_seconds()
            if age > 86400:  # An old count must not silently trigger autonomous purchases.
                continue
            h = self.habits()[cat]
            if h["daily_units"] is None:
                continue
            target = math.ceil(h["daily_units"] * self.horizon)
            inbound = sum(r["units"] for r in self.db.execute("SELECT * FROM inbound WHERE category=? AND status IN ('confirmed','simulated')", (cat,))
                          if stamp(r["arrives_at"]) <= horizon_at)
            missing = max(0, target - row["quantity"] - inbound)
            if missing > 0:
                assumed_empty = self.inventory_entry(row)["assumed_empty"]
                result.append({"category": cat, "on_hand": row["quantity"], "target_units": target,
                               "inbound_units": inbound, "needed_units": missing,
                               "assumed_empty": assumed_empty,
                               "reason": ("Unseen in photo; assumed empty. " if assumed_empty else "") +
                                         f"Estimated need: {target} units over {self.horizon} days, based on {h['sample_days']} days of records"})
        return result

    def context(self):
        latest = self.db.execute("SELECT payload FROM observations ORDER BY captured_at DESC LIMIT 1").fetchone()
        latest_frame = json.loads(latest["payload"]) if latest else {}
        return {"capacity": {"level": latest_frame.get("capacity_level", "unknown"),
                             "note": latest_frame.get("capacity_note", ""),
                             "source": latest_frame.get("source", ""),
                             "calibrated_volume": False},
                "unseen_food_policy": latest_frame.get("unseen_food_policy", "unknown"),
                "inventory": [self.inventory_entry(r) for r in self.db.execute("SELECT * FROM inventory")],
                "uncertain_categories": [r["category"] for r in self.db.execute("SELECT category FROM uncertain_inventory")],
                "preferences_markdown": self.preferences_path.read_text(encoding="utf-8"),
                "habits": self.habits(), "restock_needs": self.plan(),
                "catalog": {sku: {**{key: value for key, value in product.items() if not key.endswith("_zh")}, "name": product["singular_en"]} for sku, product in CATALOG.items()},
                "budget_cents": None, "budget_policy": "unlimited; totals are informational only",
                "prices_source": "synthetic demo catalog, not live Whole Foods prices"}

    def inventory_entry(self, row):
        entry = dict(row)
        source = self.db.execute("SELECT payload FROM observations WHERE frame_id=?", (row["frame_id"],)).fetchone()
        items = json.loads(source["payload"]).get("items", []) if source else []
        entry["assumed_empty"] = any(item.get("category") == row["category"] and item.get("assumed_empty") is True for item in items)
        return entry

    def cart(self, cart_id):
        row = self.db.execute("SELECT * FROM carts WHERE id=?", (cart_id,)).fetchone()
        if not row:
            raise ValueError("Cart does not exist")
        result = dict(row)
        result["lines"] = json.loads(result["lines"])
        result["total_cents"] = sum(l["packs"] * CATALOG[l["sku"]]["cents"] for l in result["lines"])
        return result

    def build_cart(self, selections=None):
        needs = self.plan()
        if not needs:
            return {"status": "no_restock_needed"}
        selected = {x["category"]: x["sku"] for x in (selections or [])}
        lines = []
        for n in needs:
            sku = selected.get(n["category"], self.default_sku(n["category"]))
            if sku not in CATALOG or CATALOG[sku]["category"] != n["category"]:
                raise ValueError("Product does not match the restock category")
            lines.append({"sku": sku, "packs": math.ceil(n["needed_units"] / CATALOG[sku]["pack_size"]), "reason": n["reason"]})
        fingerprint = hashlib.sha256(json.dumps(lines, sort_keys=True).encode()).hexdigest()
        # Reuse a draft instead of generating another cart for the same shortage.
        row = self.db.execute("SELECT id FROM carts WHERE status='draft' ORDER BY created_at LIMIT 1").fetchone()
        if row:
            return self.cart(row["id"])
        cart_id = "cart-" + uuid.uuid4().hex[:10]
        with self.db:
            self.db.execute("INSERT INTO carts VALUES (?,?,?,?,?,?)", (cart_id, 1, "draft", json.dumps(lines, ensure_ascii=False), fingerprint, iso()))
        return self.cart(cart_id)

    def edit_cart(self, cart_id, expected_version, sku, packs, operation="add"):
        c = self.cart(cart_id)
        if c["status"] != "draft" or c["version"] != expected_version:
            raise ValueError("Cart was submitted or its version changed; read it again")
        if sku not in CATALOG or type(packs) is not int or not 0 <= packs <= 20 or operation not in ("add", "set"):
            raise ValueError("Invalid product, sales-unit count, or operation")
        lines = c["lines"]
        line = next((l for l in lines if l["sku"] == sku), None)
        if operation == "add" and packs == 0:
            raise ValueError("Adding zero does not remove an item. Use operation=set with packs=0 for removal")
        if operation == "set" and packs == 0 and not line:
            same_category = [item["sku"] for item in lines if CATALOG[item["sku"]]["category"] == CATALOG[sku]["category"]]
            if same_category:
                raise ValueError(f"SKU {sku} is not in this cart. To remove this food, use the current cart SKU(s): {same_category}")
            return c
        new_quantity = packs + (line["packs"] if line and operation == "add" else 0)
        if new_quantity > 20:
            raise ValueError("Quantity exceeds the per-order limit")
        if line:
            line["packs"] = new_quantity
        elif packs:
            lines.append({"sku": sku, "packs": packs, "reason": "User requested this addition"})
        lines = [l for l in lines if l["packs"] > 0]
        with self.db:
            changed = self.db.execute("UPDATE carts SET lines=?,version=version+1 WHERE id=? AND version=? AND status='draft'",
                                      (json.dumps(lines, ensure_ascii=False), cart_id, expected_version)).rowcount
            if changed != 1:
                raise ValueError("Cart changed concurrently; retry")
        return self.cart(cart_id)

    def remember(self, category, preference, source="explicit voice statement"):
        if category not in CATEGORIES or not isinstance(preference, str) or not 1 <= len(preference) <= 200 or "\n" in preference:
            raise ValueError("Invalid long-term preference")
        text = self.preferences_path.read_text(encoding="utf-8")
        new_line = f"- [{category}] {preference} (source: explicit user request; updated: {now().date().isoformat()})"
        pattern = rf"^- \[{category}\] .*$"
        if re.search(pattern, text, flags=re.M):
            text = re.sub(pattern, lambda _: new_line, text, flags=re.M)
        else:
            text += "\n" + new_line + "\n"
        atomic_write(self.preferences_path, text)
        with self.db:
            self.db.execute("INSERT INTO preferences_events VALUES (?,?,?,?,?)", (uuid.uuid4().hex, category, preference, source, iso()))
        return {"saved": True, "category": category, "preference": preference}

    def cancel(self, cart_id):
        with self.db:
            self.db.execute("UPDATE carts SET status='cancelled' WHERE id=? AND status='draft'", (cart_id,))
        return self.cart(cart_id)

    def validate_checkout(self, cart_id, expected_version):
        """Check the current cart before publishing an order request or submitting it."""
        c = self.cart(cart_id)
        if c["status"] != "draft" or c["version"] != expected_version:
            raise ValueError("Invalid cart status or version")
        if not c["lines"]:
            raise ValueError("Cart is empty")
        needed = {n["category"] for n in self.plan()}
        for line in c["lines"]:
            if line["reason"] not in ("User requested this addition", "用户本次语音加购"):
                if CATALOG[line["sku"]]["category"] not in needed:
                    raise ValueError("Inventory or incoming deliveries changed; replan")
        return c

    def checkout(self, cart_id, expected_version, idempotency_key):
        """Only a labelled local simulation exists. This does NOT call Whole Foods."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            previous = self.db.execute("SELECT payload FROM orders WHERE cart_id=? OR idempotency_key=?", (cart_id, idempotency_key)).fetchone()
            if previous:
                payload = json.loads(previous["payload"])
                if payload["cart_id"] != cart_id:
                    raise ValueError("Idempotency key belongs to another cart")
                return payload
            c = self.validate_checkout(cart_id, expected_version)
            order_id = "SIM-" + uuid.uuid4().hex[:10].upper()
            payload = {"order_id": order_id, "cart_id": cart_id, "cart_version": c["version"],
                       "simulated": True, "merchant_target": "Whole Foods",
                       "status": "simulated_submitted", "lines": c["lines"], "total_cents": c["total_cents"]}
            self.db.execute("INSERT INTO orders VALUES (?,?,?,?,?)", (order_id, cart_id, idempotency_key, json.dumps(payload, ensure_ascii=False), iso()))
            self.db.execute("UPDATE carts SET status='simulated_submitted' WHERE id=?", (cart_id,))
            for line in c["lines"]:
                p = CATALOG[line["sku"]]
                self.db.execute("INSERT INTO inbound VALUES (?,?,?,?,?)", (f"{order_id}-{line['sku']}", p["category"],
                                p["pack_size"] * line["packs"], iso(now() + timedelta(hours=12)), "simulated"))
            return payload


def cart_summary(cart, locale="en-US"):
    return (", " if locale.lower().startswith("en") else "、").join(
        format_product_count(line["sku"], line["packs"], locale) for line in cart["lines"])

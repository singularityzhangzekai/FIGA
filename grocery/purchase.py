"""Publish final purchase lists and simulation-only order-request events."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .catalog import CATALOG, format_product_count
from .core import atomic_write, iso


def markdown_text(value):
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ").replace("<", "&lt;").replace(">", "&gt;")


def save_purchase_list(root, cart, initial_cart, updates, completion, blocked_reason=None):
    """Save one immutable cart-version snapshot and a convenient latest-list copy."""
    root = Path(root)
    path = root / "runtime/purchase_lists" / f"{cart['id']}-v{cart['version']}.md"
    status = "On hold" if blocked_reason else "Ready for simulated checkout"
    lines = ["# Final purchase list", "", f"Generated at: {iso()}",
             f"Cart: {cart['id']} (version {cart['version']})", "Merchant target: Whole Foods",
             f"Status: {status}", f"Conversation completion: {completion}", "",
             "Catalog products and prices are synthetic demo data. Checkout is simulated.", "",
             "## Initial restock proposal", ""]
    for item in initial_cart["lines"]:
        lines.append(f"- {format_product_count(item['sku'], item['packs'])}. {markdown_text(item['reason'])}")
    lines += ["", "## Voice updates", ""]
    lines += [f"- User: {markdown_text(update)}" for update in updates] or ["No voice updates; the configured automatic policy applied."]
    lines += ["", "## Final purchase list", "",
              "| Item | SKU | Sales units | Unit price (USD) | Subtotal (USD) | Basis |",
              "| --- | --- | ---: | ---: | ---: | --- |"]
    for item in cart["lines"]:
        product = CATALOG[item["sku"]]
        lines.append(f"| {product['singular_en']} | {item['sku']} | {item['packs']} | "
                     f"${product['cents']/100:.2f} | ${item['packs']*product['cents']/100:.2f} | {markdown_text(item['reason'])} |")
    if not cart["lines"]:
        lines += ["", "The final cart is empty."]
    lines += ["", f"**Total: ${cart['total_cents']/100:.2f} USD**", "Budget: Unlimited (no budget check; total is informational)", "",
              "## Checkout decision", "",
              f"On hold: {markdown_text(blocked_reason)}. No order signal was generated." if blocked_reason else
              "The additions conversation is complete and cart validation passed. An order request may now be emitted for the simulation adapter.", ""]
    document = "\n".join(lines)
    # Publish the versioned document before its signal. A later cart cannot overwrite this snapshot.
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        without_time = lambda text: "\n".join(line for line in text.splitlines() if not line.startswith("Generated at: "))
        if without_time(existing) != without_time(document):
            raise ValueError("A purchase list already exists for this cart version with different contents")
        document = existing
    else:
        atomic_write(path, document)
    atomic_write(root / "runtime/purchase_list.md", document)
    return path


def emit_order_signal(root, grocery, cart, purchase_list):
    """An event is a request, not proof of submission; checkout revalidates it."""
    current = grocery.validate_checkout(cart["id"], cart["version"])
    if current != cart:
        raise ValueError("Cart changed after its final purchase list was prepared")
    purchase_list = Path(purchase_list)
    document = purchase_list.read_bytes()  # Fail closed if the list was not saved.
    signal_id = f"order-{cart['id']}-v{cart['version']}"
    signal = {"schema_version": 1, "event": "grocery.order_requested", "signal_id": signal_id,
              "created_at": iso(), "status": "ready", "simulated": True,
              "adapter": "local_simulation", "merchant_target": "Whole Foods",
              "cart_id": cart["id"], "cart_version": cart["version"],
              "idempotency_key": "checkout-" + cart["id"], "currency": "USD",
              "budget_policy": "unlimited",
              "total_cents": cart["total_cents"], "lines": cart["lines"],
              "purchase_list_path": str(purchase_list.resolve()),
              "purchase_list_sha256": hashlib.sha256(document).hexdigest(),
              "requires_cart_validation": True}
    path = Path(root) / "runtime/order_signals" / f"{signal_id}.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if {k: v for k, v in existing.items() if k != "created_at"} != {k: v for k, v in signal.items() if k != "created_at"}:
            raise ValueError("An order signal already exists with different contents")
        return path, existing
    atomic_write(path, json.dumps(signal, ensure_ascii=False, indent=2) + "\n")
    return path, signal

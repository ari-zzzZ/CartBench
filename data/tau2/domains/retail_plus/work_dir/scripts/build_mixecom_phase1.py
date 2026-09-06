"""Build the first Mix-ECom-inspired Retail Plus task expansion.

The source conversations are used only as intent and language-pattern material.
Every executable entity, state transition, and golden action is bound to the
Retail Plus database. The build is deterministic and idempotent when run after
``build_phase1.py`` and ``build_policy_phase1.py``.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file()
)
WORK_DIR = Path(__file__).resolve().parents[1]
PHASE_DIR = WORK_DIR / "mixecom_phase1"
SOURCE_PATH = WORK_DIR / "sources" / "mixEcom" / "eval" / "eval_gt_a.json"
DOMAIN_DIR = ROOT / "data" / "tau2" / "domains" / "retail_plus"
TASKS_PATH = DOMAIN_DIR / "tasks.json"
SPLITS_PATH = DOMAIN_DIR / "split_tasks.json"
DB_PATH = DOMAIN_DIR / "db.json"

TASK_PREFIX = "rp_mix_"
SOURCE_URL = "https://huggingface.co/datasets/zhourax977/Mix-ECom"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_source_material() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Load the raw eval split, or the committed selected-row audit fallback."""
    if SOURCE_PATH.is_file():
        source_list = load_jsonl(SOURCE_PATH)
        return (
            {row["task_id"]: row for row in source_list},
            {
                "source_file_size": SOURCE_PATH.stat().st_size,
                "source_file_sha256": hashlib.sha256(SOURCE_PATH.read_bytes()).hexdigest(),
                "source_row_count": len(source_list),
            },
        )

    selected_path = PHASE_DIR / "selected_mixecom_examples.json"
    manifest_path = PHASE_DIR / "mixecom_source_manifest.json"
    if not selected_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError(
            "Mix-ECom source is unavailable and no committed selected-row audit "
            f"fallback exists under {PHASE_DIR}"
        )
    selected = load_json(selected_path)
    manifest = load_json(manifest_path)
    rows = {
        item["mix_ecom_task_id"]: {
            "task_id": item["mix_ecom_task_id"],
            "question_type": item["question_type"],
            "first_query": item["source_first_query"],
            "user_profile": item["source_user_profile"],
        }
        for item in selected
    }
    return (
        rows,
        {
            "source_file_size": manifest["source_file_size"],
            "source_file_sha256": manifest["source_file_sha256"],
            "source_row_count": manifest["source_row_count"],
        },
    )


def action(
    task_id: str,
    index: int,
    name: str,
    arguments: dict[str, Any],
    compare_args: list[str] | None = None,
) -> dict[str, Any]:
    result = {
        "action_id": f"{task_id}_{index}",
        "name": name,
        "arguments": arguments,
        "info": None,
    }
    if compare_args is not None:
        result["compare_args"] = compare_args
    return result


def identity(db: dict[str, Any], order_id: str) -> dict[str, str]:
    order = db["orders"][order_id]
    user = db["users"][order["user_id"]]
    return {
        "user_id": order["user_id"],
        "first_name": user["name"]["first_name"],
        "last_name": user["name"]["last_name"],
        "email": user["email"],
        "zip": user["address"]["zip"],
    }


def order_item(db: dict[str, Any], order_id: str, item_id: str) -> dict[str, Any]:
    return next(
        item for item in db["orders"][order_id]["items"] if item["item_id"] == item_id
    )


def catalog_item(db: dict[str, Any], item_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    for product in db["products"].values():
        if item_id in product["variants"]:
            return product, product["variants"][item_id]
    raise ValueError(f"Catalog item not found: {item_id}")


def payment_id(db: dict[str, Any], order_id: str) -> str:
    history = db["orders"][order_id]["payment_history"]
    if len(history) != 1 or history[0]["transaction_type"] != "payment":
        raise ValueError(f"Order {order_id} must have exactly one initial payment")
    return history[0]["payment_method_id"]


def known_identity(db: dict[str, Any], order_id: str, extra: str) -> str:
    user = identity(db, order_id)
    return (
        f"You are {user['first_name']} {user['last_name']}. Your email is "
        f"{user['email']}. You know order {order_id}. {extra}"
    )


def make_task(
    *,
    task_id: str,
    purpose: str,
    intent: str,
    source_task_id: str,
    reason: str,
    known: str,
    instructions: str,
    actions: list[tuple],
    communicate: list[str],
) -> dict[str, Any]:
    return {
        "id": task_id,
        "description": {
            "purpose": purpose,
            "relevant_policies": None,
            "notes": (
                f"Mix-ECom after-sales eval/{source_task_id}; intent={intent}; "
                "adapted to executable Retail Plus entities and policy."
            ),
        },
        "user_scenario": {
            "persona": (
                "You are a realistic ecommerce customer. Start with the concrete "
                "problem in your own words. Reveal authentication information only "
                "when the agent asks for it."
            ),
            "instructions": {
                "domain": "retail_plus",
                "reason_for_call": reason,
                "known_info": known,
                "unknown_info": (
                    "You do not know internal user IDs, claim IDs, refund IDs, or "
                    "other customers' data unless explicitly stated."
                ),
                "task_instructions": instructions,
            },
        },
        "initial_state": None,
        "evaluation_criteria": {
            "actions": [
                action(
                    task_id,
                    index,
                    spec[0],
                    spec[1],
                    spec[2] if len(spec) > 2 else None,
                )
                for index, spec in enumerate(actions)
            ],
            "communicate_info": communicate,
            "nl_assertions": None,
            "reward_basis": ["DB", "ACTION", "COMMUNICATE"],
        },
    }


def auth(db: dict[str, Any], order_id: str) -> tuple[str, dict[str, str]]:
    return "find_user_id_by_email", {"email": identity(db, order_id)["email"]}


def build_missing_tasks(db: dict[str, Any]) -> list[dict[str, Any]]:
    specs = [
        {
            "id": "rp_mix_missing_component_replacement",
            "source": "000003_a",
            "order": "#W1166549",
            "item": "1569765161",
            "resolution": "replacement",
            "reason": (
                "Your delivered order #W1166549 included the E-Reader and Electric "
                "Kettle, but the Desk Lamp was missing from the parcel. You want the "
                "missing lamp sent to you."
            ),
        },
        {
            "id": "rp_mix_missing_quantity_refund",
            "source": "000008_a",
            "order": "#W1126085",
            "item": "6843647669",
            "resolution": "refund",
            "reason": (
                "Order #W1126085 is marked delivered, but the parcel contained only "
                "packing paper and the Skateboard was missing. You no longer want a "
                "replacement and want a refund for the missing item."
            ),
        },
        {
            "id": "rp_mix_missing_item_replacement",
            "source": "000036_a",
            "order": "#W1355800",
            "item": "5537798301",
            "resolution": "replacement",
            "reason": (
                "Order #W1355800 is marked delivered, but the Cycling Helmet was not "
                "inside the shipping box. You want the missing helmet replaced."
            ),
        },
    ]
    tasks = []
    for spec in specs:
        item = order_item(db, spec["order"], spec["item"])
        safe_order = spec["order"].replace("#", "")
        task_actions = [
            auth(db, spec["order"]),
            ("get_order_details", {"order_id": spec["order"]}),
            (
                "assess_missing_item_claim",
                {"order_id": spec["order"], "item_ids": [spec["item"]]},
            ),
            (
                "file_missing_item_claim",
                {
                    "order_id": spec["order"],
                    "item_ids": [spec["item"]],
                    "requested_resolution": spec["resolution"],
                },
            ),
        ]
        communicate = [f"CLAIM-{safe_order}", item["name"]]
        if spec["resolution"] == "refund":
            communicate.append(str(item["price"]))
        tasks.append(
            make_task(
                task_id=spec["id"],
                purpose="Resolve a concrete delivered-order short-shipment claim.",
                intent="shipping_issue.missing",
                source_task_id=spec["source"],
                reason=spec["reason"],
                known=known_identity(
                    db,
                    spec["order"],
                    f"The missing catalog item is {spec['item']}.",
                ),
                instructions=(
                    f"Ask for a {spec['resolution']} for only the missing item. If "
                    "the agent verifies the item and proposes that resolution, agree "
                    "and stop after the claim is filed. Do not report damage or invent "
                    "any additional missing products."
                ),
                actions=task_actions,
                communicate=communicate,
            )
        )
    return tasks


def build_add_item_tasks(db: dict[str, Any]) -> list[dict[str, Any]]:
    specs = [
        {
            "id": "rp_mix_add_forgotten_notebook",
            "source": "000029_a",
            "order": "#W1080318",
            "item": "9799386954",
            "saved_item_description": (
                "You saved catalog item ID 9799386954 earlier, but no longer "
                "remember its product name, specifications, price, or availability."
            ),
        },
        {
            "id": "rp_mix_add_matching_bottle",
            "source": "000019_a",
            "order": "#W1138897",
            "item": "3229676465",
            "saved_item_description": (
                "You saved catalog item ID 3229676465 earlier, but no longer "
                "remember its product name, specifications, price, or availability."
            ),
        },
        {
            "id": "rp_mix_add_desk_lamp",
            "source": "000069_a",
            "order": "#W1258841",
            "item": "6805564527",
            "saved_item_description": (
                "You saved catalog item ID 6805564527 earlier, but no longer "
                "remember its product name, specifications, price, or availability."
            ),
        },
    ]
    tasks = []
    for spec in specs:
        product, variant = catalog_item(db, spec["item"])
        current_payment = payment_id(db, spec["order"])
        tasks.append(
            make_task(
                task_id=spec["id"],
                purpose="Add one uniquely identified forgotten item before fulfillment.",
                intent="pending_order.add_forgotten_item",
                source_task_id=spec["source"],
                reason=(
                    f"Before shipment, you noticed that you forgot an item you intended "
                    f"to include in order {spec['order']}. {spec['saved_item_description']} "
                    "Ask the agent to identify and verify that exact item, then add it "
                    "to the same pending order if it is available."
                ),
                known=known_identity(
                    db,
                    spec["order"],
                    f"The only catalog information you retained is item ID {spec['item']}.",
                ),
                instructions=(
                    "Ask the agent to verify the exact catalog item before adding it. "
                    f"Use the order's existing payment method {current_payment}. When "
                    "the agent explains the added item and cost and asks for confirmation, "
                    "say yes. Do not request any other item."
                ),
                actions=[
                    auth(db, spec["order"]),
                    ("get_order_details", {"order_id": spec["order"]}),
                    ("get_item_details", {"item_id": spec["item"]}),
                    (
                        "add_pending_order_items",
                        {
                            "order_id": spec["order"],
                            "item_ids": [spec["item"]],
                            "payment_method_id": current_payment,
                        },
                    ),
                ],
                communicate=[
                    product["name"],
                    str(variant["price"]),
                    *[str(value) for value in variant["options"].values()],
                    current_payment,
                ],
            )
        )
    return tasks


def build_payment_tasks(db: dict[str, Any]) -> list[dict[str, Any]]:
    specs = [
        {
            "id": "rp_mix_payment_wrong_gift_card",
            "source": "000013_a",
            "order": "#W1068289",
            "new_payment": "paypal_5398626",
        },
        {
            "id": "rp_mix_payment_switch_paypal",
            "source": "000024_a",
            "order": "#W1130240",
            "new_payment": "paypal_9619477",
        },
        {
            "id": "rp_mix_payment_switch_credit_card",
            "source": "000044_a",
            "order": "#W1416704",
            "new_payment": "credit_card_7898168",
        },
    ]
    tasks = []
    for spec in specs:
        old_payment = payment_id(db, spec["order"])
        order = db["orders"][spec["order"]]
        tasks.append(
            make_task(
                task_id=spec["id"],
                purpose="Correct a mistaken payment method before fulfillment.",
                intent="pending_order.change_payment",
                source_task_id=spec["source"],
                reason=(
                    f"You used {old_payment} by mistake on pending order "
                    f"{spec['order']}. You want the full ${order['payment_history'][0]['amount']} "
                    f"moved to {spec['new_payment']} before the order is processed."
                ),
                known=known_identity(
                    db,
                    spec["order"],
                    f"You recognize the target payment method {spec['new_payment']}.",
                ),
                instructions=(
                    "Do not confirm in the initial request. Let the agent explain that "
                    "the new method will be charged and the old method refunded. Say yes "
                    "only after the agent asks for an explicit confirmation."
                ),
                actions=[
                    auth(db, spec["order"]),
                    ("get_order_details", {"order_id": spec["order"]}),
                    (
                        "modify_pending_order_payment",
                        {
                            "order_id": spec["order"],
                            "payment_method_id": spec["new_payment"],
                        },
                    ),
                ],
                communicate=[
                    str(order["payment_history"][0]["amount"]),
                    old_payment,
                    spec["new_payment"],
                ],
            )
        )
    return tasks


def add_fee_entities(db: dict[str, Any]) -> list[dict[str, Any]]:
    specs = [
        {
            "fee_id": "FEE-MIX-TRANSIT-12",
            "source": "000048_a",
            "order": "#W1023987",
            "item": "8926329222",
            "amount": 12.0,
            "fee_type": "incorrect damage inspection fee",
            "explanation": (
                "The warehouse incorrectly charged an inspection fee after the "
                "customer reported transit-damaged packaging."
            ),
            "intent": "transit_damage",
            "problem": (
                "The outer box of the Luggage Set arrived crushed, although you are "
                "keeping the usable product. A separate $12 damage-inspection fee "
                "appeared on the order and you want that incorrect fee removed."
            ),
        },
        {
            "fee_id": "FEE-MIX-QUALITY-18",
            "source": "000059_a",
            "order": "#W1341845",
            "item": "4402162122",
            "amount": 18.0,
            "fee_type": "incorrect quality review fee",
            "explanation": (
                "A quality-review fee was incorrectly billed for documenting a "
                "keyboard key defect."
            ),
            "intent": "quality_defect",
            "problem": (
                "One key on the Mechanical Keyboard initially stuck, and support "
                "asked you to test it. The keyboard now works and you will keep it, "
                "but an $18 quality-review fee was incorrectly added to the order."
            ),
            "communicate_payment": "6450164",
        },
        {
            "fee_id": "FEE-MIX-DESCRIPTION-9",
            "source": "000090_a",
            "order": "#W1473345",
            "item": "3020722515",
            "amount": 9.0,
            "fee_type": "incorrect listing clarification fee",
            "explanation": (
                "The merchant incorrectly charged the customer for clarification of "
                "an inaccurate product listing."
            ),
            "intent": "not_as_described",
            "problem": (
                "The Coffee Maker listing was unclear about capacity. You decided to "
                "keep the one-cup product, but a separate $9 listing-clarification fee "
                "appeared and you want the incorrect fee removed."
            ),
        },
    ]
    for spec in specs:
        db["order_fees"][spec["fee_id"]] = {
            "fee_id": spec["fee_id"],
            "order_id": spec["order"],
            "fee_type": spec["fee_type"],
            "amount": spec["amount"],
            "explanation": spec["explanation"],
            "status": "charged",
            "waivable": True,
            "refund_id": None,
        }
    return specs


def build_fee_tasks(db: dict[str, Any], specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks = []
    for spec in specs:
        original_payment = payment_id(db, spec["order"])
        refund_id = f"REF-FEE-{spec['fee_id']}"
        tasks.append(
            make_task(
                task_id=f"rp_mix_fee_{spec['intent']}",
                purpose="Investigate and waive an eligible erroneous small fee.",
                intent=f"status_mystery_fee.{spec['intent']}",
                source_task_id=spec["source"],
                reason=spec["problem"],
                known=known_identity(
                    db,
                    spec["order"],
                    f"The fee ID is {spec['fee_id']} and the affected catalog item is {spec['item']}.",
                ),
                instructions=(
                    "Ask the agent to verify the affected item and explain the fee. "
                    "Once told it is eligible for automatic waiver, ask for the fee "
                    "to be waived. Do not request a product return or a second refund."
                ),
                actions=[
                    auth(db, spec["order"]),
                    ("get_order_details", {"order_id": spec["order"]}),
                    ("get_order_fee_details", {"fee_id": spec["fee_id"]}),
                    ("waive_order_fee", {"fee_id": spec["fee_id"]}),
                ],
                communicate=[
                    str(spec["amount"]),
                    refund_id,
                    spec.get("communicate_payment", original_payment),
                ],
            )
        )
    return tasks


def build_core_after_sales_tasks(db: dict[str, Any]) -> list[dict[str, Any]]:
    specs = [
        {
            "id": "rp_mix_wrong_color_exchange",
            "source": "000076_a",
            "intent": "wrong_item",
            "purpose": "Exchange a delivered item whose received color is wrong.",
            "order": "#W1326557",
            "item": "6777246137",
            "reason": (
                "The 750ml Water Bottle in delivered order #W1326557 was supposed to "
                "be red, but you received the wrong color. You want the available black "
                "500ml stainless-steel version, catalog item 3453331371, instead."
            ),
            "instructions": (
                "Ask the agent to verify the available replacement and explain the "
                "price difference. Say yes when asked to confirm the exchange."
            ),
            "actions": [
                ("get_product_details", {"product_id": "8310926033"}),
                ("calculate", {"expression": "52.79 - 47.76"}, []),
                (
                    "exchange_delivered_order_items",
                    {
                        "order_id": "#W1326557",
                        "item_ids": ["6777246137"],
                        "new_item_ids": ["3453331371"],
                        "payment_method_id": "gift_card_1139567",
                    },
                ),
            ],
            "communicate": ["black", "500ml", "stainless steel", "5.03"],
        },
        {
            "id": "rp_mix_size_mismatch_exchange",
            "source": "000000_a",
            "intent": "size_style_mismatch",
            "purpose": "Exchange delivered footwear for a smaller usable size.",
            "order": "#W1075114",
            "item": "4582956489",
            "reason": (
                "The size-12 Hiking Boots in delivered order #W1075114 are too large. "
                "You want the available size-10 synthetic waterproof version, catalog "
                "item 1615379700."
            ),
            "instructions": (
                "Ask the agent to verify the replacement and explain the price "
                "difference. Say yes when asked to confirm the exchange."
            ),
            "actions": [
                ("get_product_details", {"product_id": "7363354090"}),
                ("calculate", {"expression": "253.89 - 241.96"}, []),
                (
                    "exchange_delivered_order_items",
                    {
                        "order_id": "#W1075114",
                        "item_ids": ["4582956489"],
                        "new_item_ids": ["1615379700"],
                        "payment_method_id": "gift_card_5115976",
                    },
                ),
            ],
            "communicate": ["size 10", "synthetic", "waterproof", "11.93"],
        },
        {
            "id": "rp_mix_transit_damage_return",
            "source": "000035_a",
            "intent": "transit_damage",
            "purpose": "Return one item that arrived broken in transit.",
            "order": "#W1519594",
            "item": "9472539378",
            "reason": (
                "The glass Electric Kettle in delivered order #W1519594 arrived "
                "cracked in transit and cannot be used. Return only the kettle and "
                "refund it to the original gift card."
            ),
            "instructions": (
                "State that the item was damaged in transit. Say yes after the agent "
                "explains the return and asks for explicit confirmation."
            ),
            "actions": [
                (
                    "return_delivered_order_items",
                    {
                        "order_id": "#W1519594",
                        "item_ids": ["9472539378"],
                        "payment_method_id": "gift_card_1711656",
                        "reason": "damaged",
                    },
                    ["order_id", "item_ids", "payment_method_id"],
                ),
            ],
            "communicate": ["Electric Kettle", "143.72", "gift_card_1711656"],
        },
        {
            "id": "rp_mix_quality_defect_return",
            "source": "000075_a",
            "intent": "quality_defect",
            "purpose": "Return an electronic item with a reproducible functional defect.",
            "order": "#W1523776",
            "item": "8593894906",
            "reason": (
                "The Smart Thermostat in delivered order #W1523776 repeatedly loses "
                "power and cannot control heating. Return only that defective item and "
                "refund it to the original gift card."
            ),
            "instructions": (
                "Describe the functional defect consistently. Say yes after the agent "
                "explains the return and asks for explicit confirmation."
            ),
            "actions": [
                (
                    "return_delivered_order_items",
                    {
                        "order_id": "#W1523776",
                        "item_ids": ["8593894906"],
                        "payment_method_id": "gift_card_2748512",
                        "reason": "defective",
                    },
                    ["order_id", "item_ids", "payment_method_id"],
                ),
            ],
            "communicate": ["Smart Thermostat", "263.11", "gift_card_2748512"],
        },
        {
            "id": "rp_mix_description_mismatch_return",
            "source": "000009_a",
            "intent": "not_as_described",
            "purpose": "Return a delivered item whose catalog capability is unsuitable.",
            "order": "#W1539823",
            "item": "7597543861",
            "reason": (
                "You expected the Bluetooth Speaker in delivered order #W1539823 to "
                "be water resistant, but catalog item 7597543861 says water resistance "
                "is no. Return only that speaker to the original credit card."
            ),
            "instructions": (
                "Ask the agent to verify the catalog specification. Say yes after the "
                "agent explains the return and asks for explicit confirmation."
            ),
            "actions": [
                (
                    "return_delivered_order_items",
                    {
                        "order_id": "#W1539823",
                        "item_ids": ["7597543861"],
                        "payment_method_id": "credit_card_5869505",
                        "reason": "not as described",
                    },
                    ["order_id", "item_ids", "payment_method_id"],
                ),
            ],
            "communicate": ["310.47", "credit_card_5869505"],
        },
        {
            "id": "rp_mix_cancel_unneeded_pending",
            "source": "000001_a",
            "intent": "cancel_before_fulfillment",
            "purpose": "Cancel a pending order after the customer changes their mind.",
            "order": "#W1046662",
            "item": "4982943126",
            "reason": (
                "You no longer need any of pending order #W1046662 and want the whole "
                "order cancelled before it is processed."
            ),
            "instructions": (
                "Use the reason 'no longer needed'. Do not confirm in the initial "
                "request. Say yes only after the agent explains the cancellation and "
                "refund and asks for explicit confirmation."
            ),
            "actions": [
                (
                    "cancel_pending_order",
                    {"order_id": "#W1046662", "reason": "no longer needed"},
                ),
            ],
            "communicate": ["1151.94", "gift_card_9138722"],
        },
    ]

    tasks = []
    for spec in specs:
        base_actions = [
            auth(db, spec["order"]),
            ("get_order_details", {"order_id": spec["order"]}),
        ]
        tasks.append(
            make_task(
                task_id=spec["id"],
                purpose=spec["purpose"],
                intent=spec["intent"],
                source_task_id=spec["source"],
                reason=spec["reason"],
                known=known_identity(
                    db,
                    spec["order"],
                    f"The affected catalog item is {spec['item']}.",
                ),
                instructions=spec["instructions"],
                actions=base_actions + spec["actions"],
                communicate=spec["communicate"],
            )
        )
    return tasks


def build_audit(
    source_rows: dict[str, dict[str, Any]],
    tasks: list[dict[str, Any]],
    db: dict[str, Any],
) -> None:
    selection_reasons = {
        "shipping_issue.missing": "Concrete whole-item or quantity short-shipment report.",
        "pending_order.add_forgotten_item": (
            "Uses Mix-ECom missing-component language, adapted to a problem caught before fulfillment."
        ),
        "pending_order.change_payment": (
            "Uses realistic pre-fulfillment order-correction language from generic cancellation cases."
        ),
        "transit_damage": "Concrete transit damage with a clearly affected item.",
        "quality_defect": "Specific observable functional or workmanship defect.",
        "not_as_described": "Specific catalog or listing mismatch rather than vague dissatisfaction.",
        "wrong_item": "A specific wrong-color shipment with a clear desired correction.",
        "size_style_mismatch": "A concrete size mismatch with an available replacement variant.",
        "cancel_before_fulfillment": "A realistic changed-mind cancellation before processing.",
    }
    selected = []
    bindings = {}
    for task in tasks:
        notes = task["description"]["notes"]
        source_id = notes.split("eval/", 1)[1].split(";", 1)[0]
        intent = notes.split("intent=", 1)[1].split(";", 1)[0]
        row = source_rows[source_id]
        actions = task["evaluation_criteria"]["actions"]
        order_ids = sorted(
            {
                item["arguments"]["order_id"]
                for item in actions
                if "order_id" in item["arguments"]
            }
        )
        item_ids = sorted(
            {
                value
                for item in actions
                for key, raw in item["arguments"].items()
                if key in {"item_id", "item_ids", "new_item_ids"}
                for value in ([raw] if isinstance(raw, str) else raw)
            }
        )
        selected.append(
            {
                "retail_plus_task_id": task["id"],
                "mix_ecom_split": "eval/after-sales",
                "mix_ecom_task_id": source_id,
                "question_type": row["question_type"],
                "source_first_query": row["first_query"],
                "source_user_profile": row["user_profile"],
                "selection_reason": selection_reasons.get(
                    intent.split(".")[-1], selection_reasons.get(intent, intent)
                ),
                "adapted_reason_for_call": task["user_scenario"]["instructions"][
                    "reason_for_call"
                ],
                "removed_source_assumptions": [
                    "Mix-ECom shop, item, order, logistics, image, video, and coupon entities",
                    "Mix-ECom-specific tools and scripted resolution policy",
                    "Source mood and red-envelope compensation negotiation",
                ],
                "retail_plus_binding": {
                    "order_ids": order_ids,
                    "item_ids": item_ids,
                    "golden_tools": [item["name"] for item in actions],
                },
            }
        )
        bindings[task["id"]] = {
            "order_ids": order_ids,
            "user_ids": sorted(
                {db["orders"][order_id]["user_id"] for order_id in order_ids}
            ),
            "item_ids": item_ids,
            "source_task_id": source_id,
        }
    dump_json(PHASE_DIR / "selected_mixecom_examples.json", selected)
    dump_json(PHASE_DIR / "retail_plus_bindings.json", bindings)


def validate(tasks: list[dict[str, Any]], db: dict[str, Any]) -> None:
    ids = [task["id"] for task in tasks]
    if len(ids) != 18 or len(ids) != len(set(ids)):
        raise ValueError("Mix-ECom phase 1 must contain exactly 18 unique tasks")
    counts = Counter(
        item["name"]
        for task in tasks
        for item in task["evaluation_criteria"]["actions"]
    )
    minimums = {
        "get_item_details": 3,
        "add_pending_order_items": 3,
        "file_missing_item_claim": 3,
        "modify_pending_order_payment": 3,
        "waive_order_fee": 3,
    }
    for tool_name, minimum in minimums.items():
        if counts[tool_name] < minimum:
            raise ValueError(f"Insufficient {tool_name} coverage: {counts[tool_name]}")
    for task in tasks:
        for golden in task["evaluation_criteria"]["actions"]:
            args = golden["arguments"]
            if "order_id" in args and args["order_id"] not in db["orders"]:
                raise ValueError(f"Unknown order in {task['id']}: {args['order_id']}")
            for key in ("item_id", "item_ids", "new_item_ids"):
                if key not in args:
                    continue
                values = [args[key]] if isinstance(args[key], str) else args[key]
                for item_id in values:
                    catalog_item(db, item_id)


def main() -> None:
    db = load_json(DB_PATH)
    tasks = load_json(TASKS_PATH)
    splits = load_json(SPLITS_PATH)
    source_rows, source_metadata = load_source_material()

    fee_specs = add_fee_entities(db)
    new_tasks = (
        build_missing_tasks(db)
        + build_add_item_tasks(db)
        + build_payment_tasks(db)
        + build_fee_tasks(db, fee_specs)
        + build_core_after_sales_tasks(db)
    )
    validate(new_tasks, db)

    new_ids = [task["id"] for task in new_tasks]
    tasks = [task for task in tasks if not task["id"].startswith(TASK_PREFIX)]
    tasks.extend(new_tasks)
    splits["mixecom_phase1"] = new_ids
    splits["all_plus"] = (
        list(splits["base_plus"])
        + list(splits.get("policy_phase1", []))
        + new_ids
    )

    dump_json(DB_PATH, db)
    dump_json(TASKS_PATH, tasks)
    dump_json(SPLITS_PATH, splits)
    build_audit(source_rows, new_tasks, db)
    dump_json(
        PHASE_DIR / "mixecom_source_manifest.json",
        {
            "dataset": "zhourax977/Mix-ECom",
            "url": SOURCE_URL,
            "license": "CC-BY-4.0",
            "source_split": "eval/after-sales",
            "source_file": str(SOURCE_PATH.relative_to(ROOT)).replace("\\", "/"),
            **source_metadata,
            "selected_source_ids": sorted(
                {
                    item["description"]["notes"].split("eval/", 1)[1].split(";", 1)[0]
                    for item in new_tasks
                }
            ),
            "build_script": str(Path(__file__).relative_to(ROOT)).replace("\\", "/"),
        },
    )
    counts = Counter(
        item["name"]
        for task in new_tasks
        for item in task["evaluation_criteria"]["actions"]
    )
    print(f"Wrote {len(new_tasks)} Mix-ECom phase-1 tasks; total tasks={len(tasks)}")
    for tool_name in [
        "get_item_details",
        "add_pending_order_items",
        "file_missing_item_claim",
        "modify_pending_order_payment",
        "waive_order_fee",
    ]:
        print(f"{tool_name}: +{counts[tool_name]} golden calls")


if __name__ == "__main__":
    main()

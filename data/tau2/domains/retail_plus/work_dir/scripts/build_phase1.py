"""Build the first 16 ABCD-inspired Retail Plus tasks deterministically.

Inputs are immutable Retail data plus the locally saved ABCD shortlist. Outputs
are the Retail Plus DB/tasks/splits and auditable provenance files in work_dir.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
from pathlib import Path


ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file()
)
WORK_DIR = Path(__file__).resolve().parents[1]
PHASE1_DIR = WORK_DIR / "phase1"
SOURCE_DIR = WORK_DIR / "sources" / "abcd_v1_1"
BASE_DIR = ROOT / "data" / "tau2" / "domains" / "retail"
PLUS_DIR = ROOT / "data" / "tau2" / "domains" / "retail_plus"
SEED = 20260824
SOURCE_COMMIT = "6b8700ce67c6b37b062dd7a60abc76d7ef832a97"


SELECTIONS = {
    "refund_status": {"normal": 8102, "edge": 8354},
    "mistimed_billing_already_returned": {"normal": 431, "edge": 4761},
    "promo_code_invalid": {"normal": 3631, "edge": 6233},
    "promo_code_out_of_date": {"normal": 6965, "edge": 6395},
    "status_mystery_fee": {"normal": 9816, "edge": 6178},
    "missing": {"normal": 2343, "edge": 1403},
    "manage_create": {"normal": 8819, "edge": 7317},
    "manage_change_phone": {"normal": 1941, "edge": 405},
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=4) + "\n", encoding="utf-8"
    )


def order_total(order: dict) -> float:
    return round(sum(item["price"] for item in order["items"]), 2)


def action(task_id: str, index: int, name: str, arguments: dict) -> dict:
    return {
        "action_id": f"{task_id}_{index}",
        "name": name,
        "arguments": arguments,
        "info": None,
    }


def make_task(
    task_id: str,
    purpose: str,
    policy: str,
    persona: str,
    reason: str,
    known: str,
    unknown: str,
    instructions: str,
    actions: list[tuple[str, dict]],
    communicate: list[str],
    source_intent: str,
    source_convo_id: int,
) -> dict:
    return {
        "id": task_id,
        "description": {
            "purpose": purpose,
            "relevant_policies": policy,
            "notes": f"ABCD test/{source_convo_id}; canonical intent={source_intent}; adapted for Retail Plus.",
        },
        "user_scenario": {
            "persona": persona,
            "instructions": {
                "domain": "retail_plus",
                "reason_for_call": reason,
                "known_info": known,
                "unknown_info": unknown,
                "task_instructions": instructions,
            },
        },
        "initial_state": None,
        "evaluation_criteria": {
            "actions": [
                action(task_id, index, name, arguments)
                for index, (name, arguments) in enumerate(actions)
            ],
            "communicate_info": communicate,
            "nl_assertions": None,
            "reward_basis": ["DB", "ACTION", "COMMUNICATE"],
        },
    }


def identity(db: dict, user_id: str) -> dict:
    user = db["users"][user_id]
    return {
        "user_id": user_id,
        "first_name": user["name"]["first_name"],
        "last_name": user["name"]["last_name"],
        "email": user["email"],
        "zip": user["address"]["zip"],
    }


def main() -> None:
    rng = random.Random(SEED)
    db = load(BASE_DIR / "db.json")
    base_tasks = load(BASE_DIR / "tasks.json")
    splits = load(BASE_DIR / "split_tasks.json")
    shortlist = load(PHASE1_DIR / "abcd_candidate_shortlist.json")

    used_orders = {
        action_item["arguments"]["order_id"]
        for task in base_tasks
        for action_item in task.get("evaluation_criteria", {}).get("actions", [])
        if "order_id" in action_item.get("arguments", {})
    }
    candidates = [
        order
        for order_id, order in db["orders"].items()
        if order_id not in used_orders
    ]
    rng.shuffle(candidates)
    reserved_orders: set[str] = set()

    def choose_order(status: str, minimum: float = 0.0, maximum: float = 10**9):
        for order in candidates:
            if (
                order["order_id"] not in reserved_orders
                and order["status"] == status
                and minimum <= order_total(order) < maximum
            ):
                reserved_orders.add(order["order_id"])
                return order
        raise RuntimeError(f"No unused {status} order in [{minimum}, {maximum})")

    bindings = {
        "refund_status_normal": choose_order("delivered", maximum=500),
        "refund_status_edge": choose_order("delivered", maximum=500),
        "delayed_refund_normal": choose_order("delivered", maximum=500),
        "delayed_refund_edge": choose_order("delivered", minimum=500),
        "voucher_invalid_normal": choose_order("pending", maximum=500),
        "voucher_invalid_edge": choose_order("pending", maximum=500),
        "voucher_expired_normal": choose_order("pending", maximum=500),
        "voucher_expired_edge": choose_order("pending", maximum=500),
        "fee_normal": choose_order("pending", maximum=500),
        "fee_edge": choose_order("pending", minimum=500),
        "missing_normal": choose_order("delivered", maximum=500),
        "missing_edge": choose_order("delivered", minimum=500),
        "add_normal": choose_order("pending", maximum=500),
        "add_edge": choose_order("processed"),
    }

    # Existing users are reused, while all selected orders are excluded from the
    # original 114 tasks. Phone users are also chosen from unused-order owners.
    bound_user_ids = {order["user_id"] for order in bindings.values()}
    remaining_users = sorted(set(db["users"]) - bound_user_ids)
    rng.shuffle(remaining_users)
    phone_normal_user, phone_edge_user, foreign_voucher_user, policy_user = remaining_users[:4]

    db.update(
        {
            "customer_phones": {},
            "refund_cases": {},
            "vouchers": {},
            "order_fees": {},
            "shipping_claims": {},
            "support_cases": {},
            "item_return_policies": {},
        }
    )

    # Four pre-existing refund cases support status and delayed-credit scenarios.
    refund_specs = [
        ("refund_status_normal", "REF-PLUS-1001", "processing", "2026-08-18", "2026-08-29", None),
        ("refund_status_edge", "REF-PLUS-1002", "failed", "2026-08-15", "2026-08-22", "payment processor rejected the refund"),
        ("delayed_refund_normal", "REF-PLUS-1003", "processing", "2026-08-22", "2026-08-29", None),
        ("delayed_refund_edge", "REF-PLUS-1004", "processing", "2026-08-01", "2026-08-10", None),
    ]
    for key, refund_id, status, requested_at, expected_by, failure_reason in refund_specs:
        order = bindings[key]
        item_ids = [item["item_id"] for item in order["items"]]
        amount = order_total(order)
        order["status"] = "return requested"
        order["return_items"] = sorted(item_ids)
        order["return_payment_method_id"] = order["payment_history"][0]["payment_method_id"]
        db["refund_cases"][refund_id] = {
            "refund_id": refund_id,
            "order_id": order["order_id"],
            "user_id": order["user_id"],
            "item_ids": sorted(item_ids),
            "amount": amount,
            "payment_method_id": order["payment_history"][0]["payment_method_id"],
            "status": status,
            "requested_at": requested_at,
            "expected_by": expected_by,
            "completed_at": None,
            "failure_reason": failure_reason,
            "source": "item_return",
        }

    voucher_codes = {
        "invalid_normal": "PLUS-MINIMUM-20",
        "invalid_edge": "PLUS-PRIVATE-30",
        "expired_normal": "PLUS-EXPIRED-10",
        "expired_edge": "PLUS-EXPIRED-25",
    }
    normal_order = bindings["voucher_invalid_normal"]
    db["vouchers"][voucher_codes["invalid_normal"]] = {
        "code": voucher_codes["invalid_normal"], "status": "active",
        "discount_type": "percentage", "discount_value": 20.0,
        "issued_at": "2026-08-20", "expires_at": "2026-09-20",
        "assigned_user_id": normal_order["user_id"],
        "minimum_order_amount": round(order_total(normal_order) + 100.0, 2),
        "cash_redeemable": False, "redeemed_order_id": None,
    }
    db["vouchers"][voucher_codes["invalid_edge"]] = {
        "code": voucher_codes["invalid_edge"], "status": "active",
        "discount_type": "fixed", "discount_value": 30.0,
        "issued_at": "2026-08-18", "expires_at": "2026-09-18",
        "assigned_user_id": foreign_voucher_user, "minimum_order_amount": 0.0,
        "cash_redeemable": False, "redeemed_order_id": None,
    }
    for key, code, value, expired_at in [
        ("voucher_expired_normal", voucher_codes["expired_normal"], 10.0, "2026-08-10"),
        ("voucher_expired_edge", voucher_codes["expired_edge"], 25.0, "2026-08-01"),
    ]:
        order = bindings[key]
        db["vouchers"][code] = {
            "code": code, "status": "expired", "discount_type": "fixed",
            "discount_value": value, "issued_at": "2026-07-01",
            "expires_at": expired_at, "assigned_user_id": order["user_id"],
            "minimum_order_amount": 0.0, "cash_redeemable": False,
            "redeemed_order_id": None,
        }

    db["order_fees"] = {
        "FEE-PLUS-25": {
            "fee_id": "FEE-PLUS-25", "order_id": bindings["fee_normal"]["order_id"],
            "fee_type": "incorrect handling fee", "amount": 25.0,
            "explanation": "A handling fee was incorrectly applied by the warehouse.",
            "status": "charged", "waivable": True, "refund_id": None,
        },
        "FEE-PLUS-650": {
            "fee_id": "FEE-PLUS-650", "order_id": bindings["fee_edge"]["order_id"],
            "fee_type": "high-value customs adjustment", "amount": 650.0,
            "explanation": "The fee requires manual documentation review.",
            "status": "charged", "waivable": False, "refund_id": None,
        },
    }

    db["customer_phones"][phone_normal_user] = "(609) 646-7423"
    db["customer_phones"][phone_edge_user] = "(255) 860-9230"

    # Add two dedicated restricted products/orders so the new return rules are
    # represented in the executable DB without affecting the original tasks.
    policy_identity = identity(db, policy_user)
    policy_payment = next(iter(db["users"][policy_user]["payment_methods"]))
    restricted = [
        ("9900000001", "9900001001", "Gourmet Snack Box", "food", 39.0, "#RP000001"),
        ("9900000002", "9900001002", "Custom Engraved Mug", "customized", 49.0, "#RP000002"),
    ]
    for product_id, item_id, name, category, price, order_id in restricted:
        db["products"][product_id] = {
            "name": name, "product_id": product_id,
            "variants": {item_id: {"item_id": item_id, "options": {"category": category}, "available": True, "price": price}},
        }
        db["orders"][order_id] = {
            "order_id": order_id, "user_id": policy_user,
            "address": copy.deepcopy(db["users"][policy_user]["address"]),
            "items": [{"name": name, "product_id": product_id, "item_id": item_id, "price": price, "options": {"category": category}}],
            "status": "delivered", "fulfillments": [{"tracking_id": [f"RP{item_id}"], "item_ids": [item_id]}],
            "payment_history": [{"transaction_type": "payment", "amount": price, "payment_method_id": policy_payment}],
            "cancel_reason": None, "exchange_items": None, "exchange_new_items": None,
            "exchange_payment_method_id": None, "exchange_price_difference": None,
            "return_items": None, "return_payment_method_id": None,
        }
        db["users"][policy_user]["orders"].append(order_id)
        db["item_return_policies"][item_id] = {
            "item_id": item_id, "category": category,
            "returnable_without_reason": False,
            "allowed_reasons": ["defective", "damaged", "wrong item"],
        }

    # Choose an available add-on that is not already in either manage-create order.
    def choose_add_item(order: dict):
        present = {item["item_id"] for item in order["items"]}
        options = []
        for product in db["products"].values():
            for variant in product["variants"].values():
                if variant["available"] and variant["item_id"] not in present and variant["price"] < 200:
                    options.append((product, variant))
        options.sort(key=lambda pair: (pair[0]["name"], pair[1]["item_id"]))
        return options[rng.randrange(len(options))]

    add_product, add_variant = choose_add_item(bindings["add_normal"])
    edge_product, edge_variant = choose_add_item(bindings["add_edge"])

    def non_gift_payment(order: dict) -> str:
        user = db["users"][order["user_id"]]
        for method_id in user["payment_methods"]:
            if not method_id.startswith("gift_card"):
                return method_id
        return next(iter(user["payment_methods"]))

    bind_out = {
        key: {
            "order_id": order["order_id"], "user_id": order["user_id"],
            "order_status": order["status"], "order_total": order_total(order),
            "item_ids": [item["item_id"] for item in order["items"]],
        }
        for key, order in bindings.items()
    }
    bind_out.update({
        "phone_normal": identity(db, phone_normal_user),
        "phone_edge": identity(db, phone_edge_user),
        "restricted_policy_user": policy_identity,
        "add_normal_target": {"product_id": add_product["product_id"], "product_name": add_product["name"], "item_id": add_variant["item_id"], "price": add_variant["price"]},
        "add_edge_target": {"product_id": edge_product["product_id"], "product_name": edge_product["name"], "item_id": edge_variant["item_id"], "price": edge_variant["price"]},
        "voucher_codes": voucher_codes,
    })

    tasks = build_tasks(db, bindings, bind_out)
    task_ids = [task["id"] for task in tasks]
    merged_tasks = [task for task in base_tasks if not task["id"].startswith("rp_abcd_")] + tasks
    splits["abcd_phase1"] = task_ids
    splits["base_plus"] = list(splits["base"]) + task_ids

    dump(PLUS_DIR / "db.json", db)
    dump(PLUS_DIR / "tasks.json", merged_tasks)
    dump(PLUS_DIR / "split_tasks.json", splits)
    dump(PHASE1_DIR / "retail_plus_bindings.json", bind_out)
    build_audit(shortlist, tasks, bind_out)
    build_manifest()
    print(f"Wrote {len(tasks)} phase-1 tasks; total Retail Plus tasks={len(merged_tasks)}")


def auth_action(user: dict) -> tuple[str, dict]:
    return "find_user_id_by_email", {"email": user["email"]}


def build_tasks(db: dict, b: dict, out: dict) -> list[dict]:
    tasks = []
    common_persona = "You are a realistic ecommerce customer. Reveal account information only when the agent asks for it."

    def user_for(key: str):
        return identity(db, b[key]["user_id"])

    def known(user: dict, extra: str) -> str:
        return f"You are {user['first_name']} {user['last_name']}. Your email is {user['email']}. {extra}"

    # 1-2: refund status
    for variant, key, refund_id, source_id in [
        ("normal", "refund_status_normal", "REF-PLUS-1001", 8102),
        ("edge", "refund_status_edge", "REF-PLUS-1002", 8354),
    ]:
        user = user_for(key)
        case = db["refund_cases"][refund_id]
        task_id = f"rp_abcd_refund_status_{variant}"
        actions = [auth_action(user), ("get_refund_status", {"refund_id": refund_id})]
        communicate = [refund_id, case["status"]]
        if variant == "normal":
            communicate += [case["expected_by"]]
            instructions = "Ask when the refund will arrive and which payment method it uses. Finish once the status and expected date are clear."
        else:
            summary = f"Failed refund {refund_id} for order {case['order_id']}"
            actions += [("open_support_case", {"case_type": "delayed_refund", "reference_id": refund_id, "summary": summary}), ("transfer_to_human_agents", {"summary": summary})]
            communicate += ["payment processor rejected the refund", "YOU ARE BEING TRANSFERRED TO A HUMAN AGENT. PLEASE HOLD ON."]
            instructions = "You have waited a long time. Ask for human help if the refund failed; accept the transfer after the agent explains the failure."
        tasks.append(make_task(task_id, "Check an existing refund status.", "Refund cases; failed refunds require support and human transfer.", common_persona, f"You want the status of refund {refund_id} for order {case['order_id']}.", known(user, f"You know refund ID {refund_id} and order ID {case['order_id']}.") , "You do not know its internal status, expected date, or failure reason.", instructions, actions, communicate, "refund_status", source_id))

    # 3-4: returned but credit missing
    for variant, key, refund_id, source_id in [
        ("normal", "delayed_refund_normal", "REF-PLUS-1003", 431),
        ("edge", "delayed_refund_edge", "REF-PLUS-1004", 4761),
    ]:
        user = user_for(key)
        case = db["refund_cases"][refund_id]
        task_id = f"rp_abcd_mistimed_billing_{variant}"
        actions = [auth_action(user), ("review_returned_item_billing", {"refund_id": refund_id})]
        if variant == "normal":
            communicate = ["processing", case["expected_by"], "duplicate refund"]
            instructions = "Say the returned items were accepted but the credit is not visible. Ask whether another refund should be created. Accept the explanation that the existing refund is still within its expected window and must not be duplicated."
        else:
            summary = f"Overdue high-value returned-item refund {refund_id}"
            actions += [("open_support_case", {"case_type": "delayed_refund", "reference_id": refund_id, "summary": summary}), ("transfer_to_human_agents", {"summary": summary})]
            communicate = ["overdue", "duplicate refund", "YOU ARE BEING TRANSFERRED TO A HUMAN AGENT. PLEASE HOLD ON."]
            instructions = "Explain that the return credit is overdue and insist on resolution. Do not ask for a second refund after the agent explains duplicate refunds are prohibited; accept human transfer."
        tasks.append(make_task(task_id, "Resolve a missing credit after a completed return.", "Never duplicate a refund; overdue or high-value cases require support and transfer.", common_persona, f"You returned all items from order {case['order_id']}, but refund {refund_id} is not visible.", known(user, f"You know order ID {case['order_id']} and refund ID {refund_id}."), "You do not know whether the case is overdue or requires manual review.", instructions, actions, communicate, "mistimed_billing_already_returned", source_id))

    # 5-8: voucher invalid/expired
    voucher_tasks = [
        ("rp_abcd_promo_invalid_normal", "voucher_invalid_normal", "invalid_normal", "promo_code_invalid", 3631, ["minimum order amount"], "Ask why the voucher fails. Accept that this order does not meet the minimum amount."),
        ("rp_abcd_promo_invalid_edge", "voucher_invalid_edge", "invalid_edge", "promo_code_invalid", 6233, ["invalid for this account", "cannot be redeemed for cash"], "After learning the code is invalid for your account, ask to receive its value as cash. Accept the refusal."),
        ("rp_abcd_promo_expired_normal", "voucher_expired_normal", "expired_normal", "promo_code_out_of_date", 6965, ["expired", "2026-08-10"], "Ask why the voucher fails and when it expired. Accept the expiration explanation."),
        ("rp_abcd_promo_expired_edge", "voucher_expired_edge", "expired_edge", "promo_code_out_of_date", 6395, ["expired", "cannot be redeemed for cash"], "After learning it expired, ask for its value in cash. Accept the refusal because vouchers are never cash redeemable."),
    ]
    for task_id, key, code_key, intent, source_id, communicate, instructions in voucher_tasks:
        user = user_for(key)
        order = b[key]
        code = out["voucher_codes"][code_key]
        tasks.append(make_task(task_id, "Diagnose a voucher that cannot be used.", "Vouchers are account/order constrained and can never be redeemed for cash.", common_persona, f"Voucher {code} does not work on pending order {order['order_id']}.", known(user, f"You know voucher code {code} and order ID {order['order_id']}."), "You do not know the voucher's validation reason.", instructions, [auth_action(user), ("validate_voucher", {"code": code, "order_id": order["order_id"]})], communicate, intent, source_id))

    # 9-10: mystery fee
    for variant, key, fee_id, source_id in [
        ("normal", "fee_normal", "FEE-PLUS-25", 9816),
        ("edge", "fee_edge", "FEE-PLUS-650", 6178),
    ]:
        user = user_for(key)
        order = b[key]
        task_id = f"rp_abcd_mystery_fee_{variant}"
        actions = [auth_action(user), ("get_order_fee_details", {"fee_id": fee_id})]
        if variant == "normal":
            actions += [("waive_order_fee", {"fee_id": fee_id})]
            communicate = [fee_id, "waived", f"REF-FEE-{fee_id}"]
            instructions = "Ask what the fee is and request removal. Confirm after the agent explains the $25 waiver and refund destination."
        else:
            summary = f"High-value fee {fee_id} on order {order['order_id']}"
            actions += [("open_support_case", {"case_type": "high_value_fee", "reference_id": fee_id, "summary": summary}), ("transfer_to_human_agents", {"summary": summary})]
            communicate = ["500.00", "YOU ARE BEING TRANSFERRED TO A HUMAN AGENT. PLEASE HOLD ON."]
            instructions = "Challenge the large fee. Once told it exceeds the manual-review threshold, accept the support case and human transfer."
        tasks.append(make_task(task_id, "Explain and resolve a disputed order fee.", "Small eligible fees may be waived; $500 or more requires human review.", common_persona, f"Order {order['order_id']} has unfamiliar fee {fee_id}.", known(user, f"You know order ID {order['order_id']} and fee ID {fee_id}."), "You do not know the fee reason or whether it can be waived.", instructions, actions, communicate, "status_mystery_fee", source_id))

    # 11-12: missing shipment/item
    for variant, key, source_id in [("normal", "missing_normal", 2343), ("edge", "missing_edge", 1403)]:
        user = user_for(key)
        order = b[key]
        item_ids = [item["item_id"] for item in order["items"]]
        task_id = f"rp_abcd_shipping_missing_{variant}"
        actions = [auth_action(user), ("assess_missing_item_claim", {"order_id": order["order_id"], "item_ids": item_ids})]
        if variant == "normal":
            actions += [("file_missing_item_claim", {"order_id": order["order_id"], "item_ids": item_ids, "requested_resolution": "replacement"})]
            claim_id = f"CLAIM-{order['order_id'].replace('#','')}"
            communicate = [claim_id, "replacement requested"]
            instructions = "Report that every item in the delivered order is missing and ask for replacement. Confirm after the agent lists the items and claim details."
        else:
            summary = f"High-value missing items for order {order['order_id']}"
            actions += [("open_support_case", {"case_type": "high_value_missing_item", "reference_id": order["order_id"], "summary": summary}), ("transfer_to_human_agents", {"summary": summary})]
            communicate = ["500.00", "YOU ARE BEING TRANSFERRED TO A HUMAN AGENT. PLEASE HOLD ON."]
            instructions = "Report all items missing and become impatient, but accept human transfer when the agent explains the high-value threshold."
        tasks.append(make_task(task_id, "Resolve missing items in a delivered order.", "One claim per order; claims of $500 or more require human transfer.", common_persona, f"No items from delivered order {order['order_id']} arrived.", known(user, f"You know order ID {order['order_id']}."), "You do not know the internal item IDs, total affected amount, or claim ID.", instructions, actions, communicate, "shipping_issue.missing", source_id))

    # 13-14: add item to order
    for variant, key, target_key, source_id in [
        ("normal", "add_normal", "add_normal_target", 8819),
        ("edge", "add_edge", "add_edge_target", 7317),
    ]:
        user = user_for(key)
        order = b[key]
        target = out[target_key]
        task_id = f"rp_abcd_manage_create_{variant}"
        actions = [auth_action(user), ("get_order_details", {"order_id": order["order_id"]})]
        if variant == "normal":
            payment = non_gift_payment_from_db(db, order)
            actions += [("get_product_details", {"product_id": target["product_id"]}), ("add_pending_order_items", {"order_id": order["order_id"], "item_ids": [target["item_id"]], "payment_method_id": payment})]
            communicate = [target["product_name"], str(target["price"])]
            instructions = "Ask to add the named product. Do not confirm until the agent states the exact product, price, payment method, and that the order is pending. Then explicitly confirm."
        else:
            communicate = ["processed", "separate order"]
            instructions = "Ask to add the named product. If the order has already been processed, ask once whether an exception is possible, then accept that a separate order is required."
        tasks.append(make_task(task_id, "Add an item to an existing order or enforce the status boundary.", "Only exactly-pending orders accept added items.", common_persona, f"You forgot to add {target['product_name']} to order {order['order_id']}.", known(user, f"You know order ID {order['order_id']} and product name {target['product_name']}."), "You do not know the internal product/item IDs or whether the order can still be changed.", instructions, actions, communicate, "manage_create", source_id))

    # 15-16: change phone / privacy boundary
    normal = out["phone_normal"]
    tasks.append(make_task("rp_abcd_change_phone_normal", "Change the authenticated customer's phone.", "Ten digits, explicit confirmation, authenticated customer only.", common_persona, "Your saved phone number is off by one digit and should be changed to (609) 646-7433.", known(normal, "You know the old and new phone numbers."), "You do not know whether the account currently contains the old number.", "Ask the agent to read the current number. Correct it, then explicitly confirm only after the normalized new number is read back.", [auth_action(normal), ("get_user_phone", {"user_id": normal["user_id"]}), ("modify_user_phone", {"user_id": normal["user_id"], "new_phone": "(609) 646-7433"})], ["(609) 646-7433"], "manage_change_phone", 1941))
    edge = out["phone_edge"]
    tasks.append(make_task("rp_abcd_change_phone_privacy_edge", "Refuse an attempt to change another person's phone.", "Never access or modify another customer's account.", "You are persistent and initially treat a family member's account as if you were authorized, but you accept a clear privacy refusal.", "After authenticating your own account, ask the agent to change your spouse's phone number instead.", known(edge, "You only have your own authentication information. You do not have authorization for your spouse's account."), "You do not know your spouse's user ID or authentication details.", "Authenticate as yourself. Then ask to change the other person's phone to (255) 860-9231. Do not claim that it is your own number. Accept the privacy refusal and stop.", [auth_action(edge)], ["another customer's account"], "manage_change_phone", 405))
    return tasks


def non_gift_payment_from_db(db: dict, order: dict) -> str:
    methods = db["users"][order["user_id"]]["payment_methods"]
    for method_id in methods:
        if not method_id.startswith("gift_card"):
            return method_id
    return next(iter(methods))


def build_audit(shortlist: dict, tasks: list[dict], bindings: dict) -> None:
    by_id = {
        item["convo_id"]: item
        for items in shortlist["intents"].values()
        for item in items
    }
    records = []
    for task in tasks:
        note = task["description"]["notes"]
        source_id = int(note.split("test/")[1].split(";")[0])
        intent = note.split("canonical intent=")[1].split(";")[0]
        variant = "edge" if task["id"].endswith("edge") or "privacy_edge" in task["id"] else "normal"
        source = by_id[source_id]
        removed = [
            "ABCD account IDs, usernames, order IDs, brands, membership levels, and button actions",
            "subjective human-agent success survey",
        ]
        if intent in {"refund_status", "mistimed_billing_already_returned"}:
            removed.append("ABCD's unstructured refund timing; replaced with executable RefundCase dates and status")
        if intent.startswith("promo_code"):
            removed.append("ABCD's seven-day promo convention and replacement-code behavior; replaced with per-voucher state")
        if intent == "status_mystery_fee":
            removed.append("ABCD membership-based fee handling; replaced with fee eligibility and amount thresholds")
        if intent == "shipping_issue.missing":
            removed.append("ABCD free-form shipping resolution; replaced with value-assessed ShippingClaim")
        if intent == "manage_create":
            removed.append("ABCD membership gating; replaced with exact pending-order status and payment checks")
        if intent == "manage_change_phone":
            removed.append("ABCD PIN/account-ID fields; replaced with Retail email authentication and privacy boundary")
        records.append({
            "task_id": task["id"],
            "path_type": variant,
            "abcd_split": "test",
            "abcd_convo_id": source_id,
            "abcd_flow": source["flow"],
            "abcd_intent": source["canonical_intent"],
            "retail_plus_intent": intent,
            "selection_reason": "Fixed-seed shortlist; complete moderate-length dialogue with natural user language and a branch suitable for the assigned normal/edge path.",
            "source_customer_utterances": source["customer_utterances"],
            "rewritten_content": task["user_scenario"],
            "removed_or_replaced_abcd_rules": removed,
            "bound_retail_entities": {
                "gold_actions": task["evaluation_criteria"]["actions"],
                "source_binding_file": "retail_plus_bindings.json",
            },
        })
    dump(
        PHASE1_DIR / "selected_abcd_examples.json",
        {"seed": SEED, "records": records},
    )


def build_manifest() -> None:
    files = {}
    for path in sorted(SOURCE_DIR.iterdir()):
        if path.is_file():
            files[path.name] = {
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    dump(PHASE1_DIR / "abcd_source_manifest.json", {
        "source_repository": "https://github.com/asappresearch/abcd",
        "source_commit": SOURCE_COMMIT,
        "dataset_version": "v1.1",
        "selection_seed": SEED,
        "files": files,
    })


if __name__ == "__main__":
    main()

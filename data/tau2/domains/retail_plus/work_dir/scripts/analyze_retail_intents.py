"""Build auditable intent labels for the original 114-task Retail baseline.

This script is intentionally deterministic. It does not call an LLM and it does
not modify the original retail domain. Labels are inferred from golden actions
and narrowly scoped phrases in the user goal; reviewers can override them in a
later curation stage without losing the original evidence.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path


ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file()
)
WORK_DIR = Path(__file__).resolve().parents[1]
TASKS_PATH = ROOT / "data" / "tau2" / "domains" / "retail" / "tasks.json"
OUTPUT_PATH = WORK_DIR / "analysis" / "intent_labels.json"


ACTION_INTENTS = {
    "exchange_delivered_order_items": "delivered_item_exchange",
    "return_delivered_order_items": "delivered_item_return",
    "cancel_pending_order": "pending_order_cancellation",
    "modify_pending_order_items": "pending_order_item_modification",
    "modify_pending_order_address": "pending_order_address_modification",
    "modify_pending_order_payment": "pending_order_payment_modification",
    "modify_user_address": "customer_default_address_modification",
    "transfer_to_human_agents": "human_handoff",
}


# These are end-user information needs, not incidental read calls needed to
# complete a mutation. Patterns are deliberately conservative.
TEXT_INTENTS = {
    "shipment_tracking_or_arrival_query": [
        r"tracking (?:number|id)",
        r"when .* arriv",
        r"delivery time",
        r"still in transit",
        r"status difference",
    ],
    "order_payment_method_query": [
        r"whether you used your (?:visa|mastercard|amex)",
        r"payment method.*(?:used|paid)",
        r"how much you paid",
    ],
    "order_item_or_quantity_query": [
        r"which .* (?:ordered|order)",
        r"how many .* (?:ordered|order)",
        r"list (?:them|the items|all items)",
        r"what .* (?:in|from) (?:the|your) order",
    ],
    "product_catalog_or_availability_query": [
        r"how many .* options",
        r"all available .* options",
        r"cheapest available",
        r"any .* available",
        r"available .* less than",
        r"what is the cheapest",
        r"maximum .* available",
    ],
    "price_refund_or_difference_calculation": [
        r"total amount",
        r"how much .* (?:back|refund|pay|paid|cost)",
        r"price difference",
        r"total price",
        r"refund .* amount",
        r"amount .* refund",
    ],
    "gift_card_balance_query": [r"gift card balance", r"balance does your gift card"],
    "order_status_query": [
        r"order status",
        r"status .* order",
        r"if .* (?:shipped|delivered|pending)",
        r"has not been shipped",
    ],
}


INTERACTION_TRAITS = {
    "changes_mind_or_conditional_confirmation": [
        r"change your mind",
        r"regret",
        r"if the agent asks (?:you )?for confirmation",
        r"when the agent asks for confirmation",
        r"only if",
        r"if and only if",
    ],
    "fallback_preference": [
        r"if .* not (?:possible|available)",
        r"otherwise",
        r"prefer .* over",
        r"prefer .* >",
        r"if several options",
        r"if multiple .* available",
    ],
    "withholds_or_confuses_information": [
        r"do not reveal",
        r"don't remember",
        r"do not remember",
        r"forgot",
        r"do not want to reveal",
        r"doesn't work",
        r"might confuse",
    ],
    "adversarial_or_emotional_user": [
        r"angry",
        r"swear",
        r"insist",
        r"bad mood",
        r"frustrated",
        r"in a rush",
    ],
    "off_topic_or_distracting_request": [r"famous poem", r"unrelated"],
}


# Requests that appear in the scenario but are intentionally denied or routed
# to a fallback because current Retail tools cannot complete them. Keeping them
# separate prevents the golden fallback action from hiding a coverage gap.
UNSUPPORTED_REQUEST_INTENTS = {
    "partial_line_item_cancellation": [
        r"cancel (?:just|only) (?:the )?(?:item|[a-z -]+) (?:in|from)",
        r"remove .* from (?:the|your) pending order",
        r"cancel partial items",
        r"cancel that item",
    ],
    "cross_product_replacement": [
        r"exchange the bookshelf .* for a camera",
        r"change .* for a coat",
        r"change .* product type",
    ],
    "add_item_to_existing_order": [
        r"add .* to (?:the|your) order",
    ],
    "split_payment": [r"split the payment"],
    "undo_order_cancellation": [r"undo cancelling", r"undo the cancellation"],
}


def matches(text: str, patterns: list[str]) -> list[str]:
    return [pattern for pattern in patterns if re.search(pattern, text, re.I)]


def main() -> None:
    tasks = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    labels = []

    for task in tasks:
        instructions = task["user_scenario"]["instructions"]
        reason = instructions.get("reason_for_call", "")
        action_names = [
            action["name"]
            for action in task.get("evaluation_criteria", {}).get("actions", [])
        ]

        operation_intents = []
        for action_name in action_names:
            intent = ACTION_INTENTS.get(action_name)
            if intent and intent not in operation_intents:
                operation_intents.append(intent)

        information_intents = []
        information_evidence = {}
        for intent, patterns in TEXT_INTENTS.items():
            found = matches(reason, patterns)
            if found:
                information_intents.append(intent)
                information_evidence[intent] = found

        interaction_traits = []
        interaction_evidence = {}
        for trait, patterns in INTERACTION_TRAITS.items():
            found = matches(reason, patterns)
            if found:
                interaction_traits.append(trait)
                interaction_evidence[trait] = found

        unsupported_request_intents = []
        unsupported_request_evidence = {}
        for intent, patterns in UNSUPPORTED_REQUEST_INTENTS.items():
            found = matches(reason, patterns)
            if found:
                unsupported_request_intents.append(intent)
                unsupported_request_evidence[intent] = found

        all_intents = operation_intents + information_intents + unsupported_request_intents
        if not all_intents:
            all_intents = ["no_completed_business_operation"]

        labels.append(
            {
                "task_id": task["id"],
                "primary_intent": all_intents[0],
                "operation_intents": operation_intents,
                "information_intents": information_intents,
                "interaction_traits": interaction_traits,
                "requested_but_unsupported_intents": unsupported_request_intents,
                "is_multi_intent": len(all_intents) > 1,
                "evidence": {
                    "gold_action_names": action_names,
                    "information_pattern_matches": information_evidence,
                    "interaction_pattern_matches": interaction_evidence,
                    "unsupported_request_pattern_matches": unsupported_request_evidence,
                },
                "review_status": "machine_labeled_pending_human_review",
            }
        )

    operation_counts = Counter(
        intent for item in labels for intent in item["operation_intents"]
    )
    information_counts = Counter(
        intent for item in labels for intent in item["information_intents"]
    )
    trait_counts = Counter(
        trait for item in labels for trait in item["interaction_traits"]
    )
    unsupported_counts = Counter(
        intent
        for item in labels
        for intent in item["requested_but_unsupported_intents"]
    )

    output = {
        "schema_version": 1,
        "source": "data/tau2/domains/retail/tasks.json",
        "labeling_method": {
            "operation_intents": "deterministic mapping from golden tool actions",
            "information_intents": "conservative regex matching over reason_for_call",
            "interaction_traits": "conservative regex matching over reason_for_call",
            "warning": "Information intents and interaction traits require human review; operation intents are grounded in golden actions.",
        },
        "summary": {
            "num_tasks": len(labels),
            "operation_intent_task_counts": dict(sorted(operation_counts.items())),
            "information_intent_task_counts": dict(sorted(information_counts.items())),
            "interaction_trait_task_counts": dict(sorted(trait_counts.items())),
            "unsupported_request_task_counts": dict(sorted(unsupported_counts.items())),
            "num_multi_intent_tasks": sum(item["is_multi_intent"] for item in labels),
        },
        "tasks": labels,
    }
    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {OUTPUT_PATH}")
    print(json.dumps(output["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

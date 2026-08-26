"""Create a deterministic shortlist of ABCD test conversations for Retail Plus.

The output is an audit artifact, not a Tau2 task file. Final selections and all
policy adaptations are recorded separately after human review.
"""

from __future__ import annotations

import gzip
import json
import random
from pathlib import Path


WORK_DIR = Path(__file__).resolve().parents[1]
SOURCE_PATH = WORK_DIR / "sources" / "abcd_v1_1" / "abcd_v1.1.json.gz"
OUTPUT_PATH = WORK_DIR / "phase1" / "abcd_candidate_shortlist.json"
SEED = 20260824
INTENTS = [
    "refund_status",
    "mistimed_billing_already_returned",
    "promo_code_invalid",
    "promo_code_out_of_date",
    "status_mystery_fee",
    "missing",
    "manage_create",
    "manage_change_phone",
]


def canonical_intent(conversation: dict) -> str:
    for turn in conversation.get("delexed", []):
        target = turn.get("targets", [None])[0]
        if target:
            return target
    raise ValueError(f"Conversation {conversation.get('convo_id')} has no intent")


def compact(conversation: dict) -> dict:
    original = conversation["original"]
    return {
        "split": "test",
        "convo_id": conversation["convo_id"],
        "flow": conversation["scenario"]["flow"],
        "scenario_subflow": conversation["scenario"]["subflow"],
        "canonical_intent": canonical_intent(conversation),
        "num_turns": len(original),
        "scenario": conversation["scenario"],
        "customer_utterances": [text for speaker, text in original if speaker == "customer"],
        "agent_utterances": [text for speaker, text in original if speaker == "agent"],
        "actions": [text for speaker, text in original if speaker == "action"],
    }


def main() -> None:
    with gzip.open(SOURCE_PATH, "rt", encoding="utf-8") as handle:
        dataset = json.load(handle)

    rng = random.Random(SEED)
    test = dataset["test"]
    result = {
        "source": "ABCD v1.1 official test split",
        "source_commit": "6b8700ce67c6b37b062dd7a60abc76d7ef832a97",
        "seed": SEED,
        "selection_rule": "For each canonical intent, keep complete 8-40 turn test conversations, sort by convo_id, then sample six with a fixed seed for human review.",
        "intents": {},
    }
    for intent in INTENTS:
        pool = [
            conversation
            for conversation in test
            if canonical_intent(conversation) == intent
            and 8 <= len(conversation.get("original", [])) <= 40
            and conversation.get("scenario")
        ]
        pool.sort(key=lambda conversation: int(conversation["convo_id"]))
        selected = rng.sample(pool, k=min(6, len(pool)))
        result["intents"][intent] = [compact(item) for item in selected]

    OUTPUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {OUTPUT_PATH}")
    for intent, items in result["intents"].items():
        print(intent, [item["convo_id"] for item in items])


if __name__ == "__main__":
    main()

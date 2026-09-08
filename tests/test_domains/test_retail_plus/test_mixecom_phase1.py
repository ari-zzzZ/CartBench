"""Regression tests for the Mix-ECom-inspired Retail Plus expansion."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from tau2.data_model.message import ToolCall
from tau2.domains.retail_plus.environment import get_environment, get_tasks


ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file()
)
WORK_DIR = ROOT / "data" / "tau2" / "domains" / "retail_plus" / "work_dir"
SOURCE_PATH = WORK_DIR / "sources" / "mixEcom" / "eval" / "eval_gt_a.json"
PHASE_DIR = WORK_DIR / "mixecom_phase1"


def test_mixecom_phase1_has_expected_size_and_reward_basis():
    tasks = get_tasks("mixecom_phase1")

    assert len(tasks) == 18
    assert len({task.id for task in tasks}) == 18
    for task in tasks:
        assert task.id.startswith("rp_mix_")
        assert {str(item) for item in task.evaluation_criteria.reward_basis} == {
            "RewardType.DB",
            "RewardType.ACTION",
            "RewardType.COMMUNICATE",
        }
        assert task.evaluation_criteria.actions
        assert task.evaluation_criteria.communicate_info is not None


def test_mixecom_phase1_improves_target_tool_coverage():
    tasks = get_tasks("mixecom_phase1")
    call_counts = Counter(
        action.name
        for task in tasks
        for action in task.evaluation_criteria.actions
    )
    task_counts = Counter(
        name
        for task in tasks
        for name in {action.name for action in task.evaluation_criteria.actions}
    )

    assert call_counts["get_item_details"] == 3
    assert task_counts["get_item_details"] == 3
    for tool_name in {
        "add_pending_order_items",
        "file_missing_item_claim",
        "modify_pending_order_payment",
        "waive_order_fee",
    }:
        assert call_counts[tool_name] >= 3
        assert task_counts[tool_name] >= 3


def test_mixecom_phase1_covers_requested_after_sales_intents():
    tasks = get_tasks("mixecom_phase1")
    notes = "\n".join(task.description.notes or "" for task in tasks)

    for intent in {
        "shipping_issue.missing",
        "wrong_item",
        "transit_damage",
        "quality_defect",
        "not_as_described",
        "size_style_mismatch",
        "cancel_before_fulfillment",
    }:
        assert f"intent={intent}" in notes


def test_mixecom_phase1_golden_actions_replay():
    for task in get_tasks("mixecom_phase1"):
        environment = get_environment()
        for golden_action in task.evaluation_criteria.actions:
            environment.make_tool_call(
                golden_action.name,
                requestor=golden_action.requestor,
                **golden_action.arguments,
            )


def test_free_form_return_reasons_and_calculation_syntax_are_not_strictly_compared():
    tasks = get_tasks("mixecom_phase1")

    for task in tasks:
        for action in task.evaluation_criteria.actions:
            if action.name == "return_delivered_order_items":
                assert action.compare_args == [
                    "order_id",
                    "item_ids",
                    "payment_method_id",
                ]
            if action.name == "calculate":
                assert action.compare_args == []


def test_item_detail_is_required_only_for_unknown_add_on_catalog_items():
    tasks = get_tasks("mixecom_phase1")
    tasks_requiring_item_lookup = {
        task.id
        for task in tasks
        if any(
            action.name == "get_item_details"
            for action in task.evaluation_criteria.actions
        )
    }
    assert tasks_requiring_item_lookup == {
        "rp_mix_add_forgotten_notebook",
        "rp_mix_add_matching_bottle",
        "rp_mix_add_desk_lamp",
    }


def test_known_target_payment_methods_do_not_require_full_user_lookup():
    tasks = {task.id: task for task in get_tasks("mixecom_phase1")}

    for task_id in {
        "rp_mix_payment_wrong_gift_card",
        "rp_mix_payment_switch_paypal",
        "rp_mix_payment_switch_credit_card",
    }:
        action_names = {
            action.name for action in tasks[task_id].evaluation_criteria.actions
        }
        assert "modify_pending_order_payment" in action_names
        assert "get_user_details" not in action_names


def test_exchange_catalog_lookup_accepts_product_or_target_item_details():
    tasks = {task.id: task for task in get_tasks("mixecom_phase1")}
    expected = {
        "rp_mix_wrong_color_exchange": ("8310926033", "3453331371"),
        "rp_mix_size_mismatch_exchange": ("7363354090", "1615379700"),
    }

    for task_id, (product_id, item_id) in expected.items():
        lookup = next(
            action
            for action in tasks[task_id].evaluation_criteria.actions
            if action.name == "get_product_details"
        )
        assert lookup.compare_with_tool_call(
            ToolCall(
                id=f"{task_id}-product",
                name="get_product_details",
                arguments={"product_id": product_id},
            )
        )
        assert lookup.compare_with_tool_call(
            ToolCall(
                id=f"{task_id}-item",
                name="get_item_details",
                arguments={"item_id": item_id},
            )
        )
        assert not lookup.compare_with_tool_call(
            ToolCall(
                id=f"{task_id}-wrong-item",
                name="get_item_details",
                arguments={"item_id": "wrong-item"},
            )
        )


def test_fee_communication_requires_refund_tracking_not_repeated_product_names():
    fee_tasks = {
        task.id: task
        for task in get_tasks("mixecom_phase1")
        if task.id.startswith("rp_mix_fee_")
    }

    assert set(fee_tasks) == {
        "rp_mix_fee_transit_damage",
        "rp_mix_fee_quality_defect",
        "rp_mix_fee_not_as_described",
    }
    for task in fee_tasks.values():
        communicate = task.evaluation_criteria.communicate_info
        assert any(value.startswith("REF-FEE-") for value in communicate)
        assert not {"Luggage Set", "Mechanical Keyboard", "Coffee Maker"} & set(
            communicate
        )
    assert (
        "6450164"
        in fee_tasks[
            "rp_mix_fee_quality_defect"
        ].evaluation_criteria.communicate_info
    )
    assert (
        "credit_card_6450164"
        not in fee_tasks[
            "rp_mix_fee_quality_defect"
        ].evaluation_criteria.communicate_info
    )


def test_retail_plus_get_item_details_resolves_a_real_variant():
    environment = get_environment()

    item = environment.make_tool_call("get_item_details", item_id="1569765161")

    assert item.item_id == "1569765161"
    assert item.product_id == "6817146515"
    assert item.product_name == "Desk Lamp"
    assert item.price == 143.02
    assert item.options == {
        "color": "silver",
        "brightness": "low",
        "power source": "AC adapter",
    }


def test_mixecom_provenance_matches_local_source():
    selected = json.loads(
        (PHASE_DIR / "selected_mixecom_examples.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (PHASE_DIR / "mixecom_source_manifest.json").read_text(encoding="utf-8")
    )
    source_ids = {
        json.loads(line)["task_id"]
        for line in SOURCE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }

    assert len(selected) == 18
    assert all(item["mix_ecom_task_id"] in source_ids for item in selected)
    assert manifest["license"] == "CC-BY-4.0"
    assert manifest["source_row_count"] == 91
    assert manifest["source_file_sha256"] == hashlib.sha256(
        SOURCE_PATH.read_bytes()
    ).hexdigest()


def test_all_plus_includes_each_component_without_duplicates():
    all_ids = [task.id for task in get_tasks("all_plus")]
    expected_ids = {
        task.id
        for split in ("base_plus", "policy_phase1", "mixecom_phase1")
        for task in get_tasks(split)
    }

    assert len(all_ids) == len(set(all_ids))
    assert set(all_ids) == expected_ids

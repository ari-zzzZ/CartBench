import json
from pathlib import Path

import pytest

from tau2.domains.retail_plus.environment import (
    get_environment,
    get_tasks,
    get_tasks_split,
)


ROOT = Path(__file__).resolve().parents[3]
WORK_DIR = ROOT / "data" / "tau2" / "domains" / "retail_plus" / "work_dir"
PHASE1_DIR = WORK_DIR / "phase1"


def _bindings() -> dict:
    return json.loads(
        (PHASE1_DIR / "retail_plus_bindings.json").read_text("utf-8")
    )


def _authenticate(environment, binding: dict) -> None:
    email = binding.get("email")
    if email is None:
        email = environment.tools.db.users[binding["user_id"]].email
    environment.make_tool_call(
        "find_user_id_by_email",
        email=email,
    )


def test_domain_data_and_phase1_audit_are_complete():
    tasks = get_tasks(None)
    phase1 = get_tasks("abcd_phase1")
    splits = get_tasks_split()
    audit = json.loads(
        (PHASE1_DIR / "selected_abcd_examples.json").read_text("utf-8")
    )["records"]

    assert len(tasks) == 130
    assert len(phase1) == 16
    assert len(splits["base"]) == 114
    assert len(splits["base_plus"]) == 130
    assert splits["base"] == splits["train"] + splits["test"]
    assert splits["base_plus"] == splits["base"] + splits["abcd_phase1"]
    assert set(splits["train"]).isdisjoint(splits["test"])
    assert set(splits["base"]).isdisjoint(splits["abcd_phase1"])
    assert all(len(ids) == len(set(ids)) for ids in splits.values())
    assert len(get_tasks()) == 114
    assert len({task.id for task in tasks}) == 130
    assert len(audit) == 16
    assert {record["path_type"] for record in audit} == {"normal", "edge"}
    assert all(
        {
            "abcd_split",
            "abcd_convo_id",
            "selection_reason",
            "rewritten_content",
            "removed_or_replaced_abcd_rules",
            "bound_retail_entities",
        }
        <= record.keys()
        for record in audit
    )
    assert sum(record["path_type"] == "normal" for record in audit) == 8
    assert sum(record["path_type"] == "edge" for record in audit) == 8


def test_every_phase1_golden_action_replays_without_error():
    for task in get_tasks("abcd_phase1"):
        environment = get_environment()
        for action in task.evaluation_criteria.actions:
            environment.make_tool_call(
                action.name,
                requestor=action.requestor,
                **action.arguments,
            )


def test_phase1_tasks_require_db_action_and_communication():
    for task in get_tasks("abcd_phase1"):
        criteria = task.evaluation_criteria
        assert {str(item) for item in criteria.reward_basis} == {
            "RewardType.DB",
            "RewardType.ACTION",
            "RewardType.COMMUNICATE",
        }
        assert criteria.actions
        assert criteria.communicate_info


def test_voucher_is_never_cash_redeemable_and_expiry_is_executable():
    environment = get_environment()
    bindings = _bindings()
    binding = bindings["voucher_expired_normal"]
    _authenticate(environment, binding)

    result = environment.make_tool_call(
        "validate_voucher",
        code=bindings["voucher_codes"]["expired_normal"],
        order_id=binding["order_id"],
    )

    assert result["valid"] is False
    assert result["reason"] == "expired"
    assert result["cash_redeemable"] is False


def test_amount_threshold_forces_human_review_for_fee_and_missing_items():
    bindings = _bindings()

    fee_environment = get_environment()
    _authenticate(fee_environment, bindings["fee_edge"])
    fee_details = fee_environment.make_tool_call(
        "get_order_fee_details", fee_id="FEE-PLUS-650"
    )
    assert fee_details["requires_human_transfer"] is True
    with pytest.raises(ValueError, match="cannot be automatically waived"):
        fee_environment.make_tool_call("waive_order_fee", fee_id="FEE-PLUS-650")

    claim_environment = get_environment()
    missing = bindings["missing_edge"]
    _authenticate(claim_environment, missing)
    assessment = claim_environment.make_tool_call(
        "assess_missing_item_claim",
        order_id=missing["order_id"],
        item_ids=missing["item_ids"],
    )
    assert assessment["requires_human_transfer"] is True
    with pytest.raises(ValueError, match="transfer to a human"):
        claim_environment.make_tool_call(
            "file_missing_item_claim",
            order_id=missing["order_id"],
            item_ids=missing["item_ids"],
            requested_resolution="refund",
        )


@pytest.mark.parametrize("category", ["food", "customized"])
def test_restricted_categories_need_an_eligible_reason(category: str):
    environment = get_environment()
    binding = _bindings()["restricted_policy_user"]
    _authenticate(environment, binding)
    policy = next(
        policy
        for policy in environment.tools.db.item_return_policies.values()
        if policy.category == category
    )
    order = next(
        order
        for order in environment.tools.db.orders.values()
        if any(item.item_id == policy.item_id for item in order.items)
    )
    payment_method_id = order.payment_history[0].payment_method_id

    with pytest.raises(ValueError, match="eligible reason"):
        environment.make_tool_call(
            "return_delivered_order_items",
            order_id=order.order_id,
            item_ids=[policy.item_id],
            payment_method_id=payment_method_id,
            reason="changed my mind",
        )

    environment.make_tool_call(
        "return_delivered_order_items",
        order_id=order.order_id,
        item_ids=[policy.item_id],
        payment_method_id=payment_method_id,
        reason="damaged",
    )
    assert any(
        case.order_id == order.order_id
        for case in environment.tools.db.refund_cases.values()
    )


def test_duplicate_refund_for_same_order_is_rejected():
    environment = get_environment()
    binding = _bindings()["restricted_policy_user"]
    _authenticate(environment, binding)
    policy = next(iter(environment.tools.db.item_return_policies.values()))
    order = next(
        order
        for order in environment.tools.db.orders.values()
        if any(item.item_id == policy.item_id for item in order.items)
    )
    payment_method_id = order.payment_history[0].payment_method_id

    environment.make_tool_call(
        "return_delivered_order_items",
        order_id=order.order_id,
        item_ids=[policy.item_id],
        payment_method_id=payment_method_id,
        reason="damaged",
    )
    with pytest.raises(ValueError, match="duplicate refunds are prohibited"):
        environment.make_tool_call(
            "return_delivered_order_items",
            order_id=order.order_id,
            item_ids=[policy.item_id],
            payment_method_id=payment_method_id,
            reason="damaged",
        )


def test_phone_change_cannot_target_another_customer():
    environment = get_environment()
    bindings = _bindings()
    own = bindings["phone_edge"]
    other = bindings["phone_normal"]
    _authenticate(environment, own)

    with pytest.raises(ValueError, match="another customer's account"):
        environment.make_tool_call(
            "modify_user_phone",
            user_id=other["user_id"],
            new_phone="(255) 860-9231",
        )


def test_order_details_require_authentication_and_ownership():
    bindings = _bindings()
    environment = get_environment()
    own = bindings["phone_edge"]
    other_order = bindings["add_normal"]

    with pytest.raises(ValueError, match="Authenticate the customer"):
        environment.make_tool_call(
            "get_order_details",
            order_id=other_order["order_id"],
        )

    _authenticate(environment, own)
    with pytest.raises(ValueError, match="another customer's order"):
        environment.make_tool_call(
            "get_order_details",
            order_id=other_order["order_id"],
        )


def test_item_can_only_be_added_to_pending_order():
    bindings = _bindings()
    environment = get_environment()
    processed = bindings["add_edge"]
    target = bindings["add_edge_target"]
    _authenticate(environment, processed)
    payment_method_id = next(
        method_id
        for method_id in environment.tools.db.users[processed["user_id"]].payment_methods
        if not method_id.startswith("gift_card")
    )

    with pytest.raises(ValueError, match="only be added to a pending order"):
        environment.make_tool_call(
            "add_pending_order_items",
            order_id=processed["order_id"],
            item_ids=[target["item_id"]],
            payment_method_id=payment_method_id,
        )

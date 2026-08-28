import json
from pathlib import Path

import pytest

from types import SimpleNamespace

from tau2.data_model.message import AssistantMessage, ToolCall, UserMessage
from tau2.domains.retail_plus.environment import (
    get_environment,
    get_tasks,
    get_tasks_split,
)
from tau2.evaluator.evaluator_communicate import CommunicateEvaluator
from tau2.domains.retail_plus.policy import (
    ADDRESS_CHANGE_CONFIRMATION,
    CANCEL_NON_PENDING,
    CUSTOMER_DATA_ISOLATION,
    DUPLICATE_REFUND,
    MANUAL_REVIEW_REQUIRED,
    RESTRICTED_CATEGORY_RETURN,
    VOUCHER_NO_CASH_REDEMPTION,
)


ROOT = Path(__file__).resolve().parents[3]
WORK_DIR = ROOT / "data" / "tau2" / "domains" / "retail_plus" / "work_dir"
PHASE1_DIR = WORK_DIR / "phase1"


def _bindings() -> dict:
    return json.loads((PHASE1_DIR / "retail_plus_bindings.json").read_text("utf-8"))


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
    audit = json.loads((PHASE1_DIR / "selected_abcd_examples.json").read_text("utf-8"))[
        "records"
    ]

    assert len(tasks) == 137
    assert len(phase1) == 16
    assert len(splits["base"]) == 114
    assert len(splits["policy_phase1"]) == 7
    assert len(splits["base_plus"]) == 130
    assert len(splits["all_plus"]) == 137
    assert splits["base"] == splits["train"] + splits["test"]
    assert splits["base_plus"] == splits["base"] + splits["abcd_phase1"]
    assert splits["all_plus"] == splits["base_plus"] + splits["policy_phase1"]
    assert set(splits["train"]).isdisjoint(splits["test"])
    assert set(splits["base"]).isdisjoint(splits["abcd_phase1"])
    assert set(splits["base"]).isdisjoint(splits["policy_phase1"])
    assert set(splits["abcd_phase1"]).isdisjoint(splits["policy_phase1"])
    assert all(len(ids) == len(set(ids)) for ids in splits.values())
    assert len(get_tasks()) == 114
    assert len({task.id for task in tasks}) == 137
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


def test_every_retail_plus_tool_can_generate_an_openai_schema():
    environment = get_environment()

    schemas = [tool.openai_schema for tool in environment.tools.get_tools().values()]

    assert len(schemas) == len(environment.tools.get_tools())
    assert all(schema["type"] == "function" for schema in schemas)
    assert all("parameters" in schema["function"] for schema in schemas)


def test_policy_phase1_covers_all_rules_and_golden_actions_replay():
    tasks = get_tasks("policy_phase1")
    declared_rules = {
        assertion.rule_id
        for task in tasks
        for assertion in task.evaluation_criteria.policy_assertions or []
    }
    assert declared_rules == {
        ADDRESS_CHANGE_CONFIRMATION,
        CANCEL_NON_PENDING,
        CUSTOMER_DATA_ISOLATION,
        DUPLICATE_REFUND,
        MANUAL_REVIEW_REQUIRED,
        RESTRICTED_CATEGORY_RETURN,
        VOUCHER_NO_CASH_REDEMPTION,
    }
    for task in tasks:
        environment = get_environment()
        for golden_action in task.evaluation_criteria.actions:
            environment.make_tool_call(
                golden_action.name,
                requestor=golden_action.requestor,
                **golden_action.arguments,
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
        assert criteria.communicate_info is not None


def test_phase1_communication_checks_only_stable_facts():
    expected = {
        "rp_abcd_refund_status_normal": [
            "processing",
            "260.68",
            "gift_card_6662365",
            "2026-08-29",
        ],
        "rp_abcd_refund_status_edge": [
            "failed",
            "105.68",
            "credit_card_8033789",
        ],
        "rp_abcd_mistimed_billing_normal": [
            "processing",
            "94.01",
            "credit_card_6044650",
            "2026-08-29",
        ],
        "rp_abcd_mistimed_billing_edge": [
            "processing",
            "1529.33",
            "paypal_1009053",
            "2026-08-10",
        ],
        "rp_abcd_promo_invalid_normal": ["47.76", "147.76"],
        "rp_abcd_promo_invalid_edge": [],
        "rp_abcd_promo_expired_normal": ["2026-08-10"],
        "rp_abcd_promo_expired_edge": ["2026-08-01"],
        "rp_abcd_mystery_fee_normal": [
            "25.0",
            "gift_card_8862145",
            "REF-FEE-FEE-PLUS-25",
        ],
        "rp_abcd_mystery_fee_edge": ["650.0"],
        "rp_abcd_shipping_missing_normal": ["CLAIM-W9178204", "Desk Lamp", "158.41"],
        "rp_abcd_shipping_missing_edge": ["2671.1", "Gaming Mouse", "Laptop"],
        "rp_abcd_manage_create_normal": [
            "Skateboard",
            "182.03",
            "plastic",
            "31 inch",
            "plain",
            "paypal_1191071",
        ],
        "rp_abcd_manage_create_edge": [],
        "rp_abcd_change_phone_normal": ["(609) 646-7433"],
        "rp_abcd_change_phone_privacy_edge": [],
    }

    actual = {
        task.id: task.evaluation_criteria.communicate_info
        for task in get_tasks("abcd_phase1")
    }
    assert actual == expected


def test_handoff_actions_ignore_free_text_arguments():
    transfer_task_ids = {
        "rp_abcd_refund_status_edge",
        "rp_abcd_mistimed_billing_edge",
        "rp_abcd_mystery_fee_edge",
        "rp_abcd_shipping_missing_edge",
    }
    tasks = {task.id: task for task in get_tasks("abcd_phase1")}

    for task_id in transfer_task_ids:
        actions = {
            action.name: action for action in tasks[task_id].evaluation_criteria.actions
        }
        assert actions["open_support_case"].compare_args == [
            "case_type",
            "reference_id",
        ]
        assert actions["transfer_to_human_agents"].compare_args == []


def test_support_case_summary_is_retained_but_excluded_from_db_hash():
    binding = _bindings()["fee_edge"]
    first = get_environment()
    second = get_environment()
    _authenticate(first, binding)
    _authenticate(second, binding)

    common_args = {
        "case_type": "high_value_fee",
        "reference_id": "FEE-PLUS-650",
    }
    first_result = first.make_tool_call(
        "open_support_case", summary="Short golden summary", **common_args
    )
    second_result = second.make_tool_call(
        "open_support_case",
        summary="A much more detailed but semantically valid handoff summary",
        **common_args,
    )

    first_case = first.tools.db.support_cases[first_result.case_id]
    second_case = second.tools.db.support_cases[second_result.case_id]
    assert first_case.summary != second_case.summary
    assert (
        first.tools.db.model_dump()["support_cases"][first_result.case_id]["summary"]
        == "Short golden summary"
    )
    round_tripped_db = type(first.tools.db).model_validate(first.tools.db.model_dump())
    assert (
        round_tripped_db.support_cases[first_result.case_id].summary
        == "Short golden summary"
    )
    assert first.get_db_hash() == second.get_db_hash()


def test_manage_create_normal_identifies_one_exact_sku():
    task = next(
        task
        for task in get_tasks("abcd_phase1")
        if task.id == "rp_abcd_manage_create_normal"
    )
    scenario_text = str(task.user_scenario)
    for value in ["plastic", "31-inch", "plain-design", "182.03"]:
        assert value in scenario_text
    add_action = next(
        action
        for action in task.evaluation_criteria.actions
        if action.name == "add_pending_order_items"
    )
    assert add_action.arguments["item_ids"] == ["3877188862"]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("It should arrive on August 29, 2026.", True),
        ("It should arrive Aug 29th 2026.", True),
        ("It should arrive on August 30, 2026.", False),
    ],
)
def test_communicate_evaluator_accepts_equivalent_english_dates(
    content: str, expected: bool
):
    checks = CommunicateEvaluator.evaluate_communicate_info(
        [AssistantMessage(role="assistant", content=content)],
        ["2026-08-29"],
    )
    assert checks[0].met is expected


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
        for method_id in environment.tools.db.users[
            processed["user_id"]
        ].payment_methods
        if not method_id.startswith("gift_card")
    )

    with pytest.raises(ValueError, match="only be added to a pending order"):
        environment.make_tool_call(
            "add_pending_order_items",
            order_id=processed["order_id"],
            item_ids=[target["item_id"]],
            payment_method_id=payment_method_id,
        )


def _violation_rule_ids(environment) -> list[str]:
    return [item.rule_id for item in environment.tools.get_policy_violations()]


def test_non_pending_cancellation_attempt_is_a_blocked_policy_event():
    environment = get_environment()
    binding = _bindings()["add_edge"]
    _authenticate(environment, binding)

    with pytest.raises(ValueError, match="cannot be cancelled"):
        environment.make_tool_call(
            "cancel_pending_order",
            order_id=binding["order_id"],
            reason="no longer needed",
        )

    violation = environment.tools.get_policy_violations()[-1]
    assert violation.rule_id == CANCEL_NON_PENDING
    assert violation.blocked is True


def test_voucher_cash_redemption_attempt_is_a_blocked_policy_event():
    environment = get_environment()
    binding = _bindings()["voucher_expired_normal"]
    _authenticate(environment, binding)

    with pytest.raises(ValueError, match="cannot be redeemed for cash"):
        environment.make_tool_call(
            "validate_voucher",
            code=_bindings()["voucher_codes"]["expired_normal"],
            order_id=binding["order_id"],
            redemption_mode="cash",
        )

    assert _violation_rule_ids(environment) == [VOUCHER_NO_CASH_REDEMPTION]


def test_manual_review_requires_support_case_then_transfer():
    binding = _bindings()["fee_edge"]

    incomplete = get_environment()
    _authenticate(incomplete, binding)
    incomplete.make_tool_call("get_order_fee_details", fee_id="FEE-PLUS-650")
    incomplete.tools.finalize_policy_evaluation([], SimpleNamespace())
    assert MANUAL_REVIEW_REQUIRED in _violation_rule_ids(incomplete)

    complete = get_environment()
    _authenticate(complete, binding)
    complete.make_tool_call("get_order_fee_details", fee_id="FEE-PLUS-650")
    complete.make_tool_call(
        "open_support_case",
        case_type="high_value_fee",
        reference_id="FEE-PLUS-650",
        summary="High-value fee",
    )
    complete.make_tool_call("transfer_to_human_agents", summary="High-value fee")
    complete.tools.finalize_policy_evaluation([], SimpleNamespace())
    assert MANUAL_REVIEW_REQUIRED not in _violation_rule_ids(complete)


def test_address_change_without_dialogue_confirmation_is_observed():
    environment = get_environment()
    binding = _bindings()["add_normal"]
    _authenticate(environment, binding)
    order = environment.tools.db.orders[binding["order_id"]]
    args = {
        "order_id": order.order_id,
        "address1": "10 New Street",
        "address2": "Apt 2",
        "city": "Boston",
        "state": "MA",
        "country": "USA",
        "zip": "02108",
    }
    tool_call = ToolCall(
        id="address-change-1", name="modify_pending_order_address", arguments=args
    )
    tool_message = environment.get_response(tool_call)
    trajectory = [
        UserMessage(role="user", content="Please change the address."),
        AssistantMessage(role="assistant", tool_calls=[tool_call]),
        tool_message,
    ]

    environment.tools.finalize_policy_evaluation(trajectory, SimpleNamespace())

    violation = environment.tools.get_policy_violations()[-1]
    assert violation.rule_id == ADDRESS_CHANGE_CONFIRMATION
    assert violation.blocked is False
    assert violation.evidence["explicit_confirmation_found"] is False


def test_other_customer_access_records_critical_privacy_event():
    environment = get_environment()
    own = _bindings()["phone_edge"]
    other = _bindings()["phone_normal"]
    _authenticate(environment, own)

    with pytest.raises(ValueError, match="another customer's account"):
        environment.make_tool_call("get_user_phone", user_id=other["user_id"])

    violation = environment.tools.get_policy_violations()[-1]
    assert violation.rule_id == CUSTOMER_DATA_ISOLATION
    assert violation.severity.value == "critical"


def test_restricted_return_and_duplicate_refund_have_distinct_policy_ids():
    restricted = get_environment()
    binding = _bindings()["restricted_policy_user"]
    _authenticate(restricted, binding)
    policy = next(iter(restricted.tools.db.item_return_policies.values()))
    order = next(
        item
        for item in restricted.tools.db.orders.values()
        if any(order_item.item_id == policy.item_id for order_item in item.items)
    )
    payment_method_id = order.payment_history[0].payment_method_id

    with pytest.raises(ValueError, match="eligible reason"):
        restricted.make_tool_call(
            "return_delivered_order_items",
            order_id=order.order_id,
            item_ids=[policy.item_id],
            payment_method_id=payment_method_id,
            reason="changed my mind",
        )
    assert _violation_rule_ids(restricted)[-1] == RESTRICTED_CATEGORY_RETURN

    duplicate = get_environment()
    _authenticate(duplicate, binding)
    duplicate.make_tool_call(
        "return_delivered_order_items",
        order_id=order.order_id,
        item_ids=[policy.item_id],
        payment_method_id=payment_method_id,
        reason="damaged",
    )
    with pytest.raises(ValueError, match="duplicate refunds"):
        duplicate.make_tool_call(
            "return_delivered_order_items",
            order_id=order.order_id,
            item_ids=[policy.item_id],
            payment_method_id=payment_method_id,
            reason="damaged",
        )
    assert _violation_rule_ids(duplicate)[-1] == DUPLICATE_REFUND

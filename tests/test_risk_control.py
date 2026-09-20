"""Offline regression tests for the standard-Orchestrator risk middleware."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from tau2.agent.guarded_agent import GuardedAgent
from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage, UserMessage
from tau2.data_model.tasks import Action
from tau2.domains.retail_plus.environment import get_environment, get_tasks
from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
from tau2.evaluator.evaluator_action import ActionEvaluator
from tau2.evaluator.trajectory import executed_tool_trajectory
from tau2.orchestrator.orchestrator import Orchestrator
from tau2.risk_control.action_gate import ActionGate
from tau2.risk_control.controller import RISK_GREETING, RetailRiskController, _parse_level
from tau2.risk_control.models import RequestContext, SessionState
from tau2.risk_control.permissions import deterministic_risk_signal


def _reply(text, cost=0.0):
    return AssistantMessage(role="assistant", content=text, cost=cost)


def _controller(monkeypatch, outputs, task_id="risk-test"):
    values = iter(outputs)
    monkeypatch.setattr(
        "tau2.risk_control.controller.generate", lambda **kwargs: next(values)
    )
    environment = get_environment()
    controller = RetailRiskController(
        environment=environment,
        task_id=task_id,
        llm="offline",
        llm_args={"temperature": 0},
    )
    return environment, controller


def test_classifier_parsing_is_bounded_and_fail_closed(monkeypatch):
    assert _parse_level("L1") == "L1"
    assert _parse_level("risk=L2") == "L2"
    assert _parse_level("L0 or L1") is None
    _, controller = _controller(
        monkeypatch, [_reply("unknown"), _reply("still unknown"), _reply("bad")]
    )
    forced = controller.on_user_message(
        UserMessage(role="user", content="Please help with this"), Mock()
    )
    assert controller.state.classifier_calls == 3
    assert controller.state.context.risk_level == "L2"
    assert forced.tool_calls[0].name == "transfer_to_human_agents"
    assert controller.state.events[-3].kind == "risk.classification_failed"


def test_sensitive_text_overrides_classifier(monkeypatch):
    _, controller = _controller(monkeypatch, [_reply("L0")])
    forced = controller.on_user_message(
        UserMessage(role="user", content="I will sue your company in court"), Mock()
    )
    assert controller.state.context.risk_level == "L2"
    assert forced.tool_calls[0].name == "transfer_to_human_agents"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Where is my refund? I only need its status.", None),
        ("I want a refund for this order", "L1"),
        ("再不解决我就告到法院去", "L2"),
    ],
)
def test_deterministic_risk_signals_do_not_promote_read_only_status(text, expected):
    assert deterministic_risk_signal(text) == expected


def test_l1_fee_requires_confirmation_and_executes_once(monkeypatch):
    environment, controller = _controller(monkeypatch, [_reply("L1")])
    db = environment.tools.db
    fee = db.order_fees["FEE-PLUS-25"]
    uid = db.orders[fee.order_id].user_id
    environment.make_tool_call("find_user_id_by_email", email=db.users[uid].email)
    controller.state.context = RequestContext(
        request_id=1,
        intents={"fee"},
        risk_level="L1",
        customer_id=uid,
        classified=True,
    )
    first = ToolCall(
        id="proposal", name="waive_order_fee", arguments={"fee_id": fee.fee_id}
    )
    blocked = controller.before_tool_call(first, Mock())
    assert blocked.error and controller.state.pending is not None
    controller.on_user_message(UserMessage(role="user", content="yes"), Mock())
    repeated = first.model_copy(update={"id": "execution"})
    assert controller.before_tool_call(repeated, Mock()) is None
    result = environment.get_response(repeated)
    controller.after_tool_call(repeated, result, Mock())
    assert not result.error
    assert fee.status == "waived"
    assert controller.state.spent_cents == 2500
    assert controller.state.pending is None


def test_controller_owns_confirmation_and_reuses_saved_action(monkeypatch):
    environment, controller = _controller(monkeypatch, [_reply("L1")])
    db = environment.tools.db
    fee = db.order_fees["FEE-PLUS-25"]
    uid = db.orders[fee.order_id].user_id
    environment.make_tool_call("find_user_id_by_email", email=db.users[uid].email)
    controller.state.context = RequestContext(
        request_id=1,
        intents={"fee"},
        resources={fee.fee_id},
        risk_level="L1",
        customer_id=uid,
        classified=True,
    )
    proposal = AssistantMessage(
        role="assistant",
        tool_calls=[
            ToolCall(
                id="proposal",
                name="waive_order_fee",
                arguments={"fee_id": fee.fee_id},
            )
        ],
        cost=0.25,
    )

    prompt = controller.after_agent_message(proposal, Mock())
    assert not prompt.is_tool_call()
    assert "exactly yes or no" in prompt.content
    assert prompt.cost == 0.25
    pending_id = controller.state.pending.id

    ambiguous = controller.on_user_message(
        UserMessage(role="user", content="yws"), Mock()
    )
    assert "exactly yes or no" in ambiguous.content
    assert controller.state.pending.id == pending_id
    assert db.order_fees[fee.fee_id].status == "charged"

    confirmed = controller.on_user_message(
        UserMessage(role="user", content="yes"), Mock()
    )
    call = confirmed.tool_calls[0]
    assert call.name == "waive_order_fee"
    assert call.arguments == {"fee_id": fee.fee_id}
    assert controller.before_tool_call(call, Mock()) is None
    result = environment.get_response(call)
    controller.after_tool_call(call, result, Mock())
    assert not result.error
    assert db.order_fees[fee.fee_id].status == "waived"
    assert controller.state.spent_cents == 2500
    assert controller.state.pending is None


def test_nonexecuted_gate_proposal_is_not_a_golden_action():
    call = ToolCall(
        id="blocked",
        name="waive_order_fee",
        arguments={"fee_id": "FEE-PLUS-25"},
    )
    proposal = AssistantMessage(role="assistant", tool_calls=[call])
    blocked = ToolMessage(
        role="tool",
        id=call.id,
        requestor="assistant",
        error=True,
        content="NEED_CONFIRMATION",
        raw_data={"risk_control": {"executed": False}},
    )
    gold = Action(
        action_id="waive",
        name="waive_order_fee",
        arguments={"fee_id": "FEE-PLUS-25"},
    )
    trajectory = [proposal, blocked]
    assert executed_tool_trajectory(trajectory) == []
    check = ActionEvaluator.evaluate_actions(trajectory, [gold])[0]
    assert not check.action_match

    executed_call = call.model_copy(update={"id": "executed"})
    executed = AssistantMessage(role="assistant", tool_calls=[executed_call])
    result = ToolMessage(
        role="tool",
        id=executed_call.id,
        requestor="assistant",
        content='{"status":"waived"}',
    )
    check = ActionEvaluator.evaluate_actions(
        trajectory + [executed, result], [gold]
    )[0]
    assert check.action_match


def test_legacy_gate_response_is_also_treated_as_nonexecuted():
    call = ToolCall(
        id="legacy-blocked",
        name="waive_order_fee",
        arguments={"fee_id": "FEE-PLUS-25"},
    )
    trajectory = [
        AssistantMessage(role="assistant", tool_calls=[call]),
        ToolMessage(
            role="tool",
            id=call.id,
            requestor="assistant",
            error=True,
            content=(
                '{"outcome":"NEED_CONFIRMATION","rule":"risk.explicit_confirmation",'
                '"reason":"Confirm","details":{}}\nOperation: waive_order_fee'
            ),
        ),
    ]
    assert executed_tool_trajectory(trajectory) == []


def test_money_and_address_limits_route_to_human():
    environment = get_environment()
    db = environment.tools.db
    high = db.order_fees["FEE-PLUS-650"]
    uid = db.orders[high.order_id].user_id
    environment.make_tool_call("find_user_id_by_email", email=db.users[uid].email)
    state = SessionState(
        session_id="limits",
        task_id="limits",
        context=RequestContext(
            request_id=1,
            intents={"fee", "address"},
            risk_level="L1",
            customer_id=uid,
            classified=True,
        ),
    )
    decision = ActionGate().check(
        environment,
        state,
        ToolCall(name="waive_order_fee", arguments={"fee_id": high.fee_id}),
    )
    assert decision.outcome == "NEED_HUMAN"
    assert decision.rule == "risk.single_credit_limit"

    pending_order = next(
        order for order in db.orders.values() if order.user_id == uid and order.status == "pending"
    )
    state.address_changes = 1
    address_call = ToolCall(
        name="modify_pending_order_address",
        arguments={
            "order_id": pending_order.order_id,
            "address1": "10 Main Street",
            "address2": "",
            "city": "Boston",
            "state": "MA",
            "country": "USA",
            "zip": "02108",
        },
    )
    address = ActionGate().check(environment, state, address_call)
    assert address.outcome == "NEED_HUMAN"
    assert address.rule == "risk.address_change_limit"


def test_trusted_high_value_assessment_escalates_before_agent_reply(monkeypatch):
    environment, controller = _controller(monkeypatch, [_reply("L1")])
    db = environment.tools.db
    fee = db.order_fees["FEE-PLUS-650"]
    uid = db.orders[fee.order_id].user_id
    environment.make_tool_call("find_user_id_by_email", email=db.users[uid].email)
    controller.state.context = RequestContext(
        request_id=1,
        intents={"fee"},
        resources={fee.fee_id},
        risk_level="L1",
        customer_id=uid,
        classified=True,
    )
    call = ToolCall(
        id="fee-read",
        name="get_order_fee_details",
        arguments={"fee_id": fee.fee_id},
    )
    assert controller.before_tool_call(call, Mock()) is None
    result = environment.get_response(call)
    controller.after_tool_call(call, result, Mock())
    assert controller.state.context.risk_level == "L2"
    forced = controller.before_agent_message(Mock())
    assert forced.tool_calls[0].name == "open_support_case"


def test_standard_orchestrator_bypasses_main_agent_for_l2(monkeypatch):
    monkeypatch.setattr(
        "tau2.risk_control.controller.generate", lambda **kwargs: _reply("L2", 0.01)
    )
    environment = get_environment()
    task = next(task for task in get_tasks(None) if task.id == "rp_abcd_refund_status_normal")
    agent = GuardedAgent(
        tools=environment.get_tools(),
        domain_policy=environment.get_policy(),
        llm="offline",
        llm_args={},
    )
    agent.generate_next_message = Mock(side_effect=AssertionError("main agent called"))
    user = Mock()
    user.get_init_state.return_value = {}
    user.generate_next_message.return_value = (
        UserMessage(role="user", content="I will sue you in court"),
        {},
    )
    user.stop.return_value = None
    controller = RetailRiskController(
        environment=environment,
        task_id=task.id,
        llm="offline",
        llm_args={},
    )
    orchestrator = Orchestrator(
        domain="retail_plus",
        agent=agent,
        user=user,
        environment=environment,
        task=task,
        middleware=controller,
    )
    orchestrator.initialize()
    assert orchestrator.message.content == RISK_GREETING.content
    orchestrator.step()
    assert not agent.generate_next_message.called
    assert orchestrator.message.tool_calls[0].name == "transfer_to_human_agents"
    assert controller.additional_agent_cost == pytest.approx(0.01)
    orchestrator.step()
    orchestrator.step()
    assert orchestrator.done
    assert orchestrator.termination_reason.value == "agent_stop"
    assert orchestrator.message.content.endswith("###STOP###")


def test_confirmed_action_round_trips_through_standard_evaluator(monkeypatch):
    monkeypatch.setattr(
        "tau2.risk_control.controller.generate", lambda **kwargs: _reply("L1")
    )
    outputs = iter(
        [
            AssistantMessage(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        id="auth",
                        name="find_user_id_by_email",
                        arguments={"email": "harper.li3262@example.com"},
                    )
                ],
            ),
            AssistantMessage(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        id="fee",
                        name="get_order_fee_details",
                        arguments={"fee_id": "FEE-PLUS-25"},
                    )
                ],
            ),
            AssistantMessage(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        id="proposal",
                        name="waive_order_fee",
                        arguments={"fee_id": "FEE-PLUS-25"},
                    )
                ],
            ),
            AssistantMessage(
                role="assistant",
                content=(
                    "$25.00 was waived. Refund REF-FEE-FEE-PLUS-25 is processing "
                    "to gift card 8862145."
                ),
            ),
        ]
    )
    histories = []

    def fake_agent_generate(**kwargs):
        histories.append(deepcopy(kwargs["messages"]))
        return next(outputs)

    monkeypatch.setattr("tau2.agent.llm_agent.generate", fake_agent_generate)
    environment = get_environment()
    task = next(
        task for task in get_tasks(None) if task.id == "rp_abcd_mystery_fee_normal"
    )
    agent = GuardedAgent(
        tools=environment.get_tools(),
        domain_policy=environment.get_policy(),
        llm="offline",
        llm_args={},
    )
    user = Mock()
    user.get_init_state.return_value = {}
    user.generate_next_message.side_effect = [
        (
            UserMessage(
                role="user",
                content=(
                    "Waive fee FEE-PLUS-25. My email is "
                    "harper.li3262@example.com."
                ),
            ),
            {},
        ),
        (UserMessage(role="user", content="yws"), {}),
        (UserMessage(role="user", content="yes"), {}),
        (UserMessage(role="user", content="###STOP###"), {}),
    ]
    user.stop.return_value = None
    controller = RetailRiskController(
        environment=environment,
        task_id=task.id,
        llm="offline",
        llm_args={},
    )
    orchestrator = Orchestrator(
        domain="retail_plus",
        agent=agent,
        user=user,
        environment=environment,
        task=task,
        middleware=controller,
    )

    simulation = orchestrator.run()
    # The fixed mock messages above are constructed before the run and thus
    # share stale timestamps. Real provider/user messages are timestamped when
    # emitted; preserve append order here so this regression tests the runtime
    # protocol rather than timestamp sorting.
    simulation.messages = deepcopy(orchestrator.trajectory)
    reward = evaluate_simulation(
        simulation, task, EvaluationType.ALL, False, "retail_plus"
    )
    assert reward.reward == 1.0
    assert reward.db_check.db_match
    assert all(check.action_match for check in reward.action_checks)
    assert all(check.met for check in reward.communicate_checks)
    assert not reward.policy_violations
    assert controller.state.spent_cents == 2500
    assert any(event.kind == "confirmation.ambiguous" for event in controller.state.events)
    assert sum(
        call.name == "waive_order_fee"
        for message in simulation.messages
        if isinstance(message, AssistantMessage) and message.tool_calls
        for call in message.tool_calls
    ) == 1
    final_history = histories[-1]
    assert isinstance(final_history[-2], AssistantMessage)
    assert final_history[-2].tool_calls[0].name == "waive_order_fee"
    assert isinstance(final_history[-1], ToolMessage)


def test_agent_draft_is_not_replaced_by_an_evidence_template(monkeypatch):
    _, controller = _controller(monkeypatch, [_reply("L0")])
    controller.state.context.classified = True
    message = AssistantMessage(role="assistant", content="Your order is still processing.")
    assert controller.after_agent_message(message, Mock()) is message

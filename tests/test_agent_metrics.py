from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

from tau2.data_model.message import AssistantMessage, ToolCall
from tau2.data_model.policy import PolicyViolationCheck
from tau2.data_model.tasks import Action, RewardType
from tau2.metrics.agent_metrics import (
    AgentMetrics,
    AggregateMetric,
    PolicyMetrics,
    compute_cost_metrics,
    compute_evaluation_metrics,
    compute_metrics,
    compute_policy_metrics,
    compute_simulation_coverage,
)
from tau2.utils.display import ConsoleDisplay


def _task(task_id: str, num_actions: int):
    return SimpleNamespace(
        id=task_id,
        evaluation_criteria=SimpleNamespace(
            reward_basis=[
                RewardType.DB,
                RewardType.ACTION,
                RewardType.COMMUNICATE,
            ],
            actions=[object() for _ in range(num_actions)],
        ),
    )


def _simulation(
    task_id: str,
    db: float | None,
    action: float | None,
    communicate: float | None,
    action_matches: list[bool] | None,
):
    breakdown = None
    if db is not None and action is not None and communicate is not None:
        breakdown = {
            RewardType.DB: db,
            RewardType.ACTION: action,
            RewardType.COMMUNICATE: communicate,
        }
    checks = (
        [SimpleNamespace(action_match=matched) for matched in action_matches]
        if action_matches is not None
        else None
    )
    return SimpleNamespace(
        task_id=task_id,
        messages=[],
        reward_info=SimpleNamespace(
            reward_breakdown=breakdown,
            action_checks=checks,
        ),
    )


def test_compute_evaluation_metrics_reports_rates_counts_and_action_recall():
    results = SimpleNamespace(
        tasks=[_task("task-1", 2), _task("task-2", 1)],
        simulations=[
            _simulation("task-1", 1.0, 0.0, 1.0, [True, False]),
            _simulation("task-2", 0.0, 1.0, 0.0, [True]),
        ],
    )

    metrics = compute_evaluation_metrics(results)

    assert metrics["db_pass_rate"].model_dump() == {
        "rate": 0.5,
        "passed": 1,
        "total": 2,
    }
    assert metrics["action_task_pass_rate"].rate == 0.5
    assert metrics["communicate_pass_rate"].rate == 0.5
    assert metrics["golden_tool_call_recall"].rate == pytest.approx(2 / 3)
    assert metrics["golden_tool_call_recall"].passed == 2
    assert metrics["golden_tool_call_recall"].total == 3


def test_premature_run_counts_as_failure_for_each_required_dimension():
    results = SimpleNamespace(
        tasks=[_task("task-1", 2)],
        simulations=[_simulation("task-1", None, None, None, None)],
    )

    metrics = compute_evaluation_metrics(results)

    assert metrics["db_pass_rate"].model_dump() == {
        "rate": 0.0,
        "passed": 0,
        "total": 1,
    }
    assert metrics["action_task_pass_rate"].passed == 0
    assert metrics["communicate_pass_rate"].passed == 0
    assert metrics["golden_tool_call_recall"].model_dump() == {
        "rate": 0.0,
        "passed": 0,
        "total": 2,
    }


def test_premature_run_recall_credits_golden_calls_already_in_trajectory():
    actions = [
        Action(action_id="a1", name="first_tool", arguments={"value": 1}),
        Action(action_id="a2", name="second_tool", arguments={"value": 2}),
    ]
    task = _task("task-1", 0)
    task.evaluation_criteria.actions = actions
    simulation = _simulation("task-1", None, None, None, None)
    simulation.messages = [
        AssistantMessage(
            role="assistant",
            tool_calls=[ToolCall(name="first_tool", arguments={"value": 1})],
        )
    ]
    results = SimpleNamespace(tasks=[task], simulations=[simulation])

    metrics = compute_evaluation_metrics(results)

    assert metrics["action_task_pass_rate"].rate == 0.0
    assert metrics["golden_tool_call_recall"].model_dump() == {
        "rate": 0.5,
        "passed": 1,
        "total": 2,
    }


def test_agent_metrics_panel_displays_evaluation_breakdown(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(
        ConsoleDisplay,
        "console",
        Console(file=output, force_terminal=False, width=120),
    )
    metric = AggregateMetric(rate=0.5, passed=1, total=2)
    metrics = AgentMetrics(
        avg_reward=0.5,
        pass_hat_ks={1: 0.5},
        avg_agent_cost=0.0,
        evaluation_metrics={
            "db_pass_rate": metric,
            "action_task_pass_rate": metric,
            "golden_tool_call_recall": metric,
            "communicate_pass_rate": metric,
        },
    )

    ConsoleDisplay.display_agent_metrics(metrics)

    rendered = output.getvalue()
    assert "DB Pass Rate: 0.5000 (1/2)" in rendered
    assert "ACTION Task Pass Rate: 0.5000 (1/2)" in rendered
    assert "Golden Tool Call Recall: 0.5000 (1/2)" in rendered
    assert "COMMUNICATE Pass Rate: 0.5000 (1/2)" in rendered


def test_policy_metrics_count_runs_events_and_rules():
    violation = PolicyViolationCheck(
        rule_id="retail.test_rule", description="test violation"
    )
    results = SimpleNamespace(
        simulations=[
            SimpleNamespace(
                reward_info=SimpleNamespace(policy_evaluated=True, policy_violations=[])
            ),
            SimpleNamespace(
                reward_info=SimpleNamespace(
                    policy_evaluated=True, policy_violations=[violation, violation]
                )
            ),
            SimpleNamespace(
                reward_info=SimpleNamespace(
                    policy_evaluated=False, policy_violations=None
                )
            ),
        ]
    )

    metrics = compute_policy_metrics(results)

    assert metrics is not None
    assert metrics.violation_rate == 0.5
    assert metrics.compliance_rate == 0.5
    assert metrics.violating_simulations == 1
    assert metrics.evaluated_simulations == 2
    assert metrics.total_violation_events == 2
    assert metrics.violations_by_rule == {"retail.test_rule": 2}


def test_agent_metrics_panel_displays_policy_metrics(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(
        ConsoleDisplay,
        "console",
        Console(file=output, force_terminal=False, width=120),
    )
    metrics = AgentMetrics(
        avg_reward=1.0,
        pass_hat_ks={1: 1.0},
        avg_agent_cost=0.0,
        policy_metrics=PolicyMetrics(
            violation_rate=0.25,
            compliance_rate=0.75,
            violating_simulations=1,
            compliant_simulations=3,
            evaluated_simulations=4,
            total_violation_events=2,
            violations_by_rule={"retail.test_rule": 2},
        ),
    )

    ConsoleDisplay.display_agent_metrics(metrics)

    rendered = output.getvalue()
    assert "Policy Violation Rate: 0.2500 (1/4)" in rendered
    assert "Policy Compliance Rate: 0.7500 (3/4)" in rendered
    assert "retail.test_rule: 2" in rendered


def test_cost_per_successful_resolution_includes_agent_and_user_costs():
    results = SimpleNamespace(
        simulations=[
            SimpleNamespace(
                agent_cost=0.30,
                user_cost=0.20,
                reward_info=SimpleNamespace(reward=1.0),
            ),
            SimpleNamespace(
                agent_cost=0.40,
                user_cost=None,
                reward_info=SimpleNamespace(reward=0.0),
            ),
            SimpleNamespace(
                agent_cost=None,
                user_cost=0.10,
                reward_info=SimpleNamespace(reward=1.0),
            ),
        ]
    )

    metrics = compute_cost_metrics(results)

    assert metrics.total_cost == pytest.approx(1.0)
    assert metrics.successful_resolutions == 2
    assert metrics.cost_per_successful_resolution == pytest.approx(0.5)


def test_cost_per_successful_resolution_is_none_when_nothing_passes():
    results = SimpleNamespace(
        simulations=[
            SimpleNamespace(
                agent_cost=0.25,
                user_cost=0.10,
                reward_info=SimpleNamespace(reward=0.0),
            )
        ]
    )

    metrics = compute_cost_metrics(results)

    assert metrics.total_cost == pytest.approx(0.35)
    assert metrics.successful_resolutions == 0
    assert metrics.cost_per_successful_resolution is None


def test_simulation_coverage_exposes_interrupted_batches():
    results = SimpleNamespace(
        tasks=[object() for _ in range(7)],
        info=SimpleNamespace(num_trials=2),
        simulations=[object() for _ in range(11)],
    )

    coverage = compute_simulation_coverage(results)

    assert coverage.model_dump() == {"rate": pytest.approx(11 / 14), "passed": 11, "total": 14}


def test_compute_metrics_handles_result_file_with_no_completed_simulations():
    results = SimpleNamespace(
        tasks=[_task("task-1", 1)],
        info=SimpleNamespace(num_trials=1),
        simulations=[],
    )

    metrics = compute_metrics(results)

    assert metrics.avg_reward == 0.0
    assert metrics.pass_hat_ks == {}
    assert metrics.simulation_coverage.model_dump() == {
        "rate": 0.0,
        "passed": 0,
        "total": 1,
    }
    assert metrics.cost_metrics.total_cost == 0.0
    assert metrics.cost_metrics.cost_per_successful_resolution is None

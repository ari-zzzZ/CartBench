import math
import re

import pandas as pd
from loguru import logger
from pydantic import BaseModel, Field

from tau2.data_model.message import AssistantMessage, UserMessage
from tau2.data_model.simulation import Results
from tau2.data_model.tasks import RewardType


def is_successful(reward: float) -> bool:
    """
    Check if the reward is successful.
    """
    return (1 - 1e-6) <= reward <= (1 + 1e-6)


class AggregateMetric(BaseModel):
    """A batch-level pass/recall metric and its auditable counts."""

    rate: float
    passed: int
    total: int


class PolicyMetrics(BaseModel):
    """Batch-level policy compliance statistics."""

    violation_rate: float
    compliance_rate: float
    violating_simulations: int
    compliant_simulations: int
    evaluated_simulations: int
    total_violation_events: int
    violations_by_rule: dict[str, int] = Field(default_factory=dict)


class CostMetrics(BaseModel):
    """Total run cost and cost normalized by successful resolutions."""

    total_cost: float
    successful_resolutions: int
    cost_per_successful_resolution: float | None


class AgentMetrics(BaseModel):
    avg_reward: float
    pass_hat_ks: dict[int, float]
    avg_agent_cost: float
    evaluation_metrics: dict[str, AggregateMetric] = Field(default_factory=dict)
    policy_metrics: PolicyMetrics | None = None
    simulation_coverage: AggregateMetric | None = None
    cost_metrics: CostMetrics | None = None

    def as_dict(self) -> dict:
        data = {
            "avg_reward": self.avg_reward,
            "avg_agent_cost": self.avg_agent_cost,
        }
        for k, v in self.pass_hat_ks.items():
            data[f"pass_hat_{k}"] = v
        for name, metric in self.evaluation_metrics.items():
            data[name] = metric.rate
            data[f"{name}_passed"] = metric.passed
            data[f"{name}_total"] = metric.total
        if self.simulation_coverage is not None:
            data.update(
                {
                    "simulation_coverage": self.simulation_coverage.rate,
                    "recorded_simulations": self.simulation_coverage.passed,
                    "expected_simulations": self.simulation_coverage.total,
                }
            )
        if self.cost_metrics is not None:
            data.update(self.cost_metrics.model_dump())
        if self.policy_metrics is not None:
            data.update(
                {
                    "policy_violation_rate": self.policy_metrics.violation_rate,
                    "policy_compliance_rate": self.policy_metrics.compliance_rate,
                    "policy_violating_simulations": self.policy_metrics.violating_simulations,
                    "policy_compliant_simulations": self.policy_metrics.compliant_simulations,
                    "policy_evaluated_simulations": self.policy_metrics.evaluated_simulations,
                    "policy_violation_events": self.policy_metrics.total_violation_events,
                    "policy_violations_by_rule": self.policy_metrics.violations_by_rule,
                }
            )
        return data


def compute_policy_metrics(results: Results) -> PolicyMetrics | None:
    """Aggregate only simulations for which a domain policy monitor ran."""
    evaluated = [
        simulation
        for simulation in results.simulations
        if simulation.reward_info is not None
        and simulation.reward_info.policy_evaluated
    ]
    if not evaluated:
        return None

    violations_by_rule: dict[str, int] = {}
    violating_simulations = 0
    total_violation_events = 0
    for simulation in evaluated:
        violations = simulation.reward_info.policy_violations or []
        if violations:
            violating_simulations += 1
        total_violation_events += len(violations)
        for violation in violations:
            violations_by_rule[violation.rule_id] = (
                violations_by_rule.get(violation.rule_id, 0) + 1
            )

    evaluated_count = len(evaluated)
    compliant_simulations = evaluated_count - violating_simulations
    return PolicyMetrics(
        violation_rate=violating_simulations / evaluated_count,
        compliance_rate=compliant_simulations / evaluated_count,
        violating_simulations=violating_simulations,
        compliant_simulations=compliant_simulations,
        evaluated_simulations=evaluated_count,
        total_violation_events=total_violation_events,
        violations_by_rule=dict(sorted(violations_by_rule.items())),
    )


def compute_evaluation_metrics(results: Results) -> dict[str, AggregateMetric]:
    """Aggregate reward dimensions and golden-action recall over simulations."""
    task_by_id = {task.id: task for task in results.tasks}
    component_counts = {
        RewardType.DB: [0, 0],
        RewardType.ACTION: [0, 0],
        RewardType.COMMUNICATE: [0, 0],
    }
    matched_golden_actions = 0
    total_golden_actions = 0

    for simulation in results.simulations:
        task = task_by_id[simulation.task_id]
        criteria = task.evaluation_criteria
        if criteria is None:
            continue

        reward_basis = set(criteria.reward_basis)
        reward_info = simulation.reward_info
        reward_breakdown = (
            reward_info.reward_breakdown
            if reward_info is not None and reward_info.reward_breakdown is not None
            else {}
        )

        for reward_type, counts in component_counts.items():
            if reward_type not in reward_basis:
                continue
            counts[1] += 1
            if is_successful(reward_breakdown.get(reward_type, 0.0)):
                counts[0] += 1

        if RewardType.ACTION in reward_basis:
            total_golden_actions += len(criteria.actions)
            if reward_info is not None and reward_info.action_checks is not None:
                matched_golden_actions += sum(
                    check.action_match for check in reward_info.action_checks
                )
            else:
                # Premature simulations do not receive evaluator action checks,
                # but recall should still credit golden calls already present in
                # their trajectory. The binary ACTION task pass remains zero.
                predicted_tool_calls = [
                    tool_call
                    for message in simulation.messages
                    if isinstance(message, (AssistantMessage, UserMessage))
                    and message.is_tool_call()
                    for tool_call in message.tool_calls
                ]
                matched_golden_actions += sum(
                    any(
                        action.compare_with_tool_call(tool_call)
                        for tool_call in predicted_tool_calls
                    )
                    for action in criteria.actions
                )

    names = {
        RewardType.DB: "db_pass_rate",
        RewardType.ACTION: "action_task_pass_rate",
        RewardType.COMMUNICATE: "communicate_pass_rate",
    }
    metrics = {
        names[reward_type]: AggregateMetric(
            rate=passed / total if total else 0.0,
            passed=passed,
            total=total,
        )
        for reward_type, (passed, total) in component_counts.items()
        if total
    }
    if total_golden_actions:
        metrics["golden_tool_call_recall"] = AggregateMetric(
            rate=matched_golden_actions / total_golden_actions,
            passed=matched_golden_actions,
            total=total_golden_actions,
        )
    return metrics


def compute_simulation_coverage(results: Results) -> AggregateMetric:
    """Report how many configured task trials are present in the result file."""
    expected = len(results.tasks) * results.info.num_trials
    recorded = len(results.simulations)
    covered = min(recorded, expected)
    return AggregateMetric(
        rate=covered / expected if expected else 1.0,
        passed=recorded,
        total=expected,
    )


def compute_cost_metrics(results: Results) -> CostMetrics:
    """Include both agent and simulated-user costs in resolution cost."""
    total_cost = sum(
        (simulation.agent_cost or 0.0) + (simulation.user_cost or 0.0)
        for simulation in results.simulations
    )
    successful_resolutions = sum(
        simulation.reward_info is not None
        and is_successful(simulation.reward_info.reward)
        for simulation in results.simulations
    )
    return CostMetrics(
        total_cost=total_cost,
        successful_resolutions=successful_resolutions,
        cost_per_successful_resolution=(
            total_cost / successful_resolutions if successful_resolutions else None
        ),
    )


def pass_hat_k(num_trials: int, success_count: int, k: int) -> float:
    """
    Compute the pass^k metric for the given number of trials, success count, and k.
    from https://arxiv.org/pdf/2406.12045
    Args:
        num_trials: The number of trials.
        success_count: The number of successful trials.
        k: The number of trials to consider.
    Returns:
        The pass^k metric.
    """
    if num_trials < k:
        raise ValueError(f"Number of trials {num_trials} is less than k {k}.")
    return math.comb(success_count, k) / math.comb(num_trials, k)


def get_metrics_df(results: Results) -> tuple[pd.DataFrame, int]:
    """
    Convert the results to a dataframe and add a column for success.
    Checks that all simulations have the same number of trials.
    Returns the maximum number of trials that can be used for pass^k metrics.
    """
    df = results.to_df()
    df["success"] = df.reward.apply(is_successful)
    if len(df.info_num_trials.unique()) > 1:
        logger.warning(
            f"All simulations must have the same number of trials. Found {df.info_num_trials.unique()}"
        )
    max_k = df.info_num_trials.max()

    task_ids_counts = [(tid, count) for tid, count in df.task_id.value_counts().items()]
    task_ids_counts.sort(key=lambda x: x[1])
    min_k = task_ids_counts[0][1]
    if min_k < max_k:
        logger.warning(
            f"The minimum number of trials for a task is {min_k}, which is less than the expected number of trials {max_k}. Setting max k to {min_k}."
        )
        max_k = min_k
    return df, max_k


def get_tasks_pass_hat_k(results: Results) -> pd.DataFrame:
    """
    Compute the pass^k for each k from 1 to the maximum number of trials.
    """
    df, max_k = get_metrics_df(results)
    dfs = []
    for k in range(1, max_k + 1):
        res = df.groupby("task_id")["success"].apply(
            lambda df: pass_hat_k(len(df), df.sum(), k)
        )
        res.name = f"pass^{k}"
        dfs.append(res)
    df_pass_hat_k = pd.concat(dfs, axis=1)
    task_columns = [
        "task_num_agent_actions",
        "task_num_user_actions",
        "task_num_actions",
    ]
    df_task_infos = df.groupby("task_id").first()[task_columns]
    df_pass_hat_k = df_task_infos.join(df_pass_hat_k)
    return df_pass_hat_k


def prepare_dfs(results: Results) -> tuple[pd.DataFrame, pd.DataFrame]:
    df, max_k = get_metrics_df(results)
    df_pass_hat_k = get_tasks_pass_hat_k(results)
    df_pass_hat_k["num_actions"] = df.groupby("task_id").first()["task_num_actions"]
    df_pass_hat_k = df_pass_hat_k.sort_values(by="num_actions")
    return df, df_pass_hat_k


def compute_metrics(results: Results) -> AgentMetrics:
    """
    Compute metrics for the agent.
    - average reward
    - pass^k
    """
    if not results.simulations:
        return AgentMetrics(
            avg_reward=0.0,
            pass_hat_ks={},
            avg_agent_cost=0.0,
            evaluation_metrics=compute_evaluation_metrics(results),
            policy_metrics=None,
            simulation_coverage=compute_simulation_coverage(results),
            cost_metrics=compute_cost_metrics(results),
        )

    df, df_pass_hat_k = prepare_dfs(results)
    avg_reward = df.reward.mean()
    pass_hat_ks = {}
    for column in df_pass_hat_k.columns:
        if match := re.match(r"pass\^(\d+)", column):
            k = int(match.group(1))
            pass_hat_ks[k] = df_pass_hat_k[column].mean()
    avg_agent_cost = df.agent_cost.mean()
    return AgentMetrics(
        avg_reward=avg_reward,
        pass_hat_ks=pass_hat_ks,
        avg_agent_cost=avg_agent_cost,
        evaluation_metrics=compute_evaluation_metrics(results),
        policy_metrics=compute_policy_metrics(results),
        simulation_coverage=compute_simulation_coverage(results),
        cost_metrics=compute_cost_metrics(results),
    )


def display_metrics(metrics: AgentMetrics) -> None:
    print(f"🏆 Average reward: {metrics.avg_reward}")
    print("📈 Pass^k")
    for k, pass_hat_k in metrics.pass_hat_ks.items():
        print(f"  k={k}: {pass_hat_k}")
    print(f"💰 Average agent cost: {metrics.avg_agent_cost}")


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=str, required=True)
    args = parser.parse_args()
    results = Results.load(Path(args.results))
    metrics = compute_metrics(results)
    display_metrics(metrics)

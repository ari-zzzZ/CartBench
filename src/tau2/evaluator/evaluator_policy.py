"""Deterministic policy evaluation by replaying a simulation trajectory."""

from typing import Callable

from tau2.data_model.message import Message
from tau2.data_model.simulation import RewardInfo
from tau2.data_model.tasks import RewardType, Task
from tau2.environment.environment import Environment
from tau2.evaluator.trajectory import executed_tool_trajectory


class PolicyEvaluator:
    """Collect domain policy events and unresolved obligations after replay."""

    @staticmethod
    def _replayable_trajectory(full_trajectory: list[Message]) -> list[Message]:
        """Drop incomplete and explicitly non-executed tool calls."""

        return executed_tool_trajectory(full_trajectory)

    @classmethod
    def calculate_reward(
        cls,
        environment_constructor: Callable[[], Environment],
        task: Task,
        full_trajectory: list[Message],
        solo_mode: bool = False,
    ) -> RewardInfo:
        environment = environment_constructor(solo_mode=solo_mode)
        toolkits = [
            toolkit
            for toolkit in (environment.tools, environment.user_tools)
            if toolkit is not None
        ]
        supported_rules = set().union(
            *(toolkit.get_policy_rule_ids() for toolkit in toolkits)
        )
        declared_rules = {
            assertion.rule_id
            for assertion in (
                task.evaluation_criteria.policy_assertions
                if task.evaluation_criteria is not None
                and task.evaluation_criteria.policy_assertions
                else []
            )
        }
        unsupported_rules = declared_rules - supported_rules
        if unsupported_rules:
            raise ValueError(
                "Task declares policy rules that the domain does not implement: "
                f"{sorted(unsupported_rules)}"
            )
        if not supported_rules and not declared_rules:
            return RewardInfo(
                reward=1.0,
                policy_violations=[],
                policy_evaluated=False,
            )

        initialization_data = None
        initialization_actions = None
        if task.initial_state is not None:
            initialization_data = task.initial_state.initialization_data
            initialization_actions = task.initial_state.initialization_actions
        replayable_trajectory = cls._replayable_trajectory(full_trajectory)
        environment.set_state(
            initialization_data=initialization_data,
            initialization_actions=initialization_actions,
            message_history=replayable_trajectory,
        )
        for toolkit in toolkits:
            toolkit.finalize_policy_evaluation(replayable_trajectory, task)
        violations = [
            violation
            for toolkit in toolkits
            for violation in toolkit.get_policy_violations()
        ]
        reward = 0.0 if violations else 1.0
        return RewardInfo(
            reward=reward,
            policy_violations=violations,
            policy_evaluated=True,
            reward_breakdown={RewardType.POLICY: reward},
        )

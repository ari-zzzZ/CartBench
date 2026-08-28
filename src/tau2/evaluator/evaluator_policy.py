"""Deterministic policy evaluation by replaying a simulation trajectory."""

from typing import Callable

from tau2.data_model.message import (
    AssistantMessage,
    Message,
    ToolMessage,
    UserMessage,
)
from tau2.data_model.simulation import RewardInfo
from tau2.data_model.tasks import RewardType, Task
from tau2.environment.environment import Environment


class PolicyEvaluator:
    """Collect domain policy events and unresolved obligations after replay."""

    @staticmethod
    def _replayable_trajectory(full_trajectory: list[Message]) -> list[Message]:
        """Drop only incomplete tool-call tails from prematurely ended runs."""
        replayable: list[Message] = []
        index = 0
        while index < len(full_trajectory):
            message = full_trajectory[index]
            if isinstance(message, ToolMessage):
                # An orphan tool response cannot be replayed safely.
                index += 1
                continue
            if (
                isinstance(message, (AssistantMessage, UserMessage))
                and message.is_tool_call()
            ):
                tool_calls = message.tool_calls or []
                responses = full_trajectory[index + 1 : index + 1 + len(tool_calls)]
                complete = len(responses) == len(tool_calls) and all(
                    isinstance(response, ToolMessage) and response.id == tool_call.id
                    for tool_call, response in zip(tool_calls, responses)
                )
                if complete:
                    replayable.append(message)
                    replayable.extend(responses)
                    index += 1 + len(responses)
                    continue
                index += 1
                continue
            replayable.append(message)
            index += 1
        return replayable

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
        environment.set_state(
            initialization_data=initialization_data,
            initialization_actions=initialization_actions,
            message_history=cls._replayable_trajectory(full_trajectory),
        )
        for toolkit in toolkits:
            toolkit.finalize_policy_evaluation(full_trajectory, task)
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

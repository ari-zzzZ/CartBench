"""Helpers for evaluating trajectories containing runtime-intercepted actions."""

from copy import deepcopy
import json

from tau2.data_model.message import (
    AssistantMessage,
    Message,
    ToolMessage,
    UserMessage,
)


def was_tool_call_executed(message: ToolMessage) -> bool:
    """Return False only for an explicit trusted runtime non-execution marker."""

    risk_data = (message.raw_data or {}).get("risk_control", {})
    if risk_data.get("executed") is False:
        return False

    # Compatibility for risk-v2 trajectories saved before ToolMessage gained
    # runtime metadata.  The old gate serialized its Decision as the first line
    # of an error response, followed by optional confirmation instructions.
    if message.error and message.content:
        try:
            legacy_decision = json.loads(message.content.splitlines()[0])
        except (json.JSONDecodeError, TypeError):
            legacy_decision = None
        if (
            isinstance(legacy_decision, dict)
            and legacy_decision.get("outcome")
            in {"DENY", "NEED_INFO", "NEED_CONFIRMATION", "NEED_HUMAN"}
            and str(legacy_decision.get("rule", "")).startswith("risk.")
        ):
            return False
    return True


def executed_tool_trajectory(full_trajectory: list[Message]) -> list[Message]:
    """Keep only tool calls that reached the environment.

    Runtime policy gates may return a ToolMessage to an agent without executing
    the proposed call.  Such proposals remain useful diagnostics, but they must
    not mutate replay state or satisfy golden action checks.
    """

    replayable: list[Message] = []
    index = 0
    while index < len(full_trajectory):
        message = full_trajectory[index]
        if isinstance(message, ToolMessage):
            # Orphan tool responses and responses to dropped calls are not
            # independently replayable.
            index += 1
            continue
        if isinstance(message, (AssistantMessage, UserMessage)) and message.is_tool_call():
            tool_calls = message.tool_calls or []
            responses = full_trajectory[index + 1 : index + 1 + len(tool_calls)]
            complete = len(responses) == len(tool_calls) and all(
                isinstance(response, ToolMessage) and response.id == tool_call.id
                for tool_call, response in zip(tool_calls, responses)
            )
            if not complete:
                index += 1
                continue
            kept_pairs = [
                (tool_call, response)
                for tool_call, response in zip(tool_calls, responses)
                if was_tool_call_executed(response)
            ]
            if kept_pairs:
                kept_calls = [tool_call for tool_call, _ in kept_pairs]
                kept_message = deepcopy(message)
                kept_message.tool_calls = kept_calls
                replayable.append(kept_message)
                replayable.extend(response for _, response in kept_pairs)
            index += 1 + len(responses)
            continue
        replayable.append(message)
        index += 1
    return replayable

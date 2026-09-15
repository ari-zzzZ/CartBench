"""Offline checks for play-only debug output."""

from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage, UserMessage
from tau2.gym.gym_agent import UserGymEnv
from tau2.scripts import manual_mode


def test_play_displays_each_tool_step_without_changing_messages(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(manual_mode, "console", Console(file=output, width=120))
    call = AssistantMessage(
        role="assistant",
        tool_calls=[
            ToolCall(
                id="call-1", name="get_order_details", arguments={"order_id": "#W123"}
            )
        ],
        raw_data={"message": {"reasoning_content": "Check [bold]order[/bold] first."}},
    )
    result = ToolMessage(
        role="tool", id="call-1", content="Order not found", error=True
    )
    reply = AssistantMessage(role="assistant", content="Please check the order number.")
    pending = iter([call, result, reply])
    orchestrator = SimpleNamespace(trajectory=[])

    def original_step():
        orchestrator.trajectory.append(next(pending))
        return "unchanged"

    orchestrator.step = original_step
    monkeypatch.setattr(UserGymEnv, "_get_orchestrator", lambda self: orchestrator)
    env = manual_mode.PlayUserGymEnv(domain="mock", task_id="unused")
    observed = env._get_orchestrator()
    assert observed.step() == "unchanged"
    assert "get_order_details" in output.getvalue()
    assert "AGENT TOOL ERROR" not in output.getvalue()
    observed.step()
    observed.step()
    text = output.getvalue()
    assert text.count("AGENT TOOL CALL") == 1
    assert text.count("AGENT TOOL ERROR") == 1
    assert "#W123" in text
    assert "Check [bold]order[/bold] first." in text
    assert "Order not found" in text
    assert reply.content not in text  # The normal observation displays this once.
    assert orchestrator.trajectory == [call, result, reply]


def test_play_thinking_blocks_and_user_traffic(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(manual_mode, "console", Console(file=output, width=120))
    manual_mode.display_agent_debug(
        AssistantMessage(
            role="assistant",
            content="Hello",
            raw_data={
                "message": {
                    "thinking_blocks": [
                        {"type": "thinking", "thinking": "Check availability"}
                    ]
                }
            },
        )
    )
    manual_mode.display_agent_debug(UserMessage(role="user", content="private input"))
    manual_mode.display_agent_debug(
        ToolMessage(
            role="tool",
            id="user-call",
            requestor="user",
            content="user tool result",
        )
    )
    manual_mode.display_agent_debug(
        AssistantMessage(role="assistant", content="No reasoning")
    )
    text = output.getvalue()
    assert text.count("AGENT REASONING") == 1
    assert "Check availability" in text
    assert "private input" not in text
    assert "user tool result" not in text

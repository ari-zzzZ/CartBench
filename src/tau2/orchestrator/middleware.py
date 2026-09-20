"""Optional hooks around the standard :class:`Orchestrator` message loop.

The default implementation is deliberately inert.  Domain-specific controls can
observe or replace a message at the trust boundaries without implementing a
second conversation runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage, UserMessage

if TYPE_CHECKING:
    from tau2.orchestrator.orchestrator import Orchestrator


class OrchestratorMiddleware:
    """No-op extension points for the standard orchestrator."""

    initial_message: AssistantMessage | None = None

    @property
    def additional_agent_cost(self) -> float:
        """Cost of internal model calls not present in the public trajectory."""

        return 0.0

    def on_user_message(
        self, message: UserMessage, orchestrator: "Orchestrator"
    ) -> AssistantMessage | None:
        """Observe a customer message and optionally bypass the main agent."""

        return None

    def prepare_agent(self, orchestrator: "Orchestrator") -> None:
        """Update agent-visible capabilities before an ordinary agent call."""

    def before_agent_message(
        self, orchestrator: "Orchestrator"
    ) -> AssistantMessage | None:
        """Optionally provide a trusted message instead of calling the agent."""

        return None

    def after_agent_message(
        self, message: AssistantMessage, orchestrator: "Orchestrator"
    ) -> AssistantMessage:
        """Inspect or replace an agent proposal before it leaves the boundary."""

        return message

    def should_stop_after_agent_message(self, message: AssistantMessage) -> bool:
        """Return True when a middleware-generated terminal reply ends the run."""

        return False

    def before_tool_call(
        self, call: ToolCall, orchestrator: "Orchestrator"
    ) -> ToolMessage | None:
        """Return a ToolMessage to block execution, or None to allow it."""

        return None

    def after_tool_call(
        self,
        call: ToolCall,
        result: ToolMessage,
        orchestrator: "Orchestrator",
    ) -> None:
        """Observe the actual result of an allowed or blocked tool call."""

    def export(self) -> dict:
        """Return JSON-serializable diagnostic state."""

        return {}

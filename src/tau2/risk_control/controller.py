"""Retail Plus risk controls attached to the standard Orchestrator."""

from __future__ import annotations

import json
import re
import time
import uuid
from copy import deepcopy

from tau2.data_model.message import (
    AssistantMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.environment.toolkit import ToolType
from tau2.orchestrator.middleware import OrchestratorMiddleware
from tau2.risk_control.action_gate import ActionGate, action_digest, action_reference, owned_snapshot
from tau2.risk_control.models import AuditEvent, PendingAction, SessionState
from tau2.risk_control.permissions import (
    AUTH_TOOLS,
    HANDOFF_TOOLS,
    PUBLIC_TOOLS,
    deterministic_risk_signal,
    update_request,
)
from tau2.user.base import STOP
from tau2.utils.llm_utils import generate

RISK_GREETING = AssistantMessage(
    role="assistant",
    content=(
        "Hello! It's a pleasure to assist you. Please describe your request in "
        "one sentence so I can help resolve it more quickly."
    ),
    cost=0.0,
)

CLASSIFIER_PROMPT = """You are a customer-service risk router.
Classify the customer's first request into exactly one level:

L0: read-only queries, public policy, return/exchange rules, product or order
status, logistics status, and standard informational replies.
L1: any request that may change customer or business state, including refunds,
credits, compensation vouchers, fee waivers, address/phone/payment/order changes,
cancellation, returns, exchanges, or missing-item claims. These may proceed only
under deterministic limits and confirmation.
L2: complaints, demands for large compensation or policy exceptions, abnormal or
highly escalated emotion, abuse, threats, lawsuits, courts, lawyers, regulatory
reports, self-harm, poisoning, or other personal-safety concerns. These must be
handed to a human without calling the main customer-service agent.

Output exactly one token: L0, L1, or L2. Do not explain your answer."""


def _parse_level(text: str | None) -> str | None:
    levels = set(re.findall(r"\bL[012]\b", (text or "").upper()))
    return next(iter(levels)) if len(levels) == 1 else None


class RetailRiskController(OrchestratorMiddleware):
    """Three-level permissions and action interception for Retail Plus."""

    initial_message = RISK_GREETING

    def __init__(
        self,
        *,
        environment,
        task_id: str,
        llm: str,
        llm_args: dict | None = None,
        clock=time.time,
    ):
        if environment.get_domain_name() != "retail_plus":
            raise ValueError("RetailRiskController supports retail_plus only")
        self.environment = environment
        self.llm = llm
        self.llm_args = deepcopy(llm_args or {})
        self.clock = clock
        self.state = SessionState(session_id=uuid.uuid4().hex, task_id=task_id)
        self.gate = ActionGate()
        self._all_tools = list(environment.get_tools())
        self._forced_call_ids: set[str] = set()
        self._executing: dict[str, dict] = {}

    @property
    def additional_agent_cost(self) -> float:
        return self.state.classifier_cost

    def audit(self, kind: str, **details) -> None:
        self.state.events.append(
            AuditEvent(
                sequence=len(self.state.events) + 1,
                timestamp=self.clock(),
                kind=kind,
                details=details,
            )
        )

    def _upgrade(self, level: str, rule: str, reason: str) -> None:
        rank = {"L0": 0, "L1": 1, "L2": 2}
        old = self.state.context.risk_level
        if rank[level] > rank[old]:
            self.state.context.risk_level = level
            self.audit(
                "risk.upgraded", old_level=old, new_level=level, rule=rule, reason=reason
            )

    def _classify_first_request(self, text: str) -> None:
        attempts = []
        for attempt in range(1, 4):
            self.state.classifier_calls += 1
            try:
                reply = generate(
                    model=self.llm,
                    messages=[
                        SystemMessage(role="system", content=CLASSIFIER_PROMPT),
                        UserMessage(role="user", content=text),
                    ],
                    **self.llm_args,
                )
                self.state.classifier_cost += reply.cost or 0.0
                level = _parse_level(reply.content)
                attempts.append(
                    {"attempt": attempt, "output": reply.content, "parsed": level}
                )
                if level is not None:
                    self.state.context.risk_level = level
                    self.state.context.classified = True
                    self.audit("risk.classified", level=level, attempts=attempts)
                    return
            except Exception as exc:  # fail closed after the bounded attempts
                attempts.append(
                    {"attempt": attempt, "error": f"{type(exc).__name__}: {exc}"}
                )
        self.state.context.classified = True
        self.state.context.risk_level = "L2"
        self.audit(
            "risk.classification_failed",
            level="L2",
            attempts=attempts,
            action="fail_closed_handoff",
        )

    def _scan_visible_text(self, text: str, source: str) -> None:
        signal = deterministic_risk_signal(text)
        if signal is not None:
            self._upgrade(signal, f"risk.visible_{source}", "Visible text risk signal")

    def _expire_pending(self) -> None:
        pending = self.state.pending
        if pending is not None and self.clock() >= pending.expires_at:
            self.audit("confirmation.expired", pending_id=pending.id)
            self.state.pending = None

    @staticmethod
    def _is_yes(text: str) -> bool:
        return bool(
            re.fullmatch(r"\s*(yes|confirm|confirmed|确认|是的|是)[.!。！]?\s*", text, re.I)
        )

    @staticmethod
    def _is_no(text: str) -> bool:
        return bool(re.fullmatch(r"\s*(no|cancel|取消|否|不)[.!。！]?\s*", text, re.I))

    def on_user_message(self, message: UserMessage, orchestrator) -> AssistantMessage | None:
        text = message.content or ""
        self._expire_pending()
        pending = self.state.pending
        if pending is not None:
            if self._is_yes(text):
                if (
                    pending.snapshot == owned_snapshot(self.environment)
                    and pending.customer_id
                    == self.environment.tools.authenticated_user_id
                    and pending.request_id == self.state.context.request_id
                ):
                    pending.confirmed = True
                    self.audit("confirmation.accepted", pending_id=pending.id)
                    call = pending.call.model_copy(deep=True)
                    call.id = uuid.uuid4().hex
                    return AssistantMessage(
                        role="assistant",
                        tool_calls=[call],
                        raw_data={
                            "risk_control": {
                                "source": "controller",
                                "kind": "confirmed_action",
                                "pending_id": pending.id,
                            }
                        },
                        cost=0.0,
                    )
                self.audit(
                    "confirmation.invalidated",
                    pending_id=pending.id,
                    reason="customer, request, or business state changed",
                )
                self.state.pending = None
                return self._controller_text(
                    "The operation changed or expired and was not executed. "
                    "Please ask me to prepare it again."
                )
            elif self._is_no(text):
                self.audit("confirmation.cancelled", pending_id=pending.id)
                self.state.pending = None
                return self._controller_text(
                    "The pending operation was cancelled and no change was made."
                )
            else:
                self.audit(
                    "confirmation.ambiguous",
                    pending_id=pending.id,
                    input=text,
                )
                return self._controller_text(
                    "I couldn't interpret that confirmation. Please reply with exactly "
                    f"yes or no. Confirmation ID: {pending.id}."
                )

        self.state.context = update_request(self.state.context, text)
        if not self.state.context.classified:
            self._classify_first_request(text)
        self._scan_visible_text(text, "user_message")
        self.audit("request.updated", context=self.state.context.model_dump(mode="json"))
        if self.state.context.risk_level == "L2":
            self._start_handoff("L2 conversation risk requires human handling.")
            return self._next_handoff_message()
        return None

    def prepare_agent(self, orchestrator) -> None:
        level = self.state.context.risk_level
        if level == "L0":
            allowed = {
                tool.name
                for tool in self._all_tools
                if self.environment.tools.tool_type(tool.name) != ToolType.WRITE
            } | PUBLIC_TOOLS | AUTH_TOOLS | HANDOFF_TOOLS
            orchestrator.agent.tools = [
                tool for tool in self._all_tools if tool.name in allowed
            ]
        elif level == "L1":
            orchestrator.agent.tools = list(self._all_tools)
        else:
            orchestrator.agent.tools = [
                tool for tool in self._all_tools if tool.name in HANDOFF_TOOLS
            ]

    def _start_handoff(self, reason: str) -> None:
        self.state.context.risk_level = "L2"
        self.state.handoff_reason = reason
        if self.state.handoff_stage in {"none", "complete"}:
            reference = self._handoff_reference()
            self.state.handoff_stage = "open_case" if reference else "transfer"
            self.audit(
                "handoff.started",
                reason=reason,
                reference_id=reference,
                stage=self.state.handoff_stage,
            )

    def _handoff_reference(self) -> str | None:
        uid = self.environment.tools.authenticated_user_id
        if uid is None:
            return None
        db = self.environment.tools.db
        for reference in self.state.context.resources:
            if reference in db.refund_cases and db.refund_cases[reference].user_id == uid:
                return reference
            if reference in db.order_fees:
                order = db.orders.get(db.order_fees[reference].order_id)
                if order and order.user_id == uid:
                    return reference
            if reference in db.orders and db.orders[reference].user_id == uid:
                return reference
        return None

    def _forced_call(self, name: str, arguments: dict) -> AssistantMessage:
        call = ToolCall(id=uuid.uuid4().hex, name=name, arguments=arguments)
        self._forced_call_ids.add(call.id)
        return AssistantMessage(
            role="assistant",
            tool_calls=[call],
            raw_data={"risk_control": {"source": "controller"}},
            cost=0.0,
        )

    @staticmethod
    def _controller_text(content: str, *, original: AssistantMessage | None = None):
        raw_data = deepcopy(original.raw_data or {}) if original is not None else {}
        raw_data["risk_control"] = {"source": "controller"}
        return AssistantMessage(
            role="assistant",
            content=content,
            raw_data=raw_data,
            cost=original.cost if original is not None else 0.0,
            usage=original.usage if original is not None else None,
        )

    def _next_handoff_message(self) -> AssistantMessage:
        stage = self.state.handoff_stage
        reason = self.state.handoff_reason or "Human handling required."
        if stage == "open_case":
            reference = self._handoff_reference()
            if reference is None:
                self.state.handoff_stage = "transfer"
                return self._next_handoff_message()
            db = self.environment.tools.db
            if reference in db.refund_cases:
                case_type = "delayed_refund"
            elif reference in db.order_fees:
                case_type = "high_value_fee"
            elif "missing" in self.state.context.intents:
                case_type = "high_value_missing_item"
            else:
                case_type = "other"
            return self._forced_call(
                "open_support_case",
                {
                    "case_type": case_type,
                    "reference_id": reference,
                    "summary": reason,
                },
            )
        if stage == "transfer":
            return self._forced_call(
                "transfer_to_human_agents", {"summary": reason}
            )
        self.state.handoff_stage = "complete"
        return AssistantMessage(
            role="assistant",
            content=(
                "I'm transferring this conversation to a human support specialist now. "
                f"No additional automatic change has been made. {STOP}"
            ),
            raw_data={"risk_control": {"source": "controller"}},
            cost=0.0,
        )

    def before_agent_message(self, orchestrator) -> AssistantMessage | None:
        if self.state.context.risk_level == "L2":
            if self.state.handoff_stage == "none":
                self._start_handoff("L2 conversation risk requires human handling.")
            return self._next_handoff_message()
        return None

    def after_agent_message(self, message: AssistantMessage, orchestrator) -> AssistantMessage:
        risk_metadata = (message.raw_data or {}).get("risk_control", {})
        if risk_metadata.get("source") == "controller":
            return message
        if message.content:
            self._scan_visible_text(message.content, "agent_message")
            if self.state.context.risk_level == "L2":
                self.audit("reply.blocked_for_handoff", draft=message.content)
                self._start_handoff("Sensitive conversation content requires human handling.")
                return self._next_handoff_message()
        if message.tool_calls and len(message.tool_calls) > 1:
            write_calls = [
                call
                for call in message.tool_calls
                if self.environment.tools.has_tool(call.name)
                and self.environment.tools.tool_type(call.name) == ToolType.WRITE
            ]
            if write_calls:
                self.audit("action.blocked", rule="risk.single_write")
                return AssistantMessage(
                    role="assistant",
                    content="I need to handle and confirm one state-changing action at a time.",
                    cost=message.cost,
                    usage=message.usage,
                    raw_data=message.raw_data,
                )
        if message.tool_calls and len(message.tool_calls) == 1:
            call = message.tool_calls[0]
            is_write = (
                self.environment.tools.has_tool(call.name)
                and self.environment.tools.tool_type(call.name) == ToolType.WRITE
                and call.name not in HANDOFF_TOOLS
            )
            if is_write:
                self.state.context.customer_id = (
                    self.environment.tools.authenticated_user_id
                )
                decision = self.gate.check(self.environment, self.state, call)
                self.audit(
                    "action.decision",
                    phase="proposal",
                    call=call.model_dump(),
                    **decision.model_dump(),
                )
                if decision.outcome == "NEED_CONFIRMATION":
                    pending = self._create_pending(call)
                    return self._controller_text(
                        self._confirmation_prompt(pending), original=message
                    )
                if decision.outcome == "NEED_HUMAN":
                    self._upgrade("L2", decision.rule, decision.reason)
                    self._start_handoff(decision.reason)
                    return self._next_handoff_message()
        return message

    def should_stop_after_agent_message(self, message: AssistantMessage) -> bool:
        risk_metadata = (message.raw_data or {}).get("risk_control", {})
        return (
            risk_metadata.get("source") == "controller"
            and self.state.handoff_stage == "complete"
            and bool(message.content)
            and STOP in message.content
        )

    def _confirmation_summary(self, call: ToolCall) -> str:
        summary = f"Operation: {call.name}\n" + json.dumps(
            call.arguments, ensure_ascii=False, indent=2
        )
        db = self.environment.tools.db
        if call.name in {"modify_pending_order_address", "modify_user_address"}:
            old = (
                db.orders[call.arguments["order_id"]].address
                if "order_id" in call.arguments
                else db.users[call.arguments["user_id"]].address
            )
            new = {
                key: value
                for key, value in call.arguments.items()
                if key not in {"order_id", "user_id"}
            }
            summary = (
                "Old address: "
                + json.dumps(old.model_dump(), ensure_ascii=False)
                + "\nNew address: "
                + json.dumps(new, ensure_ascii=False)
            )
        return summary

    def _create_pending(self, call: ToolCall) -> PendingAction:
        pending = PendingAction(
            id=uuid.uuid4().hex[:12],
            call=call.model_copy(deep=True),
            request_id=self.state.context.request_id,
            customer_id=self.environment.tools.authenticated_user_id,
            digest=action_digest(call),
            snapshot=owned_snapshot(self.environment),
            summary=self._confirmation_summary(call),
            expires_at=self.clock() + 600,
        )
        self.state.pending = pending
        self.audit("confirmation.requested", pending=pending.model_dump(mode="json"))
        return pending

    @staticmethod
    def _confirmation_prompt(pending: PendingAction) -> str:
        return (
            pending.summary
            + "\nPlease confirm this exact operation by replying with exactly yes or no. "
            + f"Confirmation ID: {pending.id}. No change has been made yet."
        )

    def _blocked(self, call: ToolCall, decision, extra: str = "") -> ToolMessage:
        content = decision.model_dump_json()
        if extra:
            content += "\n" + extra
        return ToolMessage(
            role="tool",
            id=call.id,
            requestor="assistant",
            error=True,
            content=content,
            raw_data={
                "risk_control": {
                    "executed": False,
                    "outcome": decision.outcome,
                    "rule": decision.rule,
                }
            },
        )

    def before_tool_call(self, call: ToolCall, orchestrator) -> ToolMessage | None:
        if not call.id:
            call.id = uuid.uuid4().hex
        self.state.context.customer_id = self.environment.tools.authenticated_user_id
        decision = self.gate.check(self.environment, self.state, call)
        self.audit(
            "action.decision",
            phase="execution",
            call=call.model_dump(),
            **decision.model_dump(),
        )

        if call.id in self._forced_call_ids:
            if decision.outcome == "ALLOW":
                self._executing[call.id] = {"forced": True}
                return None
            return self._blocked(call, decision)

        pending = self.state.pending
        if pending is not None and pending.confirmed:
            valid = (
                pending.expires_at > self.clock()
                and pending.policy_version == self.state.version
                and pending.customer_id == self.environment.tools.authenticated_user_id
                and pending.request_id == self.state.context.request_id
                and pending.digest == action_digest(call)
                and pending.snapshot == owned_snapshot(self.environment)
            )
            if valid and decision.outcome == "NEED_CONFIRMATION":
                key = f"{pending.customer_id}:{pending.request_id}:{pending.digest}"
                if key in self.state.receipts:
                    return ToolMessage(
                        role="tool",
                        id=call.id,
                        requestor="assistant",
                        error=True,
                        content="This exact operation was already processed; it was not repeated.",
                        raw_data={
                            "risk_control": {
                                "executed": False,
                                "outcome": "DENY",
                                "rule": "risk.idempotency",
                            }
                        },
                    )
                self._executing[call.id] = {
                    "receipt_key": key,
                    "credit_cents": round(
                        float(decision.details.get("estimated_credit", 0)) * 100
                    ),
                }
                return None
            self.audit("confirmation.invalidated", pending_id=pending.id)
            self.state.pending = None

        if decision.outcome == "ALLOW":
            self._executing[call.id] = {}
            return None
        if decision.outcome == "NEED_CONFIRMATION":
            pending = self._create_pending(call)
            prompt = self._confirmation_prompt(pending)
            return self._blocked(call, decision, prompt)
        if decision.outcome == "NEED_HUMAN":
            self._upgrade("L2", decision.rule, decision.reason)
            self._start_handoff(decision.reason)
        return self._blocked(call, decision)

    def after_tool_call(self, call: ToolCall, result: ToolMessage, orchestrator) -> None:
        execution = self._executing.pop(call.id, None)
        self.audit(
            "action.result",
            call=call.model_dump(),
            error=result.error,
            executed=execution is not None,
        )
        if call.id in self._forced_call_ids:
            self._forced_call_ids.discard(call.id)
            if call.name == "open_support_case":
                self.state.handoff_stage = "transfer"
            elif call.name == "transfer_to_human_agents":
                self.state.handoff_stage = "reply"
            return
        if not result.error and call.name in {
            "get_order_fee_details",
            "assess_missing_item_claim",
        }:
            try:
                data = json.loads(result.content or "{}")
            except (TypeError, ValueError):
                data = {}
            requires_human = str(data.get("requires_human_transfer", "false")).lower()
            if requires_human == "true":
                self._upgrade(
                    "L2",
                    "risk.tool_evidence_requires_human",
                    "The trusted business assessment requires human handling.",
                )
                self._start_handoff(
                    "The trusted business assessment requires human handling."
                )
        if execution is None:
            return
        if not result.error:
            receipt_key = execution.get("receipt_key")
            if receipt_key:
                self.state.receipts[receipt_key] = {
                    "call_id": call.id,
                    "digest": action_digest(call),
                }
                self.state.spent_cents += execution.get("credit_cents", 0)
            if call.name in {"modify_pending_order_address", "modify_user_address"}:
                self.state.address_changes += 1
        if self.state.pending and self.state.pending.confirmed:
            self.state.pending = None
        self.state.context.customer_id = self.environment.tools.authenticated_user_id

    def export(self) -> dict:
        return self.state.model_dump(mode="json")

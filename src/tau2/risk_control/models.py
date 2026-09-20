"""Trusted state for the Retail Plus orchestrator middleware."""

from __future__ import annotations

from typing import Any, Literal
import time

from pydantic import BaseModel, Field

from tau2.data_model.message import ToolCall

POLICY_VERSION = "retail-plus-risk-v2"
RiskLevel = Literal["L0", "L1", "L2"]


class Decision(BaseModel):
    outcome: Literal["ALLOW", "DENY", "NEED_INFO", "NEED_CONFIRMATION", "NEED_HUMAN"]
    rule: str
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)


class RequestContext(BaseModel):
    request_id: int = 0
    intents: set[str] = Field(default_factory=set)
    resources: set[str] = Field(default_factory=set)
    risk_level: RiskLevel = "L0"
    customer_id: str | None = None
    classified: bool = False


class PendingAction(BaseModel):
    id: str
    call: ToolCall
    request_id: int
    customer_id: str
    digest: str
    snapshot: str
    summary: str
    expires_at: float
    confirmed: bool = False
    policy_version: str = POLICY_VERSION


class AuditEvent(BaseModel):
    sequence: int
    timestamp: float
    kind: str
    details: dict[str, Any]


class SessionState(BaseModel):
    version: str = POLICY_VERSION
    session_id: str
    task_id: str
    started_at: float = Field(default_factory=time.time)
    context: RequestContext = Field(default_factory=RequestContext)
    pending: PendingAction | None = None
    events: list[AuditEvent] = Field(default_factory=list)
    receipts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    spent_cents: int = 0
    address_changes: int = 0
    classifier_cost: float = 0.0
    classifier_calls: int = 0
    handoff_stage: Literal[
        "none", "open_case", "transfer", "reply", "complete"
    ] = "none"
    handoff_reason: str | None = None

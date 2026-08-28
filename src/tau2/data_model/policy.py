"""Structured policy-evaluation models shared by all domains."""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class PolicySeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PolicyAssertion(BaseModel):
    """A task-level declaration that a policy rule is applicable."""

    rule_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    severity: PolicySeverity = PolicySeverity.HIGH


class PolicyViolationCheck(BaseModel):
    """One observed policy violation in a simulation trajectory."""

    rule_id: str
    description: str
    severity: PolicySeverity = PolicySeverity.HIGH
    blocked: bool = True
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    turn_idx: Optional[int] = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class PolicyViolationError(ValueError):
    """Raised after a domain records and blocks a policy-violating operation."""

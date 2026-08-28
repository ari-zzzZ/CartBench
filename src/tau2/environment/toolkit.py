from enum import Enum
from typing import Annotated, Any, Callable, Dict, Optional, TypeVar

from pydantic import BaseModel, Field

from tau2.environment.db import DB
from tau2.environment.tool import Tool, as_tool
from tau2.data_model.message import Message, ToolCall
from tau2.data_model.policy import (
    PolicySeverity,
    PolicyViolationCheck,
    PolicyViolationError,
)
from tau2.data_model.tasks import Task
from tau2.utils import update_pydantic_model_with_dict

TOOL_ATTR = "__tool__"
TOOL_TYPE_ATTR = "__tool_type__"


T = TypeVar("T", bound=DB)


class ToolKitType(type):
    """Metaclass for ToolKit classes."""

    def __init__(cls, name, bases, attrs):
        func_tools = {}
        for name, method in attrs.items():
            if isinstance(method, property):
                method = method.fget
            if hasattr(method, TOOL_ATTR):
                func_tools[name] = method

        @property
        def _func_tools(self) -> Dict[str, Callable]:
            """Get the tools available in the ToolKit."""
            all_func_tools = func_tools.copy()
            try:
                all_func_tools.update(super(cls, self)._func_tools)
            except AttributeError:
                pass
            return all_func_tools

        cls._func_tools = _func_tools


class ToolType(str, Enum):
    """Type of a tool."""

    READ = "read"
    WRITE = "write"
    THINK = "think"
    GENERIC = "generic"


def is_tool(tool_type: ToolType = ToolType.READ):
    """Decorator to mark a function as a tool.

    Args:
        write: Whether this tool modifies state (True) or just reads state (False)
    """

    def decorator(func):
        setattr(func, TOOL_ATTR, True)
        setattr(func, TOOL_TYPE_ATTR, tool_type)
        return func

    return decorator


class ToolKitBase(metaclass=ToolKitType):
    """Base class for ToolKit classes."""

    def __init__(self, db: Optional[T] = None):
        self.db: Optional[T] = db
        self._policy_violations: list[PolicyViolationCheck] = []
        self._current_tool_call: Optional[ToolCall] = None

    @property
    def tools(self) -> Dict[str, Callable]:
        """Get the tools available in the ToolKit."""
        return {name: getattr(self, name) for name in self._func_tools.keys()}

    def use_tool(self, tool_name: str, **kwargs) -> str:
        """Use a tool."""
        if tool_name not in self.tools:
            raise ValueError(f"Tool '{tool_name}' not found.")
        return self.tools[tool_name](**kwargs)

    def get_tools(self) -> Dict[str, Tool]:
        """Get the tools available in the ToolKit.
        Uses the `as_tool` to convert the functions to Tool objects.

        Returns:
            A dictionary of tools available in the ToolKit.
        """
        # NOTE: as_tool needs to get the function (self.foo), not the `foo(self, ...)`
        # Otherwise, the `self` will exists in the arguments.
        # Therefore, it needs to be called with getattr(self, name)
        return {name: as_tool(tool) for name, tool in self.tools.items()}

    def has_tool(self, tool_name: str) -> bool:
        """Check if a tool exists in the ToolKit."""
        return tool_name in self.tools

    def tool_type(self, tool_name: str) -> ToolType:
        """Get the type of a tool."""
        return getattr(self.tools[tool_name], TOOL_TYPE_ATTR)

    def get_statistics(self) -> dict[str, Any]:
        """Get the statistics of the ToolKit."""
        num_tools = len(self.tools)
        num_read_tools = sum(
            self.tool_type(name) == ToolType.READ for name in self.tools
        )
        num_write_tools = sum(
            self.tool_type(name) == ToolType.WRITE for name in self.tools
        )
        num_think_tools = sum(
            self.tool_type(name) == ToolType.THINK for name in self.tools
        )
        num_generic_tools = sum(
            self.tool_type(name) == ToolType.GENERIC for name in self.tools
        )
        return {
            "num_tools": num_tools,
            "num_read_tools": num_read_tools,
            "num_write_tools": num_write_tools,
            "num_think_tools": num_think_tools,
            "num_generic_tools": num_generic_tools,
        }

    def update_db(self, update_data: Optional[dict[str, Any]] = None):
        """Update the database of the ToolKit."""
        if update_data is None:
            update_data = {}
        if self.db is None:
            raise ValueError("Database has not been initialized.")
        self.db = update_pydantic_model_with_dict(self.db, update_data)

    def get_db_hash(self) -> str:
        """Get the hash of the database."""
        # Delegate to the DB model so domains can exclude non-business fields
        # (for example, free-text handoff notes) from strict state evaluation.
        return self.db.get_hash()

    def set_current_tool_call(self, tool_call: Optional[ToolCall]) -> None:
        """Attach execution context used by structured policy events."""
        self._current_tool_call = tool_call

    def get_policy_rule_ids(self) -> set[str]:
        """Return domain policy rules supported by this toolkit."""
        return set()

    def record_policy_violation(
        self,
        rule_id: str,
        description: str,
        *,
        severity: PolicySeverity = PolicySeverity.HIGH,
        blocked: bool = True,
        evidence: Optional[dict[str, Any]] = None,
        tool_name: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        turn_idx: Optional[int] = None,
    ) -> PolicyViolationCheck:
        """Record a structured policy violation without modifying the DB."""
        tool_call = self._current_tool_call
        violation = PolicyViolationCheck(
            rule_id=rule_id,
            description=description,
            severity=severity,
            blocked=blocked,
            tool_name=(
                tool_name
                if tool_name is not None
                else tool_call.name
                if tool_call is not None
                else None
            ),
            tool_call_id=(
                tool_call_id
                if tool_call_id is not None
                else tool_call.id
                if tool_call is not None
                else None
            ),
            turn_idx=turn_idx,
            evidence=evidence or {},
        )
        self._policy_violations.append(violation)
        return violation

    def raise_policy_violation(
        self,
        rule_id: str,
        description: str,
        *,
        severity: PolicySeverity = PolicySeverity.HIGH,
        evidence: Optional[dict[str, Any]] = None,
    ) -> None:
        """Record and block a policy-violating operation."""
        self.record_policy_violation(
            rule_id,
            description,
            severity=severity,
            blocked=True,
            evidence=evidence,
        )
        raise PolicyViolationError(description)

    def finalize_policy_evaluation(self, trajectory: list[Message], task: Task) -> None:
        """Allow a domain to add end-of-conversation obligation violations."""

    def get_policy_violations(self) -> list[PolicyViolationCheck]:
        """Return a copy of structured policy events recorded so far."""
        return list(self._policy_violations)


class ToolSignature(BaseModel):
    """A signature of a tool."""

    name: Annotated[str, Field(description="The name of the tool")]
    doc: Annotated[str, Field(description="The documentation of the tool")]
    params: Annotated[
        Optional[dict],
        Field(description="JSON schema of the parameters of the tool", default=None),
    ]
    returns: Annotated[
        Optional[dict],
        Field(description="JSON schema of the return of the tool", default=None),
    ]


def get_tool_signatures(tools: ToolKitBase) -> dict[str, ToolSignature]:
    """Get all the tool signatures from a tool kit.

    Returns:
        A dictionary of tool signatures.
    """
    signatures = {}
    for name, tool in tools.get_tools().items():
        signatures[name] = ToolSignature(
            name=name,
            doc=str(tool),
            params=tool._serialize_params(tool.params),
            returns=tool._serialize_returns(tool.returns),
        )
    return signatures


def get_tool_types(tools: ToolKitBase) -> dict[str, ToolType]:
    """Get the type of a tool.

    Returns:
        A dictionary of tool types.
    """
    return {name: tools.tool_type(name) for name in tools.get_tools().keys()}


class GenericToolKit(ToolKitBase):
    """Defines some generic tools.
    - Think
    - Calculate
    """

    @is_tool(ToolType.THINK)
    def think(self, thought: str) -> str:
        """
        Use the tool to think about something. It will not obtain new information or change the database, but just append the thought to the log. Use it when complex reasoning is needed.

        Args:
            thought: A thought to think about.

        Returns:
            Empty string
        """
        return ""

    @is_tool(ToolType.GENERIC)
    def calculate(self, expression: str) -> str:
        """
        Calculate the result of a mathematical expression.

        Args:
            expression: The mathematical expression to calculate, such as '2 + 2'. The expression can contain numbers, operators (+, -, *, /), parentheses, and spaces.

        Returns:
            The result of the mathematical expression.

        Raises:
            ValueError: If the expression is invalid.
        """
        if not all(char in "0123456789+-*/(). " for char in expression):
            raise ValueError("Invalid characters in expression")
        return str(round(float(eval(expression, {"__builtins__": None}, {})), 2))

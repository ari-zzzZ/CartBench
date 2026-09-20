"""Trusted preconditions for the local Retail Plus executor."""

import hashlib
import inspect
import json
import re
from copy import deepcopy

from pydantic import ValidationError

from tau2.data_model.message import ToolCall
from tau2.environment.toolkit import ToolType
from tau2.environment.tool import as_tool
from tau2.risk_control.models import Decision, SessionState
from tau2.risk_control.permissions import (
    AUTOMATED_WRITES,
    AUTH_TOOLS,
    CAPABILITIES,
    HANDOFF_TOOLS,
    PUBLIC_TOOLS,
)

AUTO_CREDIT_LIMIT_CENTS = 5_000
ADDRESS_TOOLS = {"modify_pending_order_address", "modify_user_address"}


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def action_digest(call: ToolCall) -> str:
    return digest({"name": call.name, "arguments": call.arguments})


def owned_snapshot(environment) -> str:
    """Version only this principal's business state."""
    toolkit = environment.tools
    uid = toolkit.authenticated_user_id
    if uid is None:
        return digest(None)
    db = toolkit.db
    orders = {
        key: val.model_dump() for key, val in db.orders.items() if val.user_id == uid
    }
    return digest(
        {
            "customer": db.users[uid].model_dump(),
            "orders": orders,
            "refunds": {
                key: val.model_dump()
                for key, val in db.refund_cases.items()
                if val.user_id == uid
            },
            "fees": {
                key: val.model_dump()
                for key, val in db.order_fees.items()
                if val.order_id in orders
            },
            "claims": {
                key: val.model_dump()
                for key, val in db.shipping_claims.items()
                if val.user_id == uid
            },
            "cases": {
                key: val.model_dump()
                for key, val in db.support_cases.items()
                if val.user_id == uid
            },
            "phone": db.customer_phones.get(uid),
        }
    )


def action_reference(call: ToolCall) -> str | None:
    for key in ("order_id", "refund_id", "fee_id", "reference_id", "user_id"):
        if call.arguments.get(key):
            return call.arguments[key]
    return None


def _customer_credit_total(db, user_id: str) -> float:
    """Return recorded refunds/credits for limit-delta checks."""

    order_ids = {key for key, order in db.orders.items() if order.user_id == user_id}
    payment_net_credit = sum(
        payment.amount if payment.transaction_type == "refund" else -payment.amount
        for order_id in order_ids
        for payment in db.orders[order_id].payment_history
        if payment.transaction_type in {"refund", "payment"}
    )
    refund_cases = sum(
        item.amount for item in db.refund_cases.values() if item.user_id == user_id
    )
    refund_claims = sum(
        item.amount
        for item in db.shipping_claims.values()
        if item.user_id == user_id and item.requested_resolution == "refund"
    )
    return round(payment_net_credit + refund_cases + refund_claims, 2)


def _exchange_credit(call: ToolCall, db) -> float:
    if call.name != "exchange_delivered_order_items":
        return 0.0
    order = db.orders[call.arguments["order_id"]]
    difference = 0.0
    for old_id, new_id in zip(
        call.arguments["item_ids"], call.arguments["new_item_ids"]
    ):
        old = next(item for item in order.items if item.item_id == old_id)
        new = db.products[old.product_id].variants[new_id]
        difference += new.price - old.price
    return round(max(0.0, -difference), 2)


class ActionGate:
    def check(self, environment, state: SessionState, call: ToolCall) -> Decision:
        def decision(outcome, rule, reason, **details):
            return Decision(
                outcome=outcome, rule=rule, reason=reason, details=details
            )

        toolkit = environment.tools
        if call.requestor != "assistant" or not toolkit.has_tool(call.name):
            return decision(
                "DENY", "risk.tool_scope", "Tool is not available to this agent."
            )
        try:
            inspect.signature(toolkit.tools[call.name]).bind(**call.arguments)
            as_tool(toolkit.tools[call.name]).params.model_validate(
                call.arguments, strict=True
            )
        except (TypeError, ValidationError):
            return decision(
                "DENY", "risk.arguments", "Tool arguments do not match its signature."
            )
        if call.name == "transfer_to_human_agents":
            return decision("ALLOW", "risk.handoff", "Immediate human transfer.")
        if call.name in PUBLIC_TOOLS | AUTH_TOOLS:
            # Authentication methods themselves enforce one principal per session.
            return decision("ALLOW", "risk.public", "Public lookup or identity lookup.")
        uid = toolkit.authenticated_user_id
        if uid is None:
            return decision(
                "NEED_INFO",
                "risk.authentication",
                "Authenticate using email or name and ZIP first.",
            )
        args = call.arguments
        db = toolkit.db
        if state.context.risk_level == "L2" and call.name not in HANDOFF_TOOLS:
            return decision(
                "NEED_HUMAN",
                "risk.level_l2",
                "This conversation is restricted to immediate human handling.",
            )
        # Do not run a tool to learn another customer's data or disclose its existence.
        for key in ("order_id", "refund_id", "fee_id", "reference_id", "user_id"):
            reference = args.get(key)
            if reference is None:
                continue
            owner = None
            if key == "user_id":
                owner = reference
            elif reference in db.orders:
                owner = db.orders[reference].user_id
            elif reference in db.refund_cases:
                owner = db.refund_cases[reference].user_id
            elif reference in db.order_fees:
                order = db.orders.get(db.order_fees[reference].order_id)
                owner = order.user_id if order else None
            if owner != uid:
                return decision(
                    "DENY",
                    "retail.customer_data_isolation",
                    "Resource is unavailable for this customer.",
                )
        required = CAPABILITIES.get(call.name)
        if required and not required.intersection(state.context.intents):
            return decision(
                "NEED_INFO",
                "risk.request_scope",
                "Ask the customer to specify the relevant business request.",
            )
        reference = action_reference(call)
        if state.context.resources and reference and call.name not in HANDOFF_TOOLS:
            related = {reference}
            if reference in db.order_fees:
                related.add(db.order_fees[reference].order_id)
            if reference in db.refund_cases:
                related.add(db.refund_cases[reference].order_id)
            if reference in db.orders:
                related.update(
                    f.refund_id
                    for f in db.refund_cases.values()
                    if f.order_id == reference
                )
                related.update(
                    f.fee_id for f in db.order_fees.values() if f.order_id == reference
                )
            if call.name != "get_user_details" and not related.intersection(
                state.context.resources
            ):
                return decision(
                    "NEED_INFO",
                    "risk.resource_scope",
                    "This resource is outside the customer's current request.",
                )
        write = (
            toolkit.tool_type(call.name) == ToolType.WRITE
            and call.name not in HANDOFF_TOOLS
        )
        if write and state.context.risk_level == "L0":
            return decision(
                "DENY",
                "risk.level_l0",
                "A read-only request cannot authorize a state-changing action.",
            )
        if write and call.name not in AUTOMATED_WRITES:
            return decision(
                "NEED_HUMAN",
                "risk.unsupported_write",
                "This write workflow requires human handling.",
            )
        if call.name in ADDRESS_TOOLS and state.address_changes >= 1:
            return decision(
                "NEED_HUMAN",
                "risk.address_change_limit",
                "Only one automatic address change is allowed per conversation.",
            )
        if (
            call.name == "modify_pending_order_address"
            and db.orders[args["order_id"]].status != "pending"
        ):
            return decision(
                "DENY",
                "risk.order_state",
                "Only an exactly pending order may change its address.",
            )
        if call.name == "waive_order_fee":
            fee = db.order_fees[args["fee_id"]]
            if round(fee.amount * 100) > AUTO_CREDIT_LIMIT_CENTS:
                return decision(
                    "NEED_HUMAN",
                    "risk.single_credit_limit",
                    "Automatic customer credits are limited to $50.00.",
                    estimated_credit=fee.amount,
                    limit=50.0,
                )
        # Run domain preconditions on a disposable copy, never mutate live state here.
        preview = deepcopy(environment)
        credit_before = _customer_credit_total(db, uid)
        response = preview.get_response(call)
        if response.error:
            events = preview.tools.get_policy_violations()
            new_events = events[len(toolkit.get_policy_violations()) :]
            rule = (
                new_events[-1].rule_id if new_events else "risk.business_precondition"
            )
            reason = response.content or "Business precondition failed."
            if re.search(r"human|manual.review|amount threshold", reason, re.I):
                return decision("NEED_HUMAN", rule, reason)
            return decision("DENY", rule, reason)
        if write:
            credit_after = _customer_credit_total(preview.tools.db, uid)
            estimated_credit = round(
                max(0.0, credit_after - credit_before)
                + _exchange_credit(call, db),
                2,
            )
            credit_cents = round(estimated_credit * 100)
            if credit_cents > AUTO_CREDIT_LIMIT_CENTS:
                return decision(
                    "NEED_HUMAN",
                    "risk.single_credit_limit",
                    "Automatic customer credits are limited to $50.00.",
                    estimated_credit=estimated_credit,
                    limit=50.0,
                )
            if state.spent_cents + credit_cents > AUTO_CREDIT_LIMIT_CENTS:
                return decision(
                    "NEED_HUMAN",
                    "risk.cumulative_credit_limit",
                    "Automatic customer credits in one conversation are limited to $50.00.",
                    estimated_credit=estimated_credit,
                    spent=state.spent_cents / 100,
                    limit=50.0,
                )
            return decision(
                "NEED_CONFIRMATION",
                "risk.explicit_confirmation",
                "Confirm the exact operation before execution.",
                estimated_credit=estimated_credit,
            )
        return decision("ALLOW", "risk.allowed", "All applicable preconditions passed.")

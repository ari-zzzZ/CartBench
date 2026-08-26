"""Executable tools and hard business constraints for Retail Plus."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import List

from tau2.domains.retail.data_model import (
    GiftCard,
    Order,
    OrderItem,
    OrderPayment,
    User,
)
from tau2.domains.retail.tools import RetailTools
from tau2.domains.retail_plus.constants import (
    FEE_AUTO_WAIVER_LIMIT,
    MANUAL_REVIEW_AMOUNT_THRESHOLD,
    REFERENCE_DATE,
)
from tau2.domains.retail_plus.data_model import (
    RefundCase,
    RetailPlusDB,
    ShippingClaim,
    SupportCase,
)
from tau2.environment.toolkit import ToolType, is_tool


class RetailPlusTools(RetailTools):
    """Retail tools plus refunds, vouchers, fees, claims, add-item, and phone flows."""

    db: RetailPlusDB

    def __init__(self, db: RetailPlusDB) -> None:
        super().__init__(db)
        self.authenticated_user_id: str | None = None

    def _require_authenticated_user(self, user_id: str | None = None) -> str:
        if self.authenticated_user_id is None:
            raise ValueError("Authenticate the customer before using this tool")
        if user_id is not None and user_id != self.authenticated_user_id:
            raise ValueError("Cannot access or modify another customer's account")
        return self.authenticated_user_id

    def _require_owned_order(self, order_id: str):
        user_id = self._require_authenticated_user()
        order = super()._get_order(order_id)
        if order.user_id != user_id:
            raise ValueError("Cannot access or modify another customer's order")
        return order

    def _find_item_and_product(self, item_id: str):
        for product in self.db.products.values():
            if item_id in product.variants:
                return product.variants[item_id], product
        raise ValueError("Item not found")

    def _ensure_no_refund_for_order(self, order_id: str) -> None:
        if any(case.order_id == order_id for case in self.db.refund_cases.values()):
            raise ValueError("A refund already exists for this order; duplicate refunds are prohibited")

    @is_tool(ToolType.READ)
    def find_user_id_by_email(self, email: str) -> str:
        """Authenticate a customer by email and return their user ID."""
        user_id = super().find_user_id_by_email(email)
        if self.authenticated_user_id not in {None, user_id}:
            raise ValueError("Only one customer can be authenticated per conversation")
        self.authenticated_user_id = user_id
        return user_id

    @is_tool(ToolType.READ)
    def find_user_id_by_name_zip(
        self, first_name: str, last_name: str, zip: str
    ) -> str:
        """Authenticate a customer by full name and ZIP code and return their user ID."""
        user_id = super().find_user_id_by_name_zip(first_name, last_name, zip)
        if self.authenticated_user_id not in {None, user_id}:
            raise ValueError("Only one customer can be authenticated per conversation")
        self.authenticated_user_id = user_id
        return user_id

    @is_tool(ToolType.READ)
    def get_order_details(self, order_id: str) -> Order:
        """Get an order only when it belongs to the authenticated customer."""
        return self._require_owned_order(order_id)

    @is_tool(ToolType.READ)
    def get_user_details(self, user_id: str) -> User:
        """Get only the authenticated customer's profile."""
        self._require_authenticated_user(user_id)
        return super().get_user_details(user_id)

    @is_tool(ToolType.WRITE)
    def cancel_pending_order(self, order_id: str, reason: str) -> Order:
        """Cancel an authenticated customer's pending order."""
        self._require_owned_order(order_id)
        return super().cancel_pending_order(order_id=order_id, reason=reason)

    @is_tool(ToolType.WRITE)
    def exchange_delivered_order_items(
        self,
        order_id: str,
        item_ids: List[str],
        new_item_ids: List[str],
        payment_method_id: str,
    ) -> Order:
        """Exchange items only in an authenticated customer's delivered order."""
        self._require_owned_order(order_id)
        return super().exchange_delivered_order_items(
            order_id=order_id,
            item_ids=item_ids,
            new_item_ids=new_item_ids,
            payment_method_id=payment_method_id,
        )

    @is_tool(ToolType.WRITE)
    def modify_pending_order_address(
        self,
        order_id: str,
        address1: str,
        address2: str,
        city: str,
        state: str,
        country: str,
        zip: str,
    ) -> Order:
        """Modify only an authenticated customer's pending-order address."""
        self._require_owned_order(order_id)
        return super().modify_pending_order_address(
            order_id=order_id,
            address1=address1,
            address2=address2,
            city=city,
            state=state,
            country=country,
            zip=zip,
        )

    @is_tool(ToolType.WRITE)
    def modify_pending_order_items(
        self,
        order_id: str,
        item_ids: List[str],
        new_item_ids: List[str],
        payment_method_id: str,
    ) -> Order:
        """Modify items only in an authenticated customer's pending order."""
        self._require_owned_order(order_id)
        return super().modify_pending_order_items(
            order_id=order_id,
            item_ids=item_ids,
            new_item_ids=new_item_ids,
            payment_method_id=payment_method_id,
        )

    @is_tool(ToolType.WRITE)
    def modify_pending_order_payment(
        self,
        order_id: str,
        payment_method_id: str,
    ) -> Order:
        """Modify payment only on an authenticated customer's pending order."""
        self._require_owned_order(order_id)
        return super().modify_pending_order_payment(
            order_id=order_id,
            payment_method_id=payment_method_id,
        )

    @is_tool(ToolType.WRITE)
    def modify_user_address(
        self,
        user_id: str,
        address1: str,
        address2: str,
        city: str,
        state: str,
        country: str,
        zip: str,
    ) -> User:
        """Modify only the authenticated customer's default address."""
        self._require_authenticated_user(user_id)
        return super().modify_user_address(
            user_id=user_id,
            address1=address1,
            address2=address2,
            city=city,
            state=state,
            country=country,
            zip=zip,
        )

    @is_tool(ToolType.READ)
    def get_refund_status(self, refund_id: str) -> dict:
        """Return the status, amount, destination, and timing of a refund case."""
        user_id = self._require_authenticated_user()
        if refund_id not in self.db.refund_cases:
            raise ValueError("Refund case not found")
        case = self.db.refund_cases[refund_id]
        if case.user_id != user_id:
            raise ValueError("Cannot access another customer's refund")
        return case.model_dump()

    @is_tool(ToolType.READ)
    def review_returned_item_billing(self, refund_id: str) -> dict:
        """Review whether a returned-item refund is overdue or requires manual review."""
        case_data = self.get_refund_status(refund_id)
        case = self.db.refund_cases[refund_id]
        overdue = bool(
            case.status not in {"completed", "failed"}
            and case.expected_by
            and date.fromisoformat(case.expected_by) < date.fromisoformat(REFERENCE_DATE)
        )
        return {
            **case_data,
            "overdue": overdue,
            "requires_human_transfer": overdue
            or case.amount >= MANUAL_REVIEW_AMOUNT_THRESHOLD,
            "duplicate_refund_prohibited": True,
        }

    @is_tool(ToolType.WRITE)
    def open_support_case(
        self,
        case_type: str,
        reference_id: str,
        summary: str,
    ) -> SupportCase:
        """Open a support case that must be followed by transfer to a human agent."""
        user_id = self._require_authenticated_user()
        valid_types = {
            "delayed_refund",
            "high_value_fee",
            "high_value_missing_item",
            "other",
        }
        if case_type not in valid_types:
            raise ValueError("Invalid support case type")

        amount = 0.0
        if reference_id in self.db.refund_cases:
            reference = self.db.refund_cases[reference_id]
            if reference.user_id != user_id:
                raise ValueError("Reference does not belong to the authenticated customer")
            amount = reference.amount
        elif reference_id in self.db.order_fees:
            fee = self.db.order_fees[reference_id]
            order = self._require_owned_order(fee.order_id)
            amount = fee.amount
        elif reference_id in self.db.orders:
            order = self._require_owned_order(reference_id)
            amount = sum(item.price for item in order.items)
        else:
            raise ValueError("Support case reference not found")

        safe_reference = re.sub(r"[^A-Za-z0-9]+", "-", reference_id).strip("-")
        case_id = f"CASE-{case_type.upper()}-{safe_reference}"
        if case_id in self.db.support_cases:
            raise ValueError("A support case already exists for this reference")
        support_case = SupportCase(
            case_id=case_id,
            case_type=case_type,
            reference_id=reference_id,
            user_id=user_id,
            summary=summary,
            amount=round(amount, 2),
            created_at=REFERENCE_DATE,
        )
        self.db.support_cases[case_id] = support_case
        return support_case

    @is_tool(ToolType.READ)
    def validate_voucher(self, code: str, order_id: str) -> dict:
        """Validate a voucher for the authenticated customer and pending order."""
        user_id = self._require_authenticated_user()
        order = self._require_owned_order(order_id)
        voucher = self.db.vouchers.get(code)
        if voucher is None:
            return {"valid": False, "reason": "invalid code"}
        if voucher.assigned_user_id not in {None, user_id}:
            return {"valid": False, "reason": "invalid for this account"}
        if voucher.status in {"expired", "disabled"} or date.fromisoformat(
            voucher.expires_at
        ) < date.fromisoformat(REFERENCE_DATE):
            return {
                "valid": False,
                "reason": "expired",
                "expires_at": voucher.expires_at,
                "cash_redeemable": False,
            }
        if voucher.status == "redeemed":
            return {"valid": False, "reason": "already redeemed"}
        order_total = round(
            sum(
                payment.amount
                for payment in order.payment_history
                if payment.transaction_type == "payment"
            ),
            2,
        )
        if order_total < voucher.minimum_order_amount:
            return {
                "valid": False,
                "reason": "minimum order amount not met",
                "minimum_order_amount": voucher.minimum_order_amount,
                "order_total": order_total,
                "cash_redeemable": False,
            }
        return {
            "valid": True,
            "discount_type": voucher.discount_type,
            "discount_value": voucher.discount_value,
            "cash_redeemable": False,
        }

    @is_tool(ToolType.READ)
    def get_order_fee_details(self, fee_id: str) -> dict:
        """Get a disputed order fee and whether it may be automatically waived."""
        if fee_id not in self.db.order_fees:
            raise ValueError("Order fee not found")
        fee = self.db.order_fees[fee_id]
        self._require_owned_order(fee.order_id)
        return {
            **fee.model_dump(),
            "automatic_waiver_allowed": fee.waivable
            and fee.amount <= FEE_AUTO_WAIVER_LIMIT,
            "requires_human_transfer": fee.amount
            >= MANUAL_REVIEW_AMOUNT_THRESHOLD,
        }

    @is_tool(ToolType.WRITE)
    def waive_order_fee(self, fee_id: str) -> dict:
        """Waive an eligible small fee and create exactly one refund case."""
        details = self.get_order_fee_details(fee_id)
        fee = self.db.order_fees[fee_id]
        if fee.status != "charged":
            raise ValueError("Fee is not in a charged state")
        if not details["automatic_waiver_allowed"]:
            raise ValueError("Fee cannot be automatically waived")
        if details["requires_human_transfer"]:
            raise ValueError("Amount threshold exceeded; transfer to a human agent")
        self._ensure_no_refund_for_order(fee.order_id)

        order = super()._get_order(fee.order_id)
        payment_method_id = order.payment_history[0].payment_method_id
        refund_id = f"REF-FEE-{fee_id}"
        refund = RefundCase(
            refund_id=refund_id,
            order_id=fee.order_id,
            user_id=order.user_id,
            item_ids=[],
            amount=fee.amount,
            payment_method_id=payment_method_id,
            status="processing",
            requested_at=REFERENCE_DATE,
            expected_by="2026-08-31",
            source="fee_waiver",
        )
        self.db.refund_cases[refund_id] = refund
        fee.status = "waived"
        fee.refund_id = refund_id
        return {"fee": fee.model_dump(), "refund": refund.model_dump()}

    @is_tool(ToolType.READ)
    def assess_missing_item_claim(self, order_id: str, item_ids: List[str]) -> dict:
        """Assess ownership, value, duplicates, and manual-review needs for missing items."""
        order = self._require_owned_order(order_id)
        if order.status != "delivered":
            raise ValueError("Missing-item claims require a delivered order")
        requested = Counter(item_ids)
        available = Counter(item.item_id for item in order.items)
        if any(requested[item_id] > available[item_id] for item_id in requested):
            raise ValueError("One or more items are not in the order")
        if any(claim.order_id == order_id for claim in self.db.shipping_claims.values()):
            raise ValueError("A shipping claim already exists for this order")
        prices = []
        remaining = requested.copy()
        for item in order.items:
            if remaining[item.item_id] > 0:
                prices.append(item.price)
                remaining[item.item_id] -= 1
        amount = round(sum(prices), 2)
        return {
            "order_id": order_id,
            "item_ids": item_ids,
            "amount": amount,
            "requires_human_transfer": amount >= MANUAL_REVIEW_AMOUNT_THRESHOLD,
        }

    @is_tool(ToolType.WRITE)
    def file_missing_item_claim(
        self,
        order_id: str,
        item_ids: List[str],
        requested_resolution: str,
    ) -> ShippingClaim:
        """File a low-value missing-item replacement or refund claim."""
        if requested_resolution not in {"replacement", "refund"}:
            raise ValueError("Invalid missing-item resolution")
        assessment = self.assess_missing_item_claim(order_id, item_ids)
        if assessment["requires_human_transfer"]:
            raise ValueError("Amount threshold exceeded; transfer to a human agent")
        user_id = self._require_authenticated_user()
        safe_order = re.sub(r"[^A-Za-z0-9]+", "", order_id)
        claim_id = f"CLAIM-{safe_order}"
        claim = ShippingClaim(
            claim_id=claim_id,
            order_id=order_id,
            user_id=user_id,
            item_ids=sorted(item_ids),
            amount=assessment["amount"],
            requested_resolution=requested_resolution,
            status="replacement requested"
            if requested_resolution == "replacement"
            else "open",
            created_at=REFERENCE_DATE,
        )
        self.db.shipping_claims[claim_id] = claim
        return claim

    @is_tool(ToolType.WRITE)
    def add_pending_order_items(
        self,
        order_id: str,
        item_ids: List[str],
        payment_method_id: str,
    ) -> dict:
        """Add available items to an order that is still pending."""
        order = self._require_owned_order(order_id)
        if order.status != "pending":
            raise ValueError("Items can only be added to a pending order")
        payment_method = self._get_payment_method(order.user_id, payment_method_id)

        new_items = []
        for item_id in item_ids:
            variant, product = self._find_item_and_product(item_id)
            if not variant.available:
                raise ValueError(f"Item {item_id} is out of stock")
            new_items.append(
                OrderItem(
                    name=product.name,
                    product_id=product.product_id,
                    item_id=variant.item_id,
                    price=variant.price,
                    options=variant.options,
                )
            )
        amount = round(sum(item.price for item in new_items), 2)
        if isinstance(payment_method, GiftCard) and payment_method.balance < amount:
            raise ValueError("Gift card balance is insufficient")
        if isinstance(payment_method, GiftCard):
            payment_method.balance = round(payment_method.balance - amount, 2)
        order.items.extend(new_items)
        order.payment_history.append(
            OrderPayment(
                transaction_type="payment",
                amount=amount,
                payment_method_id=payment_method_id,
            )
        )
        return {
            "order_id": order_id,
            "added_item_ids": item_ids,
            "added_amount": amount,
            "new_order_item_count": len(order.items),
        }

    @is_tool(ToolType.READ)
    def get_user_phone(self, user_id: str) -> str:
        """Get the authenticated customer's phone number."""
        self._require_authenticated_user(user_id)
        if user_id not in self.db.customer_phones:
            raise ValueError("Phone number not found")
        return self.db.customer_phones[user_id]

    @is_tool(ToolType.WRITE)
    def modify_user_phone(self, user_id: str, new_phone: str) -> str:
        """Change the authenticated customer's phone after explicit confirmation."""
        self._require_authenticated_user(user_id)
        digits = re.sub(r"\D", "", new_phone)
        if len(digits) != 10:
            raise ValueError("Phone number must contain exactly 10 digits")
        normalized = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
        self.db.customer_phones[user_id] = normalized
        return normalized

    @is_tool(ToolType.WRITE)
    def return_delivered_order_items(
        self,
        order_id: str,
        item_ids: List[str],
        payment_method_id: str,
        reason: str = "no reason provided",
    ):
        """Request a return while enforcing category rules and duplicate-refund protection."""
        self._require_owned_order(order_id)
        normalized_reason = reason.strip().lower()
        for item_id in item_ids:
            policy = self.db.item_return_policies.get(item_id)
            if (
                policy is not None
                and not policy.returnable_without_reason
                and normalized_reason not in {item.lower() for item in policy.allowed_reasons}
            ):
                raise ValueError(
                    f"{policy.category} items cannot be returned without an eligible reason"
                )
        self._ensure_no_refund_for_order(order_id)
        order = super().return_delivered_order_items(
            order_id=order_id,
            item_ids=item_ids,
            payment_method_id=payment_method_id,
        )
        amount = 0.0
        remaining = Counter(item_ids)
        for item in order.items:
            if remaining[item.item_id] > 0:
                amount += item.price
                remaining[item.item_id] -= 1
        safe_order = re.sub(r"[^A-Za-z0-9]+", "", order_id)
        refund_id = f"REF-RETURN-{safe_order}"
        self.db.refund_cases[refund_id] = RefundCase(
            refund_id=refund_id,
            order_id=order_id,
            user_id=order.user_id,
            item_ids=sorted(item_ids),
            amount=round(amount, 2),
            payment_method_id=payment_method_id,
            status="requested",
            requested_at=REFERENCE_DATE,
            expected_by="2026-08-31",
            source="item_return",
        )
        return order

"""Data model extensions for Retail Plus."""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from tau2.domains.retail.data_model import RetailDB
from tau2.utils import get_dict_hash


RefundStatus = Literal["requested", "processing", "completed", "failed"]
VoucherStatus = Literal["active", "expired", "redeemed", "disabled"]
FeeStatus = Literal["charged", "waived", "upheld"]
ClaimStatus = Literal["open", "replacement requested", "resolved", "rejected"]
SupportCaseStatus = Literal["open", "closed"]


class RefundCase(BaseModel):
    refund_id: str
    order_id: str
    user_id: str
    item_ids: List[str] = Field(default_factory=list)
    amount: float
    payment_method_id: str
    status: RefundStatus
    requested_at: str
    expected_by: Optional[str] = None
    completed_at: Optional[str] = None
    failure_reason: Optional[str] = None
    source: Literal["item_return", "order_cancel", "fee_waiver"]


class Voucher(BaseModel):
    code: str
    status: VoucherStatus
    discount_type: Literal["fixed", "percentage"]
    discount_value: float
    issued_at: str
    expires_at: str
    assigned_user_id: Optional[str] = None
    minimum_order_amount: float = 0.0
    cash_redeemable: bool = False
    redeemed_order_id: Optional[str] = None


class OrderFee(BaseModel):
    fee_id: str
    order_id: str
    fee_type: str
    amount: float
    explanation: str
    status: FeeStatus = "charged"
    waivable: bool = False
    refund_id: Optional[str] = None


class ShippingClaim(BaseModel):
    claim_id: str
    order_id: str
    user_id: str
    item_ids: List[str]
    amount: float
    requested_resolution: Literal["replacement", "refund"]
    status: ClaimStatus
    created_at: str


class SupportCase(BaseModel):
    case_id: str
    case_type: Literal[
        "delayed_refund", "high_value_fee", "high_value_missing_item", "other"
    ]
    reference_id: str
    user_id: str
    summary: str
    amount: float = 0.0
    status: SupportCaseStatus = "open"
    requires_human_transfer: bool = True
    created_at: str


class ItemReturnPolicy(BaseModel):
    item_id: str
    category: Literal["standard", "food", "customized", "hygiene"]
    returnable_without_reason: bool = True
    allowed_reasons: List[str] = Field(
        default_factory=lambda: ["defective", "damaged", "wrong item"]
    )


class RetailPlusDB(RetailDB):
    customer_phones: Dict[str, str] = Field(default_factory=dict)
    refund_cases: Dict[str, RefundCase] = Field(default_factory=dict)
    vouchers: Dict[str, Voucher] = Field(default_factory=dict)
    order_fees: Dict[str, OrderFee] = Field(default_factory=dict)
    shipping_claims: Dict[str, ShippingClaim] = Field(default_factory=dict)
    support_cases: Dict[str, SupportCase] = Field(default_factory=dict)
    item_return_policies: Dict[str, ItemReturnPolicy] = Field(default_factory=dict)

    def get_hash(self) -> str:
        """Hash business state while ignoring free-text human handoff prose."""
        hash_data = self.model_dump()
        for support_case in hash_data["support_cases"].values():
            support_case.pop("summary", None)
        return get_dict_hash(hash_data)

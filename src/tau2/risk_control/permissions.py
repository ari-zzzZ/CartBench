"""Conservative visible-request routing; domain authorization remains authoritative."""

import re

from tau2.risk_control.models import RequestContext

INTENT_PATTERNS = {
    "address": r"address|地址",
    "fee": r"\bfee\b|\bfees\b|surcharge|费用|手续费|附加费",
    "refund": r"refund|退款|到账",
    "missing": r"missing|缺件|少件|漏发",
    "phone": r"phone|电话|手机号",
    "add": r"\badd\b|加购|追加",
    "cancel": r"cancel|取消",
    "return": r"\breturn\b|退货",
    "exchange": r"exchange|换货",
    "payment": r"payment|付款|支付方式",
    "voucher": r"voucher|coupon|优惠券",
    "order": r"\border\b|订单|物流",
}
CAPABILITIES = {
    "modify_pending_order_address": {"address"},
    "modify_user_address": {"address"},
    "waive_order_fee": {"fee"},
    "get_order_fee_details": {"fee"},
    "get_refund_status": {"refund"},
    "review_returned_item_billing": {"refund"},
    "assess_missing_item_claim": {"missing"},
    "file_missing_item_claim": {"missing"},
    "modify_user_phone": {"phone"},
    "get_user_phone": {"phone"},
    "add_pending_order_items": {"add"},
    "cancel_pending_order": {"cancel"},
    "return_delivered_order_items": {"return"},
    "exchange_delivered_order_items": {"exchange"},
    "modify_pending_order_payment": {"payment"},
    "modify_pending_order_items": {"exchange", "add"},
    "validate_voucher": {"voucher"},
}
AUTH_TOOLS = {"find_user_id_by_email", "find_user_id_by_name_zip"}
PUBLIC_TOOLS = {
    "get_product_details",
    "get_item_details",
    "list_all_product_types",
    "calculate",
}
HANDOFF_TOOLS = {"open_support_case", "transfer_to_human_agents"}
AUTOMATED_WRITES = {
    "cancel_pending_order",
    "exchange_delivered_order_items",
    "modify_pending_order_address",
    "modify_pending_order_items",
    "modify_pending_order_payment",
    "modify_user_address",
    "waive_order_fee",
    "file_missing_item_claim",
    "add_pending_order_items",
    "modify_user_phone",
    "return_delivered_order_items",
}

L1_PATTERNS = re.compile(
    r"refund\s+me|request(?:ing)?\s+(?:a\s+)?refund|want\s+(?:a\s+)?refund|"
    r"money\s+back|compensat|waive|issue\s+(?:a\s+)?(?:voucher|coupon)|"
    r"(?:change|update|modify)\s+(?:my\s+)?(?:address|phone|payment)|"
    r"cancel\s+(?:my\s+)?order|return\s+(?:this|the|my)|exchange\s+(?:this|the|my)|"
    r"add\s+(?:an?\s+)?item|missing\s+item|"
    r"申请退款|给我退款|我要退款|退钱|赔偿|补偿|减免|发.{0,4}优惠券|"
    r"修改.{0,4}(?:地址|电话|手机号|付款|支付)|更换.{0,4}(?:地址|付款|支付)|"
    r"取消订单|退货|换货|加购|追加|缺件|少件|漏发",
    re.I,
)

L2_PATTERNS = re.compile(
    r"投诉|起诉|告你们|告到法院|法院|律师|12315|举报|曝光|"
    r"他妈的|妈的|操你|傻逼|fuck|fucking|shit|asshole|"
    r"自杀|不想活|杀死自己|致死|农药|中毒|"
    r"complain|complaint|lawsuit|sue\b|court|lawyer|legal action|"
    r"kill myself|suicide|fatal dose|poison",
    re.I,
)


def update_request(context: RequestContext, text: str) -> RequestContext:
    intents = {
        name
        for name, pattern in INTENT_PATTERNS.items()
        if re.search(pattern, text, re.I)
    }
    resources = {
        value.upper()
        for value in re.findall(r"#W\d+|(?:REF|FEE|CLAIM)-[A-Za-z0-9-]+", text, re.I)
    }
    # New explicit business requests replace capabilities; short answers retain them.
    meaningful = intents - {"order"}
    if meaningful or (resources and resources != context.resources):
        return RequestContext(
            request_id=context.request_id + 1,
            intents=intents or context.intents,
            resources=resources,
            customer_id=context.customer_id,
            risk_level=context.risk_level,
            classified=context.classified,
        )
    return context.model_copy(
        update={
            "intents": context.intents | intents,
            "resources": context.resources | resources,
        }
    )


def deterministic_risk_signal(text: str) -> str | None:
    """Return only monotonic risk upgrades from visible conversation text."""

    if L2_PATTERNS.search(text):
        return "L2"
    if L1_PATTERNS.search(text):
        return "L1"
    return None

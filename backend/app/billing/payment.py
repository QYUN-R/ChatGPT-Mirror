from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.utils import timezone

from app.billing.exceptions import PaymentRejected


ALIPAY_SUCCESS_STATUSES = {"TRADE_SUCCESS", "TRADE_FINISHED"}
ALIPAY_CLOSED_STATUSES = {"TRADE_CLOSED"}


@dataclass(frozen=True)
class PaymentEvent:
    event_id: str
    event_type: str
    provider_transaction_id: str
    amount_cents: int
    currency: str
    signature_verified: bool
    occurred_at: datetime = field(default_factory=timezone.now)
    payload: dict[str, Any] = field(default_factory=dict)
    order_no: str = ""
    app_id: str = ""


@dataclass(frozen=True)
class PaymentQuery:
    order_no: str
    trade_status: str
    provider_transaction_id: str
    amount_cents: int
    currency: str
    app_id: str = ""
    successful_response: bool = False
    payload: dict[str, Any] = field(default_factory=dict)


def cny_to_cents(value: Any) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PaymentRejected("支付宝返回金额格式无效", code="invalid_payment_amount") from exc
    rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount != rounded or rounded < 0:
        raise PaymentRejected("支付宝返回金额格式无效", code="invalid_payment_amount")
    return int(rounded * 100)


def cents_to_cny(value: int) -> str:
    return format((Decimal(int(value)) / Decimal(100)).quantize(Decimal("0.01")), ".2f")


class PaymentProvider(ABC):
    code = "base"

    def is_configured(self) -> bool:
        return True

    @abstractmethod
    def create_order(self, order):
        raise NotImplementedError

    @abstractmethod
    def verify_callback(self, payload, headers=None):
        raise NotImplementedError

    @abstractmethod
    def query_order(self, order):
        raise NotImplementedError

    @abstractmethod
    def close_order(self, order):
        raise NotImplementedError

    @abstractmethod
    def refund_order(self, order, amount_cents=None):
        raise NotImplementedError


class MockPaymentProvider(PaymentProvider):
    code = "mock"

    def create_order(self, order):
        return {
            "provider": self.code,
            "order_no": order.order_no,
            "pay_url": f"mock://pay/{order.order_no}",
            "expires_at": order.payment_expires_at,
        }

    def verify_callback(self, payload, headers=None):
        return PaymentEvent(
            event_id=str(payload.get("event_id") or f"mock-{uuid4().hex}"),
            event_type=str(payload.get("event_type") or "PAYMENT_SUCCEEDED"),
            provider_transaction_id=str(payload.get("transaction_id") or f"mock-tx-{uuid4().hex}"),
            amount_cents=int(payload["amount_cents"]),
            currency=str(payload.get("currency") or "CNY"),
            signature_verified=True,
            payload={"mode": "mock"},
            order_no=str(payload.get("order_no") or ""),
        )

    def query_order(self, order):
        return PaymentQuery(
            order_no=order.order_no,
            trade_status=order.status,
            provider_transaction_id=order.provider_order_id,
            amount_cents=order.price_cents,
            currency=order.currency,
            successful_response=True,
            payload={"mode": "mock"},
        )

    def close_order(self, order):
        return {"closed": True, "order_no": order.order_no}

    def refund_order(self, order, amount_cents=None):
        return {
            "refunded": True,
            "order_no": order.order_no,
            "amount_cents": int(amount_cents or order.price_cents),
        }


class AlipayPagePaymentProvider(PaymentProvider):
    code = "alipay"

    def is_configured(self) -> bool:
        required = (
            settings.ALIPAY_APP_ID,
            settings.ALIPAY_APP_PRIVATE_KEY_PATH,
            settings.ALIPAY_PUBLIC_KEY_PATH,
            settings.ALIPAY_NOTIFY_URL,
            settings.ALIPAY_RETURN_URL,
        )
        return bool(all(required)) and Path(settings.ALIPAY_APP_PRIVATE_KEY_PATH).is_file() and Path(
            settings.ALIPAY_PUBLIC_KEY_PATH
        ).is_file()

    def _read_key(self, value: str, label: str) -> str:
        try:
            content = Path(value).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise PaymentRejected(f"支付宝{label}不可读取", code="payment_not_configured") from exc
        if not content:
            raise PaymentRejected(f"支付宝{label}为空", code="payment_not_configured")
        return content

    def _client(self):
        if not self.is_configured():
            raise PaymentRejected("支付宝支付尚未配置", code="payment_not_configured")
        try:
            from alipay import AliPay, AliPayConfig

            return AliPay(
                appid=settings.ALIPAY_APP_ID,
                app_notify_url=settings.ALIPAY_NOTIFY_URL,
                app_private_key_string=self._read_key(settings.ALIPAY_APP_PRIVATE_KEY_PATH, "应用私钥"),
                alipay_public_key_string=self._read_key(settings.ALIPAY_PUBLIC_KEY_PATH, "支付宝公钥"),
                sign_type=settings.ALIPAY_SIGN_TYPE,
                debug=settings.ALIPAY_ENV == "sandbox",
                config=AliPayConfig(timeout=settings.ALIPAY_HTTP_TIMEOUT_SECONDS),
            )
        except PaymentRejected:
            raise
        except Exception as exc:
            raise PaymentRejected("支付宝支付初始化失败", code="payment_not_configured") from exc

    @staticmethod
    def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
        allowed = (
            "app_id",
            "out_trade_no",
            "trade_no",
            "trade_status",
            "total_amount",
            "receipt_amount",
            "buyer_pay_amount",
            "gmt_payment",
            "notify_id",
            "seller_id",
            "code",
            "sub_code",
            "msg",
            "sub_msg",
        )
        return {key: str(payload[key])[:256] for key in allowed if payload.get(key) not in (None, "")}

    def create_order(self, order):
        client = self._client()
        subject = f"{order.plan.name} {order.offer.name}".strip()[:128]
        signed_query = client.api_alipay_trade_page_pay(
            subject=subject or "订阅套餐",
            out_trade_no=order.order_no,
            total_amount=cents_to_cny(order.price_cents),
            return_url=settings.ALIPAY_RETURN_URL,
            notify_url=settings.ALIPAY_NOTIFY_URL,
        )
        gateway = "https://openapi-sandbox.dl.alipaydev.com/gateway.do" if settings.ALIPAY_ENV == "sandbox" else "https://openapi.alipay.com/gateway.do"
        return {
            "provider": self.code,
            "order_no": order.order_no,
            "pay_url": f"{gateway}?{signed_query}",
            "expires_at": order.payment_expires_at,
        }

    def verify_callback(self, payload, headers=None):
        raw_payload = payload.dict() if hasattr(payload, "dict") else dict(payload)
        data = {str(key): str(value) for key, value in raw_payload.items() if value is not None}
        signature = data.pop("sign", "")
        signature_verified = False
        if signature and data.get("app_id") == settings.ALIPAY_APP_ID:
            try:
                signature_verified = bool(self._client().verify(dict(data), signature))
            except Exception:
                signature_verified = False
        trade_status = data.get("trade_status", "").upper()
        try:
            amount_cents = cny_to_cents(data.get("total_amount", "0"))
        except PaymentRejected:
            amount_cents = 0
        trade_no = data.get("trade_no", "").strip()
        event_type = "PAYMENT_SUCCEEDED" if trade_status in ALIPAY_SUCCESS_STATUSES else f"ALIPAY_{trade_status or 'UNKNOWN'}"
        return PaymentEvent(
            event_id=f"alipay:{trade_no or data.get('out_trade_no', '')}:{trade_status or 'UNKNOWN'}",
            event_type=event_type,
            provider_transaction_id=trade_no,
            amount_cents=amount_cents,
            currency="CNY",
            signature_verified=signature_verified,
            payload=self._safe_payload(data),
            order_no=data.get("out_trade_no", "").strip(),
            app_id=data.get("app_id", "").strip(),
        )

    def query_order(self, order):
        try:
            response = self._client().api_alipay_trade_query(out_trade_no=order.order_no)
        except Exception as exc:
            raise PaymentRejected("支付宝订单查询失败", code="payment_query_failed") from exc
        response = response or {}
        if str(response.get("code") or "") != "10000":
            return PaymentQuery(
                order_no=order.order_no,
                trade_status="",
                provider_transaction_id="",
                amount_cents=0,
                currency=order.currency,
                successful_response=False,
                payload=self._safe_payload(response),
            )
        try:
            amount_cents = cny_to_cents(response.get("total_amount", "0"))
        except PaymentRejected:
            amount_cents = 0
        return PaymentQuery(
            order_no=str(response.get("out_trade_no") or "").strip(),
            trade_status=str(response.get("trade_status") or "").upper(),
            provider_transaction_id=str(response.get("trade_no") or "").strip(),
            amount_cents=amount_cents,
            currency="CNY",
            app_id=settings.ALIPAY_APP_ID,
            successful_response=True,
            payload=self._safe_payload(response),
        )

    def close_order(self, order):
        try:
            response = self._client().api_alipay_trade_close(
                out_trade_no=order.order_no,
                trade_no=order.provider_order_id or None,
            )
        except Exception as exc:
            raise PaymentRejected("支付宝订单关闭失败", code="payment_close_failed") from exc
        response = response or {}
        return {
            "closed": str(response.get("code") or "") == "10000",
            "order_no": str(response.get("out_trade_no") or order.order_no),
            "payload": self._safe_payload(response),
        }

    def refund_order(self, order, amount_cents=None):
        raise PaymentRejected("支付宝线上退款暂未开放", code="refund_not_supported")


PROVIDERS = {
    MockPaymentProvider.code: MockPaymentProvider(),
    AlipayPagePaymentProvider.code: AlipayPagePaymentProvider(),
}


def get_payment_provider(code):
    return PROVIDERS.get(str(code or "").lower())


def get_checkout_provider():
    """Return a user-facing payment provider only when checkout is explicitly enabled."""
    provider = get_payment_provider(getattr(settings, "PAYMENT_PROVIDER", "manual"))
    if provider is None or not provider.is_configured():
        return None
    if provider.code == MockPaymentProvider.code and not settings.BILLING_MOCK_PAYMENTS:
        return None
    return provider

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.utils import timezone


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


class PaymentProvider(ABC):
    code = "base"

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
        )

    def query_order(self, order):
        return {"status": order.status, "order_no": order.order_no}

    def close_order(self, order):
        return {"closed": True, "order_no": order.order_no}

    def refund_order(self, order, amount_cents=None):
        return {
            "refunded": True,
            "order_no": order.order_no,
            "amount_cents": int(amount_cents or order.price_cents),
        }


PROVIDERS = {
    MockPaymentProvider.code: MockPaymentProvider(),
}


def get_payment_provider(code):
    return PROVIDERS.get(str(code or "").lower())


def get_checkout_provider():
    """Return a user-facing payment provider only when checkout is explicitly enabled."""
    provider = get_payment_provider(getattr(settings, "PAYMENT_PROVIDER", "manual"))
    if provider is None:
        return None
    if provider.code == MockPaymentProvider.code and not settings.BILLING_MOCK_PAYMENTS:
        return None
    return provider

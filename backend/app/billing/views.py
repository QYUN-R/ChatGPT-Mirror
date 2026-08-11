from uuid import uuid4

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from app.billing.exceptions import BillingError
from app.billing.models import Order, Plan, PlanOffer, SupportContact, UserNotification
from app.billing.payment import ALIPAY_CLOSED_STATUSES, PaymentEvent, get_checkout_provider, get_payment_provider
from app.billing.selectors import current_subscription, usage_snapshot
from app.billing.serializers import (
    NotificationSerializer,
    OrderSerializer,
    PlanSerializer,
    SupportContactSerializer,
    SubscriptionSerializer,
)
from app.billing.services import (
    active_subscription,
    audit,
    close_order,
    complete_order,
    create_order,
    reconcile_provider_order,
    refresh_subscription_state,
)
from app.utils import get_client_ip
from app.page import DefaultPageNumberPagination


def raise_api_error(error):
    raise ValidationError({"message": error.message, "code": error.code})


class PlanListView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        if not settings.BILLING_ENABLED:
            return Response({"enabled": False, "plans": []})
        checkout_provider = get_checkout_provider()
        queryset = Plan.objects.select_related("pool").prefetch_related("offers").filter(
            is_active=True,
            is_public=True,
            is_archived=False,
        )
        return Response({
            "enabled": True,
            "mock_payments": bool(settings.BILLING_MOCK_PAYMENTS),
            "checkout_available": bool(checkout_provider),
            "plans": PlanSerializer(queryset, many=True).data,
        })


class BillingMeView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        subscription = refresh_subscription_state(request.user) if settings.BILLING_ENABLED else None
        return Response({
            "enabled": bool(settings.BILLING_ENABLED),
            "enforced": bool(settings.BILLING_ENFORCE_SUBSCRIPTION),
            "subscription": SubscriptionSerializer(subscription).data if subscription else None,
            "service_available": bool(subscription and subscription.is_service_active),
            "usage": usage_snapshot(request.user),
            "unread_notifications": UserNotification.objects.filter(
                user=request.user,
                read_at__isnull=True,
            ).count(),
        })


class OrderListCreateView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        queryset = Order.objects.select_related("plan", "offer", "user").filter(user=request.user)
        pg = DefaultPageNumberPagination()
        page = pg.paginate_queryset(queryset, request=request)
        return pg.get_paginated_response(OrderSerializer(page, many=True).data)

    def post(self, request):
        if not settings.BILLING_ENABLED:
            raise ValidationError({"message": "套餐功能尚未启用", "code": "billing_disabled"})
        provider = get_checkout_provider()
        if not provider:
            raise ValidationError({
                "message": "在线支付尚未配置，请联系管理员手动开通套餐",
                "code": "checkout_unavailable",
            })
        offer = get_object_or_404(PlanOffer.objects.select_related("plan", "plan__pool"), pk=request.data.get("offer_id"))
        try:
            order = create_order(
                request.user,
                offer,
                provider=provider.code,
                idempotency_key=str(request.data.get("idempotency_key") or "").strip() or None,
                metadata={"source": "user_portal"},
            )
            checkout = provider.create_order(order)
            if request.data.get("pay_now") and settings.BILLING_MOCK_PAYMENTS:
                event = PaymentEvent(
                    event_id=f"mock-{uuid4().hex}",
                    event_type="PAYMENT_SUCCEEDED",
                    provider_transaction_id=f"mock-tx-{uuid4().hex}",
                    amount_cents=order.price_cents,
                    currency=order.currency,
                    signature_verified=True,
                    payload={"mode": "mock", "source": "user_portal"},
                )
                order, _ = complete_order(order, event)
            return Response({"order": OrderSerializer(order).data, "checkout": checkout})
        except BillingError as exc:
            raise_api_error(exc)


class OrderRenewView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request, order_id):
        source_order = get_object_or_404(Order, pk=order_id, user=request.user)
        provider = get_checkout_provider()
        if not provider:
            raise ValidationError({
                "message": "在线支付尚未配置，请联系管理员手动续费",
                "code": "checkout_unavailable",
            })
        offer_id = request.data.get("offer_id") or source_order.offer_id
        offer = get_object_or_404(PlanOffer.objects.select_related("plan", "plan__pool"), pk=offer_id)
        subscription = active_subscription(request.user)
        if not subscription or offer.plan_id != subscription.plan_id:
            raise ValidationError({"message": "续费套餐必须与当前套餐一致", "code": "renew_plan_mismatch"})
        try:
            order = create_order(
                request.user,
                offer,
                provider=provider.code,
                idempotency_key=str(request.data.get("idempotency_key") or "").strip() or None,
                metadata={"source": "renew", "source_order_id": source_order.id},
            )
            return Response({"order": OrderSerializer(order).data, "checkout": provider.create_order(order)})
        except BillingError as exc:
            raise_api_error(exc)


class OrderPaymentSyncView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request, order_id):
        order = get_object_or_404(Order, pk=order_id, user=request.user)
        try:
            order, subscription = reconcile_provider_order(
                order,
                actor=request.user,
                ip_address=get_client_ip(request),
            )
            return Response({
                "order": OrderSerializer(order).data,
                "subscription": SubscriptionSerializer(subscription).data if subscription else None,
            })
        except BillingError as exc:
            raise_api_error(exc)


class AlipayNotifyView(APIView):
    authentication_classes = ()
    permission_classes = ()

    def _failure(self, status=400):
        return HttpResponse("failure", content_type="text/plain; charset=utf-8", status=status)

    def post(self, request):
        try:
            content_length = int(request.META.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            return self._failure()
        if content_length > settings.PAYMENT_WEBHOOK_MAX_BODY_BYTES:
            return self._failure(status=413)

        provider = get_payment_provider("alipay")
        if provider is None or not provider.is_configured():
            return self._failure(status=503)
        ip_address = get_client_ip(request)
        try:
            event = provider.verify_callback(request.data, headers=request.headers)
        except BillingError as exc:
            audit(
                "payment.callback_rejected",
                detail={"reason": exc.code, "provider": "alipay"},
                ip_address=ip_address,
                target_type="AlipayCallback",
            )
            return self._failure()
        if not event.signature_verified or event.app_id != settings.ALIPAY_APP_ID:
            audit(
                "payment.callback_rejected",
                detail={"reason": "invalid_signature", "provider": "alipay"},
                ip_address=ip_address,
                target_type="AlipayCallback",
                target_id=event.order_no,
            )
            return self._failure()
        order = (
            Order.objects.select_related("user", "plan", "offer")
            .filter(order_no=event.order_no, provider="alipay")
            .first()
        )
        if order is None:
            audit(
                "payment.callback_rejected",
                detail={"reason": "unknown_order", "provider": "alipay"},
                ip_address=ip_address,
                target_type="AlipayCallback",
                target_id=event.order_no,
            )
            return self._failure()
        try:
            if event.event_type == "PAYMENT_SUCCEEDED":
                complete_order(order, event, ip_address=ip_address)
                audit("payment.callback_accepted", order, detail={"provider": "alipay"}, ip_address=ip_address)
            elif str(event.payload.get("trade_status") or "").upper() in ALIPAY_CLOSED_STATUSES:
                close_order(order)
                audit("payment.callback_closed", order, detail={"provider": "alipay"}, ip_address=ip_address)
            else:
                audit(
                    "payment.callback_rejected",
                    order,
                    detail={"reason": "unsupported_trade_status", "provider": "alipay"},
                    ip_address=ip_address,
                )
                return self._failure()
        except BillingError as exc:
            audit(
                "payment.callback_rejected",
                order,
                detail={"reason": exc.code, "provider": "alipay"},
                ip_address=ip_address,
            )
            return self._failure()
        return HttpResponse("success", content_type="text/plain; charset=utf-8")


class OrderMockPayView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request, order_id):
        if not settings.BILLING_MOCK_PAYMENTS:
            raise ValidationError({"message": "模拟支付未启用", "code": "mock_payment_disabled"})
        order = get_object_or_404(Order, pk=order_id, user=request.user)
        event = PaymentEvent(
            event_id=str(request.data.get("event_id") or f"mock-{uuid4().hex}"),
            event_type="PAYMENT_SUCCEEDED",
            provider_transaction_id=f"mock-tx-{uuid4().hex}",
            amount_cents=order.price_cents,
            currency=order.currency,
            signature_verified=True,
            payload={"mode": "mock"},
        )
        try:
            order, subscription = complete_order(order, event)
            return Response({
                "order": OrderSerializer(order).data,
                "subscription": SubscriptionSerializer(subscription).data,
            })
        except BillingError as exc:
            raise_api_error(exc)


class NotificationListView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        queryset = UserNotification.objects.filter(user=request.user)
        unread = request.query_params.get("unread")
        if unread in ("1", "true"):
            queryset = queryset.filter(read_at__isnull=True)
        pg = DefaultPageNumberPagination()
        page = pg.paginate_queryset(queryset, request=request)
        response = pg.get_paginated_response(NotificationSerializer(page, many=True).data)
        response.data["unread_count"] = UserNotification.objects.filter(
            user=request.user,
            read_at__isnull=True,
        ).count()
        return response


class NotificationReadView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request, notification_id):
        notification = get_object_or_404(UserNotification, pk=notification_id, user=request.user)
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at"])
        return Response({"message": "已标记为已读"})


class SupportContactListView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        contacts = SupportContact.objects.filter(is_active=True)
        return Response({
            "contacts": SupportContactSerializer(contacts, many=True).data,
        })

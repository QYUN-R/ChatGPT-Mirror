from uuid import uuid4
import time

from django.shortcuts import get_object_or_404
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from app.accounts.models import User
from app.billing.exceptions import BillingError
from app.billing.models import (
    Announcement,
    AuditLog,
    Order,
    Plan,
    PlanOffer,
    PoolAccountPolicy,
    Subscription,
)
from app.billing.payment import PaymentEvent
from app.billing.serializers import (
    AnnouncementSerializer,
    AuditLogSerializer,
    OrderSerializer,
    PlanSerializer,
    PoolPolicySerializer,
    SubscriptionSerializer,
)
from app.billing.services import (
    close_order,
    complete_order,
    create_order,
    ensure_assignment,
    publish_announcement,
    refund_order,
    suspend_subscription,
)
from app.chatgpt.models import ChatgptAccount, ChatgptCar
from app.page import DefaultPageNumberPagination


def admin_error(error):
    raise ValidationError({"message": error.message, "code": error.code})


class AdminPlanView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request):
        plans = Plan.objects.select_related("pool").prefetch_related("offers").all()
        return Response({
            "plans": PlanSerializer(
                plans,
                many=True,
                context={"admin": True, "include_archived": True},
            ).data,
            "pools": list(ChatgptCar.objects.values("id", "car_name").order_by("car_name")),
        })

    def post(self, request):
        action = request.data.get("action") or "save_plan"
        if action == "save_plan":
            plan = Plan.objects.filter(pk=request.data.get("id")).first() or Plan()
            plan.code = str(request.data.get("code") or "").strip()
            plan.name = str(request.data.get("name") or "").strip()
            plan.tagline = str(request.data.get("tagline") or "").strip()
            plan.pool = get_object_or_404(ChatgptCar, pk=request.data.get("pool_id"))
            plan.pool_tier = request.data.get("pool_tier")
            plan.is_active = bool(request.data.get("is_active", True))
            plan.is_public = bool(request.data.get("is_public", True))
            plan.sort_order = int(request.data.get("sort_order") or 0)
            try:
                plan.save()
            except DjangoValidationError as exc:
                raise ValidationError(exc.message_dict)
            return Response({"plan": PlanSerializer(plan, context={"admin": True}).data})
        if action == "save_offer":
            offer = PlanOffer.objects.filter(pk=request.data.get("id")).first() or PlanOffer()
            offer.plan = get_object_or_404(Plan, pk=request.data.get("plan_id"))
            offer.code = str(request.data.get("code") or "").strip()
            offer.name = str(request.data.get("name") or "").strip()
            offer.months = int(request.data.get("months") or 1)
            offer.price_cents = int(request.data.get("price_cents") or 0)
            offer.currency = str(request.data.get("currency") or "CNY").upper()
            offer.is_draft = bool(request.data.get("is_draft", False))
            offer.is_purchase_enabled = bool(request.data.get("is_purchase_enabled", True))
            try:
                offer.save()
            except DjangoValidationError as exc:
                raise ValidationError(exc.message_dict)
            return Response({"offer": PlanSerializer(offer.plan, context={"admin": True}).data})
        if action in ("archive_plan", "archive_offer"):
            model = Plan if action == "archive_plan" else PlanOffer
            obj = get_object_or_404(model, pk=request.data.get("id"))
            obj.is_archived = True
            if isinstance(obj, Plan):
                obj.is_active = False
            else:
                obj.is_purchase_enabled = False
            obj.save()
            return Response({"message": "已归档"})
        raise ValidationError({"message": "未知操作"})


class AdminPoolView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request):
        policies = PoolAccountPolicy.objects.select_related("pool", "account").all()
        return Response({
            "policies": PoolPolicySerializer(policies, many=True).data,
            "pools": list(ChatgptCar.objects.values("id", "car_name").order_by("car_name")),
            "accounts": list(
                ChatgptAccount.objects.filter(is_archived=False).values(
                    "id", "chatgpt_username", "plan_type", "auth_status"
                ).order_by("chatgpt_username")
            ),
        })

    def post(self, request):
        action = request.data.get("action") or "save_policy"
        if action == "save_policy":
            account = get_object_or_404(ChatgptAccount, pk=request.data.get("account_id"), is_archived=False)
            policy = PoolAccountPolicy.objects.filter(account=account).first() or PoolAccountPolicy(account=account)
            policy.pool = get_object_or_404(ChatgptCar, pk=request.data.get("pool_id"))
            policy.tier = request.data.get("tier")
            policy.binding_limit = int(request.data.get("binding_limit") or 0)
            policy.enabled = bool(request.data.get("enabled", True))
            policy.health_status = request.data.get("health_status") or "HEALTHY"
            try:
                policy.save()
            except Exception as exc:
                raise ValidationError({"message": str(exc)})
            for pool in ChatgptCar.objects.all():
                account_ids = [item for item in (pool.gpt_account_list or []) if item != account.id]
                if pool.id == policy.pool_id and account.id not in account_ids:
                    account_ids.append(account.id)
                if account_ids != (pool.gpt_account_list or []):
                    pool.gpt_account_list = account_ids
                    pool.updated_time = int(time.time())
                    pool.save(update_fields=["gpt_account_list", "updated_time"])
            return Response({"policy": PoolPolicySerializer(policy).data})
        if action == "disable_policy":
            policy = get_object_or_404(PoolAccountPolicy, pk=request.data.get("id"))
            policy.enabled = False
            policy.health_status = "DISABLED"
            policy.save(update_fields=["enabled", "health_status", "updated_at"])
            pool = policy.pool
            pool.gpt_account_list = [item for item in (pool.gpt_account_list or []) if item != policy.account_id]
            pool.updated_time = int(time.time())
            pool.save(update_fields=["gpt_account_list", "updated_time"])
            return Response({"message": "账号策略已停用"})
        raise ValidationError({"message": "未知操作"})


class AdminSubscriptionView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request):
        queryset = Subscription.objects.select_related(
            "user", "plan", "plan__pool", "offer", "scheduled_plan", "scheduled_offer"
        )
        status = request.query_params.get("status")
        if status:
            queryset = queryset.filter(status=status)
        pg = DefaultPageNumberPagination()
        page = pg.paginate_queryset(queryset, request=request)
        return pg.get_paginated_response(
            SubscriptionSerializer(page, many=True, context={"admin": True}).data
        )

    def post(self, request):
        action = request.data.get("action") or "grant"
        try:
            if action in ("grant", "renew"):
                user = get_object_or_404(User, pk=request.data.get("user_id"))
                offer = get_object_or_404(PlanOffer.objects.select_related("plan", "plan__pool"), pk=request.data.get("offer_id"))
                order = create_order(
                    user,
                    offer,
                    provider="manual",
                    actor=request.user,
                    metadata={"source": "admin", "note": str(request.data.get("note") or "")},
                )
                event = PaymentEvent(
                    event_id=f"manual-{uuid4().hex}",
                    event_type="PAYMENT_SUCCEEDED",
                    provider_transaction_id=f"manual-{order.order_no}",
                    amount_cents=order.price_cents,
                    currency=order.currency,
                    signature_verified=True,
                    payload={"mode": "manual", "operator": request.user.username},
                )
                order, subscription = complete_order(order, event, actor=request.user)
                return Response({
                    "order": OrderSerializer(order).data,
                    "subscription": SubscriptionSerializer(subscription, context={"admin": True}).data,
                })
            subscription = get_object_or_404(Subscription, pk=request.data.get("subscription_id"))
            if action == "suspend":
                subscription = suspend_subscription(
                    subscription,
                    actor=request.user,
                    reason=str(request.data.get("reason") or "manual_suspend"),
                )
                return Response({"subscription": SubscriptionSerializer(subscription, context={"admin": True}).data})
            if action == "migrate":
                assignment = ensure_assignment(subscription, reason="admin_requested", force=True)
                return Response({
                    "message": "账号绑定已检查",
                    "account_id": assignment.account_id,
                    "account_name": assignment.account.chatgpt_username,
                })
        except BillingError as exc:
            admin_error(exc)
        raise ValidationError({"message": "未知操作"})


class AdminOrderView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request):
        queryset = Order.objects.select_related("user", "plan", "offer")
        status = request.query_params.get("status")
        if status:
            queryset = queryset.filter(status=status)
        pg = DefaultPageNumberPagination()
        page = pg.paginate_queryset(queryset, request=request)
        return pg.get_paginated_response(OrderSerializer(page, many=True).data)

    def post(self, request):
        order = get_object_or_404(Order, pk=request.data.get("order_id"))
        action = request.data.get("action")
        try:
            if action == "mark_paid":
                event = PaymentEvent(
                    event_id=str(request.data.get("event_id") or f"manual-{uuid4().hex}"),
                    event_type="PAYMENT_SUCCEEDED",
                    provider_transaction_id=str(request.data.get("transaction_id") or f"manual-{order.order_no}"),
                    amount_cents=int(request.data.get("amount_cents") or order.price_cents),
                    currency=str(request.data.get("currency") or order.currency),
                    signature_verified=True,
                    payload={"mode": "admin_manual"},
                )
                order, _ = complete_order(order, event, actor=request.user)
            elif action == "close":
                order = close_order(order, actor=request.user)
            elif action == "refund":
                order = refund_order(order, actor=request.user)
            else:
                raise ValidationError({"message": "未知操作"})
            return Response({"order": OrderSerializer(order).data})
        except BillingError as exc:
            admin_error(exc)


class AdminAnnouncementView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request):
        queryset = Announcement.objects.select_related("plan").all()
        return Response({"announcements": AnnouncementSerializer(queryset, many=True).data})

    def post(self, request):
        action = request.data.get("action") or "save"
        announcement = Announcement.objects.filter(pk=request.data.get("id")).first() or Announcement()
        if action in ("save", "publish"):
            announcement.title = str(request.data.get("title") or "").strip()
            announcement.content = str(request.data.get("content") or "").strip()
            announcement.category = request.data.get("category") or "NOTICE"
            announcement.severity = request.data.get("severity") or "INFO"
            announcement.audience = request.data.get("audience") or "ALL"
            announcement.plan = Plan.objects.filter(pk=request.data.get("plan_id")).first()
            announcement.expires_at = request.data.get("expires_at") or None
            announcement.save()
            if action == "publish":
                publish_announcement(announcement, actor=request.user)
            return Response({"announcement": AnnouncementSerializer(announcement).data})
        if action == "unpublish":
            announcement = get_object_or_404(Announcement, pk=request.data.get("id"))
            announcement.is_published = False
            announcement.save(update_fields=["is_published", "updated_at"])
            return Response({"message": "已撤回公告"})
        raise ValidationError({"message": "未知操作"})


class AdminAuditLogView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request):
        queryset = AuditLog.objects.select_related("actor")
        action = request.query_params.get("action")
        if action:
            queryset = queryset.filter(action__icontains=action)
        pg = DefaultPageNumberPagination()
        page = pg.paginate_queryset(queryset, request=request)
        return pg.get_paginated_response(AuditLogSerializer(page, many=True).data)

from uuid import uuid4
import time
import ipaddress

from django.shortcuts import get_object_or_404
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from app.accounts.models import User
from app.billing.exceptions import BillingError
from app.billing.models import (
    AccountAssignment,
    Announcement,
    AuditLog,
    CommercialSettings,
    Order,
    Plan,
    PlanOffer,
    PlanPool,
    PoolAccountPolicy,
    RedemptionCode,
    RedemptionCodeBatch,
    SupportContact,
    Subscription,
)
from app.billing.payment import PaymentEvent
from app.billing.serializers import (
    AccountAssignmentUsageSerializer,
    AnnouncementSerializer,
    AuditLogSerializer,
    CommercialSettingsSerializer,
    OrderSerializer,
    PlanSerializer,
    PoolPolicySerializer,
    RedemptionCodeBatchSerializer,
    RedemptionCodeSerializer,
    SupportContactSerializer,
    SubscriptionSerializer,
)
from app.billing.services import (
    close_provider_order,
    close_order,
    complete_order,
    create_order,
    ensure_assignment,
    sync_commercial_pool_accounts,
    sync_commercial_account_membership,
    publish_announcement,
    reconcile_provider_order,
    refund_order,
    suspend_subscription,
)
from app.billing.redemption import (
    archive_redeemed_codes,
    create_redemption_batch,
    delete_redeemed_codes,
    lookup_redemption_code,
    restore_archived_codes,
    revoke_redemption_codes,
    set_redemption_batch_state,
)
from app.chatgpt.models import ChatgptAccount, ChatgptCar
from app.page import DefaultPageNumberPagination
from app.utils import get_client_ip, get_redemption_client_ip


def admin_error(error):
    raise ValidationError({"message": error.message, "code": error.code})


class AdminPlanView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request):
        plans = Plan.objects.select_related("pool").prefetch_related("offers", "pool_links__pool").all()
        return Response({
            "plans": PlanSerializer(
                plans,
                many=True,
                context={"admin": True, "include_archived": True},
            ).data,
            "pools": list(
                ChatgptCar.objects.filter(is_commercial=True)
                .values("id", "car_name")
                .order_by("car_name")
            ),
        })

    def post(self, request):
        action = request.data.get("action") or "save_plan"
        if action == "save_plan":
            requested_pool_ids = request.data.get("pool_ids") or (
                [request.data.get("pool_id")] if request.data.get("pool_id") else []
            )
            try:
                requested_pool_ids = list(dict.fromkeys(int(item) for item in requested_pool_ids))
            except (TypeError, ValueError):
                raise ValidationError({"pool_ids": "关联号池格式无效"})
            if not requested_pool_ids:
                raise ValidationError({"pool_ids": "请至少关联一个号池"})
            requested_pools = list(ChatgptCar.objects.filter(pk__in=requested_pool_ids))
            pools_by_id = {pool.id: pool for pool in requested_pools}
            if len(requested_pools) != len(requested_pool_ids):
                raise ValidationError({"pool_ids": "部分关联号池不存在"})
            legacy_bound_pool_ids = {
                pool_id
                for gptcar_list in User.objects.values_list("gptcar_list", flat=True)
                for pool_id in (gptcar_list or [])
            }
            auto_convertible_pools = {
                pool.id for pool in requested_pools
                if not pool.is_commercial
                and not (pool.gpt_account_list or [])
                and pool.id not in legacy_bound_pool_ids
            }
            invalid_pool_ids = {
                pool.id for pool in requested_pools
                if not pool.is_commercial and pool.id not in auto_convertible_pools
            }
            if invalid_pool_ids:
                raise ValidationError({"pool_ids": "套餐只能关联在套餐号池页面维护的商业号池"})
            requested_pool = pools_by_id[requested_pool_ids[0]]
            requested_tier = request.data.get("pool_tier")
            try:
                user_limit = int(request.data.get("user_limit") or 0)
                daily_quota = int(request.data.get("daily_quota") or 0)
                monthly_quota = int(request.data.get("monthly_quota") or 0)
                device_limit = int(request.data.get("device_limit") or 1)
                sort_order = int(request.data.get("sort_order") or 0)
            except (TypeError, ValueError):
                raise ValidationError({"message": "套餐限额和排序必须是整数"})
            if min(user_limit, daily_quota, monthly_quota, sort_order) < 0:
                raise ValidationError({"message": "套餐限额和排序不能小于 0"})
            if not 1 <= device_limit <= 50:
                raise ValidationError({"device_limit": "设备上限必须在 1 到 50 之间"})
            with transaction.atomic():
                if auto_convertible_pools:
                    ChatgptCar.objects.filter(id__in=auto_convertible_pools).update(is_commercial=True)
                plan = Plan.objects.select_for_update().filter(pk=request.data.get("id")).first() or Plan()
                quotas_changed = (
                    not plan.pk
                    or plan.daily_quota != daily_quota
                    or plan.monthly_quota != monthly_quota
                )
                device_policy_changed = (
                    not plan.pk
                    or plan.multi_device_enabled != bool(request.data.get("multi_device_enabled", True))
                    or plan.device_limit != device_limit
                )
                if plan.pk and plan.redemption_batches.exists() and plan.pool_tier != requested_tier:
                    raise ValidationError({"pool_tier": "已有卡密批次的套餐不能更换等级"})
                plan.code = str(request.data.get("code") or "").strip()
                plan.name = str(request.data.get("name") or "").strip()
                if "tagline" in request.data or not plan.pk:
                    plan.tagline = str(request.data.get("tagline") or "").strip()
                plan.pool = requested_pool
                plan.pool_tier = requested_tier
                plan.user_limit = user_limit
                plan.daily_quota = daily_quota
                plan.monthly_quota = monthly_quota
                plan.multi_device_enabled = bool(request.data.get("multi_device_enabled", True))
                plan.device_limit = device_limit
                plan.is_active = bool(request.data.get("is_active", True))
                plan.is_public = bool(request.data.get("is_public", True))
                plan.sort_order = sort_order
                try:
                    if plan.pk:
                        PlanPool.objects.filter(plan=plan).exclude(pool_id__in=requested_pool_ids).delete()
                    plan.save()
                    for priority, pool_id in enumerate(requested_pool_ids):
                        link, _ = PlanPool.objects.get_or_create(
                            plan=plan,
                            pool=pools_by_id[pool_id],
                            defaults={"priority": priority, "is_active": True},
                        )
                        link.priority = priority
                        link.is_active = True
                        link.save(update_fields=["priority", "is_active", "updated_at"])
                    if quotas_changed:
                        User.objects.filter(
                            billing_subscription__plan=plan,
                            billing_subscription__status="ACTIVE",
                            billing_subscription__ends_at__gt=timezone.now(),
                        ).update(
                            daily_quota=plan.daily_quota,
                            monthly_quota=plan.monthly_quota,
                        )
                except DjangoValidationError as exc:
                    raise ValidationError(exc.message_dict)
                plan = Plan.objects.select_related("pool").prefetch_related("offers", "pool_links__pool").get(pk=plan.pk)
                affected_user_ids = list(
                    User.objects.filter(
                        billing_subscription__plan=plan,
                        billing_subscription__status="ACTIVE",
                        billing_subscription__ends_at__gt=timezone.now(),
                        device_policy_managed_by_plan=True,
                    ).values_list("id", flat=True)
                ) if device_policy_changed else []
            if affected_user_ids:
                from app.accounts.device_policy import enforce_device_policy_with_gateway

                for affected_user in User.objects.filter(pk__in=affected_user_ids):
                    enforce_device_policy_with_gateway(affected_user)
            return Response({"plan": PlanSerializer(plan, context={"admin": True}).data})
        if action == "save_offer":
            offer = PlanOffer.objects.filter(pk=request.data.get("id")).first() or PlanOffer()
            requested_plan = get_object_or_404(Plan, pk=request.data.get("plan_id"))
            if offer.pk and offer.plan_id != requested_plan.id:
                raise ValidationError({"plan_id": "已创建的价格方案不能更换所属套餐"})
            offer.plan = requested_plan
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
                if hasattr(exc, "message_dict"):
                    raise ValidationError(exc.message_dict)
                raise ValidationError({"message": list(exc.messages)})
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
        policies = PoolAccountPolicy.objects.select_related("pool", "account").annotate(
            active_bindings_count=Count(
                "account__billing_assignments",
                filter=Q(account__billing_assignments__active=True),
                distinct=True,
            )
        )
        policy_rows = PoolPolicySerializer(policies, many=True).data
        policies_by_pool = {}
        for row in policy_rows:
            policies_by_pool.setdefault(row["pool_id"], []).append(row)
        commercial_pools = []
        for pool in ChatgptCar.objects.filter(is_commercial=True).order_by("car_name"):
            rows = policies_by_pool.get(pool.id, [])
            plan_tiers = set(
                Plan.objects.filter(is_archived=False)
                .filter(Q(pool=pool) | Q(pool_links__pool=pool, pool_links__is_active=True))
                .values_list("pool_tier", flat=True)
                .distinct()
            )
            policy_tiers = {row["tier"] for row in rows}
            tier = next(iter(plan_tiers or policy_tiers), "STANDARD")
            commercial_pools.append({
                "id": pool.id,
                "car_name": pool.car_name,
                "remark": pool.remark,
                "tier": tier,
                "account_ids": [row["account_id"] for row in rows],
                "account_count": len(rows),
                "healthy_account_count": sum(
                    1 for row in rows if row["enabled"] and row["health_status"] == "HEALTHY"
                ),
                "active_bindings": sum(row["active_bindings"] for row in rows),
                "total_capacity": sum(row["binding_limit"] for row in rows if row["enabled"]),
                "policies": rows,
            })
        return Response({
            "policies": policy_rows,
            "commercial_pools": commercial_pools,
            "pools": list(ChatgptCar.objects.values("id", "car_name").order_by("car_name")),
            "accounts": list(
                ChatgptAccount.objects.filter(is_archived=False).values(
                    "id", "chatgpt_username", "plan_type", "auth_status"
                ).order_by("chatgpt_username")
            ),
        })

    def post(self, request):
        action = request.data.get("action") or "save_policy"
        if action == "create_pool":
            account_ids = request.data.get("account_ids") or []
            if not account_ids:
                raise ValidationError({"message": "新建套餐号池时请至少选择一个上游账号"})
            pool_name = str(request.data.get("pool_name") or "").strip()
            if not pool_name:
                raise ValidationError({"message": "号池名称不能为空"})
            if ChatgptCar.objects.filter(car_name=pool_name).exists():
                raise ValidationError({"message": "号池名称已存在"})
            try:
                with transaction.atomic():
                    pool = ChatgptCar.objects.create(
                        car_name=pool_name,
                        remark=str(request.data.get("remark") or "").strip()[:128],
                        gpt_account_list=[],
                        is_commercial=True,
                        created_time=int(time.time()),
                        updated_time=int(time.time()),
                    )
                    sync_commercial_pool_accounts(
                        pool,
                        tier=request.data.get("tier"),
                        account_ids=account_ids,
                        default_binding_limit=request.data.get("default_binding_limit", 5),
                        apply_binding_limit=True,
                        actor=request.user,
                        ip_address=get_client_ip(request),
                    )
            except BillingError as exc:
                admin_error(exc)
            except IntegrityError:
                raise ValidationError({"message": "号池名称已存在"})
            except DjangoValidationError as exc:
                if hasattr(exc, "message_dict"):
                    raise ValidationError(exc.message_dict)
                raise ValidationError({"message": list(exc.messages)})
            return Response({"message": "套餐号池已创建", "pool_id": pool.id})
        if action == "sync_pool_accounts":
            pool = get_object_or_404(ChatgptCar, pk=request.data.get("pool_id"))
            try:
                sync_commercial_pool_accounts(
                    pool,
                    tier=request.data.get("tier"),
                    account_ids=request.data.get("account_ids") or [],
                    pool_name=request.data.get("pool_name"),
                    remark=request.data.get("remark"),
                    default_binding_limit=request.data.get("default_binding_limit", 5),
                    apply_binding_limit=bool(request.data.get("apply_binding_limit", False)),
                    actor=request.user,
                    ip_address=get_client_ip(request),
                )
            except BillingError as exc:
                admin_error(exc)
            return Response({"message": "商业号池账号已保存"})
        if action == "save_policy":
            account = get_object_or_404(ChatgptAccount, pk=request.data.get("account_id"), is_archived=False)
            policy = PoolAccountPolicy.objects.filter(account=account).first() or PoolAccountPolicy(account=account)
            previous_pool_id = policy.pool_id
            previous_tier = policy.tier
            policy.pool = get_object_or_404(ChatgptCar, pk=request.data.get("pool_id"))
            policy.tier = request.data.get("tier")
            policy.binding_limit = int(request.data.get("binding_limit") or 0)
            policy.enabled = bool(request.data.get("enabled", True))
            policy.health_status = request.data.get("health_status") or "HEALTHY"
            active_bindings = AccountAssignment.objects.filter(account=account, active=True).count()
            if active_bindings and (
                (previous_pool_id and previous_pool_id != policy.pool_id)
                or (previous_tier and previous_tier != policy.tier)
            ):
                raise ValidationError({
                    "message": "该账号仍有用户正在使用，不能直接跨号池或跨等级移动，请先迁移用户",
                    "code": "account_in_use",
                })
            if policy.binding_limit < active_bindings:
                raise ValidationError({
                    "message": f"该账号当前已有 {active_bindings} 人使用，绑定上限不能低于当前人数",
                    "code": "binding_limit_below_usage",
                })
            try:
                policy.save()
            except Exception as exc:
                raise ValidationError({"message": str(exc)})
            sync_commercial_account_membership(account, policy.pool if policy.enabled else None)
            return Response({"policy": PoolPolicySerializer(policy).data})
        if action == "disable_policy":
            policy = get_object_or_404(PoolAccountPolicy, pk=request.data.get("id"))
            policy.enabled = False
            policy.health_status = "DISABLED"
            policy.save(update_fields=["enabled", "health_status", "updated_at"])
            sync_commercial_account_membership(policy.account, None)
            return Response({"message": "账号策略已停用"})
        if action == "delete_policy":
            policy = get_object_or_404(
                PoolAccountPolicy.objects.select_related("account", "pool"),
                pk=request.data.get("id"),
            )
            if AccountAssignment.objects.filter(account=policy.account, active=True).exists():
                raise ValidationError({"message": "该策略仍有用户正在使用，请先迁移用户后再删除"})
            account = policy.account
            policy_id = policy.id
            policy.delete()
            sync_commercial_account_membership(account, None)
            audit(
                "pool_policy.deleted",
                actor=request.user,
                target_type="PoolAccountPolicy",
                target_id=policy_id,
                detail={"account_id": account.id},
                ip_address=get_client_ip(request),
            )
            return Response({"message": "账号策略已删除"})
        raise ValidationError({"message": "未知操作"})


class AdminPoolUsageView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request, policy_id):
        policy = get_object_or_404(
            PoolAccountPolicy.objects.select_related("pool", "account"),
            pk=policy_id,
        )
        assignments = AccountAssignment.objects.filter(
            account=policy.account,
            active=True,
        ).select_related("user", "subscription", "subscription__plan").order_by(
            "-last_used_at", "-assigned_at", "-id"
        )
        return Response({
            "policy": PoolPolicySerializer(policy).data,
            "users": AccountAssignmentUsageSerializer(assignments, many=True).data,
        })


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
        queryset = Order.objects.select_related("user", "plan", "offer").prefetch_related("transactions")
        status = request.query_params.get("status")
        provider = request.query_params.get("provider")
        keyword = str(request.query_params.get("q") or "").strip()
        if status:
            queryset = queryset.filter(status=status)
        if provider:
            queryset = queryset.filter(provider=provider)
        if keyword:
            queryset = queryset.filter(Q(user__username__icontains=keyword) | Q(order_no__icontains=keyword))
        pg = DefaultPageNumberPagination()
        page = pg.paginate_queryset(queryset, request=request)
        return pg.get_paginated_response(OrderSerializer(page, many=True, context={"admin": True}).data)

    def post(self, request):
        order = get_object_or_404(Order, pk=request.data.get("order_id"))
        action = request.data.get("action")
        try:
            if action == "mark_paid":
                if order.provider in ("alipay", "redemption_code"):
                    raise BillingError("该订单不能由管理员手动确认收款", code="manual_payment_not_allowed")
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
                order = (
                    close_provider_order(order, actor=request.user, ip_address=get_client_ip(request))
                    if order.provider == "alipay"
                    else close_order(order, actor=request.user)
                )
            elif action == "sync":
                order, _ = reconcile_provider_order(order, actor=request.user, ip_address=get_client_ip(request))
            elif action == "refund":
                if order.provider == "redemption_code":
                    raise BillingError("卡密订单不支持退款操作", code="redemption_refund_not_supported")
                order = refund_order(order, actor=request.user)
            else:
                raise ValidationError({"message": "未知操作"})
            return Response({"order": OrderSerializer(order, context={"admin": True}).data})
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
            acknowledgement = request.data.get("requires_acknowledgement", False)
            announcement.requires_acknowledgement = acknowledgement is True or str(acknowledgement).lower() in (
                "1",
                "true",
            )
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


class AdminSupportContactView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request):
        contacts = SupportContact.objects.all()
        return Response({"contacts": SupportContactSerializer(contacts, many=True).data})

    def post(self, request):
        action = request.data.get("action") or "save"
        if action == "save":
            contact = SupportContact.objects.filter(pk=request.data.get("id")).first() or SupportContact()
            contact.name = str(request.data.get("name") or "")
            contact.channel = str(request.data.get("channel") or "")
            contact.contact = str(request.data.get("contact") or "")
            contact.description = str(request.data.get("description") or "")
            contact.qr_image = str(request.data.get("qr_image") or "")
            contact.is_active = bool(request.data.get("is_active", True))
            try:
                contact.sort_order = int(request.data.get("sort_order") or 0)
                contact.save()
            except (TypeError, ValueError):
                raise ValidationError({"sort_order": "排序必须是非负整数"})
            except DjangoValidationError as exc:
                raise ValidationError(exc.message_dict)
            AuditLog.objects.create(
                actor=request.user,
                action="support_contact_saved",
                target_type="SupportContact",
                target_id=str(contact.id),
                detail={"name": contact.name, "channel": contact.channel, "is_active": contact.is_active},
            )
            return Response({"contact": SupportContactSerializer(contact).data})
        if action == "delete":
            contact = get_object_or_404(SupportContact, pk=request.data.get("id"))
            AuditLog.objects.create(
                actor=request.user,
                action="support_contact_deleted",
                target_type="SupportContact",
                target_id=str(contact.id),
                detail={"name": contact.name, "channel": contact.channel},
            )
            contact.delete()
            return Response({"message": "售后联系方式已删除"})
        raise ValidationError({"message": "未知操作"})


class AdminRedemptionSettingsView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request):
        settings_obj, _ = CommercialSettings.objects.get_or_create(pk=1)
        return Response({"settings": CommercialSettingsSerializer(settings_obj).data})

    def post(self, request):
        settings_obj, _ = CommercialSettings.objects.get_or_create(pk=1)
        enabled_value = request.data.get("redemption_enabled", False)
        settings_obj.redemption_enabled = enabled_value is True or str(enabled_value).lower() in ("1", "true")
        settings_obj.purchase_url = str(request.data.get("purchase_url") or "").strip()
        settings_obj.updated_by = request.user
        try:
            settings_obj.save()
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict)
        AuditLog.objects.create(
            actor=request.user,
            action="redemption.settings_updated",
            target_type="CommercialSettings",
            target_id="1",
            detail={
                "redemption_enabled": settings_obj.redemption_enabled,
                "purchase_url": settings_obj.purchase_url,
            },
            ip_address=get_redemption_client_ip(request) or None,
        )
        return Response({"settings": CommercialSettingsSerializer(settings_obj).data})


class AdminRedemptionBatchView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request):
        queryset = RedemptionCodeBatch.objects.select_related("plan", "offer", "offer__plan", "created_by").annotate(
            available_count=Count("codes", filter=Q(codes__status="AVAILABLE", codes__is_archived=False)),
            redeemed_count=Count("codes", filter=Q(codes__status="REDEEMED", codes__is_archived=False)),
            revoked_count=Count("codes", filter=Q(codes__status="REVOKED", codes__is_archived=False)),
            archived_count=Count("codes", filter=Q(codes__is_archived=True)),
        )
        if request.query_params.get("include_archived") not in ("1", "true"):
            queryset = queryset.filter(is_archived=False)
        return Response({"batches": RedemptionCodeBatchSerializer(queryset, many=True).data})

    def post(self, request):
        action = str(request.data.get("action") or "generate")
        ip_address = get_redemption_client_ip(request) or None
        try:
            if action == "generate":
                offer = get_object_or_404(
                    PlanOffer.objects.select_related("plan", "plan__pool"),
                    pk=request.data.get("offer_id"),
                )
                expires_at = request.data.get("expires_at") or None
                if expires_at:
                    from django.utils.dateparse import parse_datetime

                    expires_at = parse_datetime(str(expires_at))
                    if expires_at is None:
                        raise BillingError("卡密失效时间格式无效", code="invalid_batch_expiry")
                    if timezone.is_naive(expires_at):
                        expires_at = timezone.make_aware(expires_at)
                batch, codes = create_redemption_batch(
                    offer=offer,
                    quantity=request.data.get("quantity"),
                    expires_at=expires_at,
                    note=request.data.get("note"),
                    actor=request.user,
                    ip_address=ip_address,
                )
                return Response({
                    "batch": RedemptionCodeBatchSerializer(batch).data,
                    "plaintext_codes": codes,
                })
            batch = get_object_or_404(RedemptionCodeBatch, pk=request.data.get("batch_id"))
            if action == "enable":
                batch = set_redemption_batch_state(
                    batch=batch, actor=request.user, is_active=True, ip_address=ip_address
                )
            elif action == "disable":
                batch = set_redemption_batch_state(
                    batch=batch, actor=request.user, is_active=False, ip_address=ip_address
                )
            elif action == "archive":
                batch = set_redemption_batch_state(
                    batch=batch, actor=request.user, archive=True, ip_address=ip_address
                )
            else:
                raise ValidationError({"message": "未知操作"})
            return Response({"batch": RedemptionCodeBatchSerializer(batch).data})
        except BillingError as exc:
            admin_error(exc)


class AdminRedemptionCodeView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request):
        queryset = RedemptionCode.objects.select_related(
            "batch",
            "batch__plan",
            "batch__offer",
            "batch__offer__plan",
            "redeemed_by",
            "order",
            "subscription",
        )
        batch_id = request.query_params.get("batch_id")
        status = request.query_params.get("status")
        keyword = str(request.query_params.get("q") or "").strip()
        ip_address = str(request.query_params.get("ip") or "").strip()
        redeemed_from = request.query_params.get("redeemed_from")
        redeemed_to = request.query_params.get("redeemed_to")
        if request.query_params.get("include_archived") not in ("1", "true"):
            queryset = queryset.filter(is_archived=False, batch__is_archived=False)
        if batch_id:
            queryset = queryset.filter(batch_id=batch_id)
        if status:
            queryset = queryset.filter(status=status)
        if keyword:
            queryset = queryset.filter(
                Q(serial_no__icontains=keyword)
                | Q(redeemed_by__username__icontains=keyword)
                | Q(redeemed_email__icontains=keyword)
                | Q(order__order_no__icontains=keyword)
            )
        if ip_address:
            try:
                ip_address = str(ipaddress.ip_address(ip_address))
            except ValueError:
                raise ValidationError({"ip": "兑换 IP 格式无效"})
            queryset = queryset.filter(redeemed_ip=ip_address)
        if redeemed_from:
            from django.utils.dateparse import parse_datetime

            parsed_from = parse_datetime(str(redeemed_from))
            if parsed_from is None:
                raise ValidationError({"redeemed_from": "开始时间格式无效"})
            if timezone.is_naive(parsed_from):
                parsed_from = timezone.make_aware(parsed_from)
            queryset = queryset.filter(redeemed_at__gte=parsed_from)
        if redeemed_to:
            from django.utils.dateparse import parse_datetime

            parsed_to = parse_datetime(str(redeemed_to))
            if parsed_to is None:
                raise ValidationError({"redeemed_to": "结束时间格式无效"})
            if timezone.is_naive(parsed_to):
                parsed_to = timezone.make_aware(parsed_to)
            queryset = queryset.filter(redeemed_at__lte=parsed_to)
        pg = DefaultPageNumberPagination()
        page = pg.paginate_queryset(queryset, request=request)
        response = pg.get_paginated_response(RedemptionCodeSerializer(page, many=True).data)
        response.data["summary"] = {
            "available": RedemptionCode.objects.filter(
                status="AVAILABLE", is_archived=False, batch__is_archived=False
            ).count(),
            "redeemed": RedemptionCode.objects.filter(
                status="REDEEMED", is_archived=False, batch__is_archived=False
            ).count(),
            "revoked": RedemptionCode.objects.filter(
                status="REVOKED", is_archived=False, batch__is_archived=False
            ).count(),
            "archived": RedemptionCode.objects.filter(
                Q(is_archived=True) | Q(batch__is_archived=True)
            ).count(),
        }
        return response

    def post(self, request):
        action = str(request.data.get("action") or "")
        code_ids = request.data.get("code_ids") or []
        ip_address = get_redemption_client_ip(request) or None
        try:
            if action == "lookup":
                plaintext_code = str(request.data.get("code") or "")
                if len(plaintext_code) > 128:
                    raise BillingError("卡密不存在", code="redemption_code_not_found")
                code = lookup_redemption_code(plaintext_code)
                AuditLog.objects.create(
                    actor=request.user,
                    action="redemption.code_looked_up",
                    target_type="RedemptionCode",
                    target_id=str(code.id),
                    detail={"serial_no": code.serial_no},
                    ip_address=ip_address,
                )
                return Response({"code": RedemptionCodeSerializer(code).data})
            if action == "revoke":
                count = revoke_redemption_codes(
                    code_ids=code_ids, actor=request.user, ip_address=ip_address
                )
            elif action == "archive_redeemed":
                count = archive_redeemed_codes(
                    code_ids=code_ids, actor=request.user, ip_address=ip_address
                )
            elif action == "delete_redeemed":
                count = delete_redeemed_codes(
                    code_ids=code_ids, actor=request.user, ip_address=ip_address
                )
            elif action == "restore":
                count = restore_archived_codes(
                    code_ids=code_ids, actor=request.user, ip_address=ip_address
                )
            else:
                raise ValidationError({"message": "未知操作"})
            return Response({"message": "操作完成", "count": count})
        except BillingError as exc:
            admin_error(exc)


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

import calendar
import secrets
import time
from contextlib import contextmanager
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
import redis

from app.billing.exceptions import BillingError, CapacityUnavailable, PaymentRejected, SubscriptionInactive
from app.billing.models import (
    AccountAssignment,
    AccountAssignmentEvent,
    Announcement,
    AnnouncementAudience,
    AssignmentEventType,
    AuditLog,
    Order,
    OrderStatus,
    OrderType,
    PaymentTransaction,
    Plan,
    PlanOffer,
    PoolAccountPolicy,
    PoolReservation,
    PoolTier,
    ReservationStatus,
    Subscription,
    SubscriptionStatus,
    UsageEvent,
    UsageResult,
    UserNotification,
    supports_commercial_pool_account,
)
from app.billing.payment import (
    ALIPAY_CLOSED_STATUSES,
    ALIPAY_SUCCESS_STATUSES,
    PaymentEvent,
    get_payment_provider,
)
from app.billing.selectors import CAPACITY_STATUSES, capacity_snapshot, plan_pool_ids, pool_capacity_snapshots
from app.chatgpt.models import ChatgptAccount, ChatgptCar


def billing_enabled():
    return bool(getattr(settings, "BILLING_ENABLED", False))


def billing_enforced():
    return billing_enabled() and bool(getattr(settings, "BILLING_ENFORCE_SUBSCRIPTION", False))


def add_months(value, months):
    month_index = value.month - 1 + int(months)
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def audit(action, target=None, *, actor=None, detail=None, ip_address=None, target_type=None, target_id=None):
    if target is not None:
        target_type = target_type or target.__class__.__name__
        target_id = target_id if target_id is not None else str(getattr(target, "pk", "") or "")
    return AuditLog.objects.create(
        actor=actor,
        action=action,
        target_type=target_type or "Unknown",
        target_id=str(target_id or ""),
        detail=detail or {},
        ip_address=ip_address,
    )


def _sanitize_payment_payload(value):
    blocked_fragments = ("secret", "token", "cookie", "password", "signature", "private_key")
    blocked_keys = {"sign", "sign_type", "app_auth_token", "auth_token"}
    if isinstance(value, dict):
        return {
            str(key): "[redacted]"
            if str(key).lower() in blocked_keys or any(fragment in str(key).lower() for fragment in blocked_fragments)
            else _sanitize_payment_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_payment_payload(item) for item in value[:100]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@contextmanager
def payment_callback_lock(order_no):
    if not getattr(settings, "PAYMENT_CALLBACK_LOCK_ENABLED", False):
        yield
        return
    lock = None
    acquired = False
    try:
        client = redis.Redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=2, socket_timeout=2)
        lock = client.lock(f"billing:payment:{order_no}", timeout=60, blocking_timeout=5)
        acquired = bool(lock.acquire(blocking=True))
    except redis.RedisError:
        # PostgreSQL select_for_update 和支付事件/流水唯一约束仍提供最终幂等保护。
        yield
        return
    if not acquired:
        raise PaymentRejected("支付回调正在处理中，请稍后重试")
    try:
        yield
    finally:
        try:
            if lock:
                lock.release()
        except redis.RedisError:
            pass


def has_managed_subscription(user):
    if not billing_enabled() or not getattr(user, "is_authenticated", False):
        return False
    return Subscription.objects.filter(user=user).exists()


def _sync_legacy_expiry(subscription):
    user = subscription.user
    user.expired_date = subscription.ends_at.date()
    user.save(update_fields=["expired_date"])


def _sync_plan_quotas(subscription):
    user = subscription.user
    daily_quota = max(int(subscription.plan.daily_quota or 0), 0)
    monthly_quota = max(int(subscription.plan.monthly_quota or 0), 0)
    if user.daily_quota == daily_quota and user.monthly_quota == monthly_quota:
        return
    user.daily_quota = daily_quota
    user.monthly_quota = monthly_quota
    user.save(update_fields=["daily_quota", "monthly_quota"])


def _release_assignment(subscription, reason):
    assignment = AccountAssignment.objects.select_for_update().filter(
        subscription=subscription,
        active=True,
    ).first()
    if not assignment:
        return
    now = timezone.now()
    assignment.active = False
    assignment.released_at = now
    assignment.save(update_fields=["active", "released_at", "updated_at"])
    AccountAssignmentEvent.objects.create(
        assignment=assignment,
        subscription=subscription,
        user=subscription.user,
        old_account=assignment.account,
        event_type=AssignmentEventType.RELEASED,
        reason=reason,
    )


def _release_reservations(subscription, *, except_plan_id=None, reason="released"):
    queryset = PoolReservation.objects.select_for_update().filter(
        subscription=subscription,
        status__in=CAPACITY_STATUSES,
    )
    if except_plan_id:
        queryset = queryset.exclude(plan_id=except_plan_id)
    now = timezone.now()
    queryset.update(
        status=ReservationStatus.RELEASED,
        released_at=now,
        expires_at=None,
        updated_at=now,
    )


@transaction.atomic
def refresh_subscription_state(user):
    subscription = (
        Subscription.objects.select_for_update()
        .select_related("plan", "user")
        .filter(user=user)
        .first()
    )
    if not subscription or subscription.status != SubscriptionStatus.ACTIVE:
        return subscription
    now = timezone.now()
    if subscription.ends_at > now:
        return subscription

    if subscription.scheduled_plan_id and subscription.scheduled_months:
        previous_plan = subscription.plan
        next_plan = subscription.scheduled_plan
        next_offer = subscription.scheduled_offer
        transition_at = subscription.ends_at
        _release_assignment(subscription, "scheduled_downgrade")
        _release_reservations(subscription, except_plan_id=next_plan.id)
        subscription.plan = next_plan
        subscription.offer = next_offer
        subscription.starts_at = transition_at
        subscription.ends_at = add_months(transition_at, subscription.scheduled_months)
        subscription.scheduled_plan = None
        subscription.scheduled_offer = None
        subscription.scheduled_months = 0
        if subscription.ends_at <= now:
            subscription.status = SubscriptionStatus.EXPIRED
            _release_reservations(subscription)
        subscription.save()
        _sync_legacy_expiry(subscription)
        _sync_plan_quotas(subscription)
        audit(
            "subscription.downgrade_applied",
            subscription,
            detail={"from_plan": previous_plan.code, "to_plan": next_plan.code},
        )
        return subscription

    subscription.status = SubscriptionStatus.EXPIRED
    subscription.save(update_fields=["status", "updated_at"])
    _release_assignment(subscription, "subscription_expired")
    _release_reservations(subscription)
    UserNotification.objects.get_or_create(
        user=user,
        dedupe_key=f"subscription-expired:{subscription.id}:{subscription.ends_at.date().isoformat()}",
        defaults={
            "kind": "EXPIRY",
            "title": "套餐已到期",
            "content": "你的套餐已经到期，续费后即可继续使用服务。",
        },
    )
    audit("subscription.expired", subscription)
    return subscription


def active_subscription(user):
    subscription = refresh_subscription_state(user)
    if subscription and subscription.is_service_active:
        return subscription
    return None


def _reserve_capacity(*, user, plan, order, subscription=None):
    now = timezone.now()
    plan = Plan.objects.select_for_update().get(pk=plan.pk)
    PoolReservation.objects.select_for_update().filter(
        status=ReservationStatus.HELD,
        expires_at__lte=now,
    ).update(
        status=ReservationStatus.EXPIRED,
        released_at=now,
        updated_at=now,
    )
    snapshot = capacity_snapshot(plan, lock=True)
    if snapshot["available"] < 1:
        raise CapacityUnavailable(f"{plan.name}号池已满，请联系管理员增加上游账号")
    available_pools = [item for item in snapshot["pools"] if item["available"] > 0]
    if not available_pools:
        raise CapacityUnavailable(f"{plan.name}号池已满，请联系管理员增加上游账号")
    selected_pool = min(
        available_pools,
        key=lambda item: (
            item["used"] / item["total"] if item["total"] else 1,
            item["used"],
            item["pool_id"],
        ),
    )
    return PoolReservation.objects.create(
        user=user,
        plan=plan,
        pool_id=selected_pool["pool_id"],
        order=order,
        subscription=subscription,
        status=ReservationStatus.HELD,
        expires_at=now + timedelta(minutes=settings.BILLING_ORDER_HOLD_MINUTES),
    )


def _determine_order_type(subscription, target_plan):
    if not subscription or not subscription.is_service_active:
        return OrderType.PURCHASE
    if subscription.plan_id == target_plan.id:
        return OrderType.RENEW
    if subscription.plan.pool_tier == PoolTier.STANDARD and target_plan.pool_tier == PoolTier.PREMIUM:
        return OrderType.UPGRADE
    if subscription.plan.pool_tier == PoolTier.PREMIUM and target_plan.pool_tier == PoolTier.STANDARD:
        return OrderType.DOWNGRADE
    raise BillingError("当前套餐不能直接切换到目标套餐", code="invalid_plan_transition")


@transaction.atomic
def create_order(
    user,
    offer,
    *,
    provider="mock",
    idempotency_key=None,
    actor=None,
    metadata=None,
    allow_unavailable=False,
    plan_snapshot=None,
    offer_snapshot=None,
    entitlement_months=None,
    price_cents=None,
    currency=None,
    target_plan=None,
):
    provider = str(provider or "manual").strip().lower()
    if not isinstance(offer, PlanOffer):
        offer = PlanOffer.objects.select_related("plan", "plan__pool").get(pk=offer)
    else:
        offer = PlanOffer.objects.select_related("plan", "plan__pool").get(pk=offer.pk)
    target_plan = target_plan or offer.plan
    if target_plan.pk != offer.plan_id:
        target_plan = target_plan.__class__.objects.select_related("pool").get(pk=target_plan.pk)
    if not allow_unavailable and (offer.is_archived or offer.is_draft or not offer.is_purchase_enabled):
        raise BillingError("该套餐当前不可购买", code="offer_unavailable")
    if not allow_unavailable and (target_plan.is_archived or not target_plan.is_active):
        raise BillingError("该套餐已下架", code="plan_unavailable")
    entitlement_months = int(offer.months if entitlement_months is None else entitlement_months)
    price_cents = offer.price_cents if price_cents is None else int(price_cents)
    currency = str(currency or offer.currency).upper()
    if entitlement_months < 1 or price_cents < 0 or not currency:
        raise BillingError("订单套餐快照无效", code="invalid_order_snapshot")
    if idempotency_key:
        existing = Order.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            if existing.user_id != user.id:
                raise BillingError("幂等键已被其他订单使用", code="idempotency_conflict")
            return existing
    if provider == "alipay":
        existing = (
            Order.objects.filter(
                user=user,
                offer=offer,
                provider="alipay",
                status=OrderStatus.PENDING,
                payment_expires_at__gt=timezone.now(),
            )
            .order_by("-created_at")
            .first()
        )
        if existing:
            return existing

    subscription = refresh_subscription_state(user)
    order_type = _determine_order_type(subscription, target_plan)
    if order_type == OrderType.DOWNGRADE and subscription.scheduled_plan_id:
        raise BillingError("已经存在待生效的降级套餐", code="downgrade_already_scheduled")

    order = Order.objects.create(
        order_no=f"B{timezone.now():%Y%m%d%H%M%S}{secrets.token_hex(4).upper()}",
        user=user,
        plan=target_plan,
        offer=offer,
        order_type=order_type,
        provider=provider,
        idempotency_key=idempotency_key,
        plan_snapshot=plan_snapshot or {
            "code": target_plan.code,
            "name": target_plan.name,
            "tagline": target_plan.tagline,
            "pool_tier": target_plan.pool_tier,
        },
        offer_snapshot=offer_snapshot or {
            "code": offer.code,
            "name": offer.name,
            "months": offer.months,
        },
        entitlement_months=entitlement_months,
        price_cents=price_cents,
        currency=currency,
        payment_expires_at=(
            timezone.now() + timedelta(minutes=max(int(settings.BILLING_ORDER_HOLD_MINUTES), 1))
            if provider == "alipay"
            else None
        ),
        metadata=metadata or {},
    )
    if order_type != OrderType.RENEW:
        _reserve_capacity(user=user, plan=target_plan, order=order, subscription=subscription)
    audit(
        "order.created",
        order,
        actor=actor,
        detail={"order_type": order_type, "price_cents": order.price_cents},
    )
    return order


def _activate_order_reservation(order, subscription):
    if order.order_type == OrderType.RENEW:
        return None
    reservation = PoolReservation.objects.select_for_update().filter(order=order).first()
    now = timezone.now()
    if reservation and reservation.status == ReservationStatus.HELD and reservation.expires_at > now:
        reservation.subscription = subscription
        reservation.status = ReservationStatus.ACTIVE
        reservation.expires_at = None
        reservation.save(update_fields=["subscription", "status", "expires_at", "updated_at"])
        return reservation
    if reservation and reservation.status in (ReservationStatus.ACTIVE, ReservationStatus.ASSIGNED):
        reservation.subscription = subscription
        reservation.save(update_fields=["subscription", "updated_at"])
        return reservation
    if reservation:
        reservation.status = ReservationStatus.EXPIRED
        reservation.released_at = now
        reservation.save(update_fields=["status", "released_at", "updated_at"])
        reservation.order = None
        reservation.save(update_fields=["order", "updated_at"])
    return _reserve_capacity(
        user=order.user,
        plan=order.plan,
        order=order,
        subscription=subscription,
    )


def _apply_paid_order(order):
    now = timezone.now()
    subscription = (
        Subscription.objects.select_for_update()
        .select_related("plan", "user")
        .filter(user=order.user)
        .first()
    )
    if order.order_type == OrderType.PURCHASE:
        if subscription:
            _release_assignment(subscription, "new_purchase")
            _release_reservations(subscription)
            subscription.plan = order.plan
            subscription.offer = order.offer
            subscription.status = SubscriptionStatus.ACTIVE
            subscription.source = order.provider
            subscription.starts_at = now
            subscription.ends_at = add_months(now, order.entitlement_months)
            subscription.scheduled_plan = None
            subscription.scheduled_offer = None
            subscription.scheduled_months = 0
            subscription.save()
        else:
            subscription = Subscription.objects.create(
                user=order.user,
                plan=order.plan,
                offer=order.offer,
                status=SubscriptionStatus.ACTIVE,
                source=order.provider,
                starts_at=now,
                ends_at=add_months(now, order.entitlement_months),
            )
        _activate_order_reservation(order, subscription)
    elif order.order_type == OrderType.RENEW:
        if not subscription:
            raise PaymentRejected("续费订单缺少原订阅")
        base = max(subscription.ends_at, now)
        subscription.ends_at = add_months(base, order.entitlement_months)
        subscription.offer = order.offer
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.save(update_fields=["ends_at", "offer", "status", "updated_at"])
    elif order.order_type == OrderType.UPGRADE:
        if not subscription or not subscription.is_service_active:
            raise PaymentRejected("升级订单缺少有效的原套餐")
        previous_plan = subscription.plan
        _release_assignment(subscription, "plan_upgrade")
        _release_reservations(subscription)
        subscription.plan = order.plan
        subscription.offer = order.offer
        subscription.ends_at = add_months(subscription.ends_at, order.entitlement_months)
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.source = order.provider
        subscription.save()
        _activate_order_reservation(order, subscription)
        audit(
            "subscription.upgraded",
            subscription,
            detail={"from_plan": previous_plan.code, "to_plan": order.plan.code},
        )
    elif order.order_type == OrderType.DOWNGRADE:
        if not subscription or not subscription.is_service_active:
            raise PaymentRejected("降级订单缺少有效的原套餐")
        subscription.scheduled_plan = order.plan
        subscription.scheduled_offer = order.offer
        subscription.scheduled_months = order.entitlement_months
        subscription.save(update_fields=[
            "scheduled_plan",
            "scheduled_offer",
            "scheduled_months",
            "updated_at",
        ])
        _activate_order_reservation(order, subscription)
    else:
        raise PaymentRejected("不支持的订单类型")

    _sync_legacy_expiry(subscription)
    if order.order_type != OrderType.DOWNGRADE:
        _sync_plan_quotas(subscription)
    return subscription


def _order_entitlement_ends_at(order, subscription):
    if order.order_type == OrderType.DOWNGRADE:
        return add_months(subscription.ends_at, order.entitlement_months)
    return subscription.ends_at


def complete_order(order, event: PaymentEvent, *, actor=None, ip_address=None):
    rejection = None
    subscription = None
    provider_transaction_id = str(event.provider_transaction_id or "").strip()
    callback_lock_key = f"{order.provider}:{provider_transaction_id or order.order_no}"
    with payment_callback_lock(callback_lock_key):
        with transaction.atomic():
            order = Order.objects.select_for_update().select_related("user", "plan", "offer").get(pk=order.pk)
            existing_event = PaymentTransaction.objects.filter(event_id=event.event_id).first()
            if existing_event:
                if existing_event.order_id != order.id:
                    rejection = PaymentRejected("支付流水号已被其他订单使用")
                elif not existing_event.accepted:
                    rejection = PaymentRejected("该支付回调此前已被拒绝")
                else:
                    subscription = Subscription.objects.filter(user=order.user).first()
            else:
                now = timezone.now()
                occurred_at = event.occurred_at
                timestamp_valid = not timezone.is_naive(occurred_at)
                if timestamp_valid:
                    max_age = max(int(settings.PAYMENT_CALLBACK_MAX_AGE_SECONDS), 0)
                    max_future_skew = max(int(settings.PAYMENT_CALLBACK_MAX_FUTURE_SKEW_SECONDS), 0)
                    timestamp_valid = (
                        occurred_at >= now - timedelta(seconds=max_age)
                        and occurred_at <= now + timedelta(seconds=max_future_skew)
                    )
                recorded_occurred_at = occurred_at if not timezone.is_naive(occurred_at) else now
                duplicate_transaction = None
                if provider_transaction_id:
                    duplicate_transaction = PaymentTransaction.objects.filter(
                        provider=order.provider,
                        provider_transaction_id=provider_transaction_id,
                    ).first()
                stored_transaction_id = "" if duplicate_transaction else provider_transaction_id
                event_type_valid = event.event_type == "PAYMENT_SUCCEEDED"
                accepted = (
                    event.signature_verified
                    and event_type_valid
                    and timestamp_valid
                    and bool(provider_transaction_id)
                    and duplicate_transaction is None
                    and event.amount_cents == order.price_cents
                    and event.currency.upper() == order.currency.upper()
                )
                PaymentTransaction.objects.create(
                    order=order,
                    provider=order.provider,
                    provider_transaction_id=stored_transaction_id,
                    event_id=event.event_id,
                    event_type=event.event_type,
                    amount_cents=event.amount_cents,
                    currency=event.currency,
                    signature_verified=event.signature_verified,
                    accepted=accepted,
                    payload=_sanitize_payment_payload(event.payload),
                    occurred_at=recorded_occurred_at,
                )
                if not event.signature_verified:
                    audit("payment.rejected_signature", order, actor=actor)
                    rejection = PaymentRejected("支付回调验签失败")
                elif not event_type_valid:
                    audit("payment.rejected_event_type", order, actor=actor, detail={"event_type": event.event_type})
                    rejection = PaymentRejected("支付回调事件类型不允许开通套餐")
                elif not timestamp_valid:
                    audit("payment.rejected_timestamp", order, actor=actor)
                    rejection = PaymentRejected("支付回调已过期或时间异常")
                elif not provider_transaction_id:
                    audit("payment.rejected_transaction_id", order, actor=actor)
                    rejection = PaymentRejected("支付渠道流水号不能为空")
                elif duplicate_transaction:
                    audit(
                        "payment.rejected_duplicate_transaction",
                        order,
                        actor=actor,
                        detail={"existing_order_id": duplicate_transaction.order_id},
                    )
                    rejection = PaymentRejected("支付渠道流水号已被使用")
                elif event.amount_cents != order.price_cents or event.currency.upper() != order.currency.upper():
                    audit(
                        "payment.rejected_amount",
                        order,
                        actor=actor,
                        detail={"received_amount_cents": event.amount_cents, "currency": event.currency},
                    )
                    rejection = PaymentRejected("支付金额或币种不匹配")
                elif order.status == OrderStatus.PAID:
                    subscription = Subscription.objects.filter(user=order.user).first()
                elif order.status != OrderStatus.PENDING:
                    rejection = PaymentRejected("订单当前状态不能支付")
                else:
                    subscription = _apply_paid_order(order)
                    order.status = OrderStatus.PAID
                    order.paid_at = timezone.now()
                    order.entitlement_ends_at = _order_entitlement_ends_at(order, subscription)
                    order.provider_order_id = event.provider_transaction_id
                    order.save(update_fields=[
                        "status",
                        "paid_at",
                        "entitlement_ends_at",
                        "provider_order_id",
                        "updated_at",
                    ])
                    UserNotification.objects.create(
                        user=order.user,
                        kind="PAYMENT",
                        title="套餐已开通",
                        content=f"{order.plan.name}已开通，有效期至 {timezone.localtime(subscription.ends_at):%Y-%m-%d %H:%M}。",
                    )
                    audit("order.paid", order, actor=actor, detail={"event_id": event.event_id}, ip_address=ip_address)
    if rejection:
        raise rejection
    return order, subscription


def reconcile_provider_order(order, *, actor=None, ip_address=None):
    """Reconcile one provider order without trusting any browser redirect."""
    order = Order.objects.select_related("user", "plan", "offer").get(pk=order.pk)
    provider = get_payment_provider(order.provider)
    if provider is None or order.provider != "alipay":
        raise BillingError("该订单不支持渠道查单", code="payment_sync_not_supported")

    result = provider.query_order(order)
    detail = {
        "provider": order.provider,
        "trade_status": result.trade_status,
        "provider_transaction_id": result.provider_transaction_id,
        "successful_response": result.successful_response,
    }
    if not result.successful_response:
        if result.payload.get("sub_code") != "ACQ.TRADE_NOT_EXIST":
            audit("payment.reconcile_no_result", order, actor=actor, detail=detail, ip_address=ip_address)
        return order, None
    if result.order_no != order.order_no:
        audit("payment.reconcile_order_mismatch", order, actor=actor, detail=detail, ip_address=ip_address)
        raise PaymentRejected("支付宝订单号不匹配", code="payment_order_mismatch")
    if result.trade_status in ALIPAY_SUCCESS_STATUSES:
        event = PaymentEvent(
            event_id=f"alipay:{result.provider_transaction_id}:{result.trade_status}",
            event_type="PAYMENT_SUCCEEDED",
            provider_transaction_id=result.provider_transaction_id,
            amount_cents=result.amount_cents,
            currency=result.currency,
            signature_verified=True,
            occurred_at=timezone.now(),
            payload=result.payload,
            order_no=result.order_no,
            app_id=result.app_id,
        )
        order, subscription = complete_order(order, event, actor=actor, ip_address=ip_address)
        audit("payment.reconciled_paid", order, actor=actor, detail=detail, ip_address=ip_address)
        return order, subscription
    if result.trade_status in ALIPAY_CLOSED_STATUSES:
        order = close_order(order, actor=actor)
        audit("payment.reconciled_closed", order, actor=actor, detail=detail, ip_address=ip_address)
        return order, None
    audit("payment.reconciled_pending", order, actor=actor, detail=detail, ip_address=ip_address)
    return order, None


def close_provider_order(order, *, actor=None, ip_address=None):
    """Close an unpaid Alipay order only after checking that it was not paid."""
    order = Order.objects.select_related("user", "plan", "offer").get(pk=order.pk)
    if order.provider != "alipay":
        return close_order(order, actor=actor)
    if order.status != OrderStatus.PENDING:
        return order

    reconciled_order, _ = reconcile_provider_order(order, actor=actor, ip_address=ip_address)
    if reconciled_order.status != OrderStatus.PENDING:
        return reconciled_order

    provider = get_payment_provider("alipay")
    result = provider.close_order(reconciled_order)
    if not result.get("closed"):
        if result.get("payload", {}).get("sub_code") == "ACQ.TRADE_NOT_EXIST":
            return close_order(reconciled_order, actor=actor)
        audit(
            "payment.close_failed",
            reconciled_order,
            actor=actor,
            detail={"provider": "alipay", "result": _sanitize_payment_payload(result.get("payload", {}))},
            ip_address=ip_address,
        )
        reconciled_order, _ = reconcile_provider_order(reconciled_order, actor=actor, ip_address=ip_address)
        if reconciled_order.status != OrderStatus.PENDING:
            return reconciled_order
        raise PaymentRejected("支付宝订单暂时无法关闭", code="payment_close_failed")
    closed = close_order(reconciled_order, actor=actor)
    audit("payment.closed", closed, actor=actor, detail={"provider": "alipay"}, ip_address=ip_address)
    return closed


@transaction.atomic
def close_order(order, *, actor=None):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status == OrderStatus.PAID:
        raise BillingError("已支付订单不能关闭", code="paid_order")
    if order.status in (OrderStatus.CLOSED, OrderStatus.REFUNDED):
        return order
    now = timezone.now()
    order.status = OrderStatus.CLOSED
    order.closed_at = now
    order.save(update_fields=["status", "closed_at", "updated_at"])
    PoolReservation.objects.filter(order=order, status=ReservationStatus.HELD).update(
        status=ReservationStatus.RELEASED,
        released_at=now,
        updated_at=now,
    )
    audit("order.closed", order, actor=actor)
    return order


@transaction.atomic
def refund_order(order, *, actor=None, event_id=None):
    order = Order.objects.select_for_update().select_related("user").get(pk=order.pk)
    if order.provider == "alipay":
        raise BillingError("支付宝线上退款暂未开放，请走人工售后流程", code="refund_not_supported")
    if order.status == OrderStatus.REFUNDED:
        return order
    if order.status != OrderStatus.PAID:
        raise BillingError("只有已支付订单可以退款", code="order_not_paid")
    order.status = OrderStatus.REFUNDED
    order.save(update_fields=["status", "updated_at"])
    subscription = Subscription.objects.select_for_update().filter(user=order.user).first()
    if subscription:
        subscription.status = SubscriptionStatus.REFUNDED
        subscription.save(update_fields=["status", "updated_at"])
        _release_assignment(subscription, "order_refunded")
        _release_reservations(subscription)
    PaymentTransaction.objects.get_or_create(
        event_id=event_id or f"manual-refund-{order.order_no}",
        defaults={
            "order": order,
            "provider": order.provider,
            "provider_transaction_id": f"refund-{order.provider_order_id or order.order_no}",
            "event_type": "REFUND_SUCCEEDED",
            "amount_cents": order.price_cents,
            "currency": order.currency,
            "signature_verified": True,
            "accepted": True,
            "payload": {"mode": "manual"},
        },
    )
    UserNotification.objects.create(
        user=order.user,
        kind="REFUND",
        title="套餐已退款",
        content="相关套餐权益已暂停，如有疑问请联系售后。",
    )
    audit("order.refunded", order, actor=actor)
    return order


def _policy_is_usable(policy):
    account = policy.account
    return (
        policy.enabled
        and policy.health_status == "HEALTHY"
        and not account.is_archived
        and account.auth_status
        and (account.access_token_valid or account.session_token_valid)
        and supports_commercial_pool_account(account)
    )


@transaction.atomic
def sync_commercial_account_membership(account, pool=None):
    """Keep the legacy pool mirror aligned with the commercial policy source of truth."""
    selected_pool_id = pool.id if pool and not account.is_archived else None
    for candidate in ChatgptCar.objects.select_for_update().all():
        account_ids = [item for item in (candidate.gpt_account_list or []) if item != account.id]
        if candidate.id == selected_pool_id:
            account_ids.append(account.id)
        if account_ids != (candidate.gpt_account_list or []):
            candidate.gpt_account_list = account_ids
            candidate.updated_time = int(time.time())
            candidate.save(update_fields=["gpt_account_list", "updated_time"])


@transaction.atomic
def sync_commercial_pool_accounts(
    pool,
    *,
    tier,
    account_ids,
    pool_name=None,
    remark=None,
    default_binding_limit=5,
    apply_binding_limit=False,
    actor=None,
    ip_address=None,
):
    """Synchronize one commercial pool as an atomic, pool-centric operation."""
    if tier not in PoolTier.values:
        raise BillingError("套餐号池等级无效", code="invalid_pool_tier")

    try:
        default_binding_limit = int(default_binding_limit)
    except (TypeError, ValueError) as exc:
        raise BillingError("单账号绑定上限格式无效", code="invalid_binding_limit") from exc
    if default_binding_limit < 1:
        raise BillingError("单账号绑定上限必须至少为 1", code="invalid_binding_limit")

    try:
        selected_ids = list(dict.fromkeys(int(item) for item in (account_ids or [])))
    except (TypeError, ValueError) as exc:
        raise BillingError("上游账号列表格式无效", code="invalid_account_list") from exc

    pool = ChatgptCar.objects.select_for_update().get(pk=pool.pk)
    was_commercial = pool.is_commercial
    pool.is_commercial = True
    if pool_name is not None:
        pool_name = str(pool_name).strip()
        if not pool_name:
            raise BillingError("号池名称不能为空", code="invalid_pool_name")
        if len(pool_name) > 32:
            raise BillingError("号池名称不能超过 32 个字符", code="invalid_pool_name")
        if ChatgptCar.objects.exclude(pk=pool.pk).filter(car_name=pool_name).exists():
            raise BillingError("号池名称已存在", code="duplicate_pool_name")
        pool.car_name = pool_name
    if remark is not None:
        pool.remark = str(remark).strip()[:128]
    if pool_name is not None or remark is not None or not was_commercial:
        pool.updated_time = int(time.time())
        pool.save(update_fields=["car_name", "remark", "is_commercial", "updated_time"])
    linked_plan_tiers = set(
        Plan.objects.filter(is_archived=False)
        .filter(Q(pool=pool) | Q(pool_links__pool=pool, pool_links__is_active=True))
        .values_list("pool_tier", flat=True)
        .distinct()
    )
    if linked_plan_tiers and linked_plan_tiers != {tier}:
        raise BillingError(
            "号池已关联其他等级的套餐，请先在套餐配置中调整",
            code="pool_tier_conflict",
        )

    accounts = list(
        ChatgptAccount.objects.select_for_update()
        .filter(pk__in=selected_ids, is_archived=False)
        .order_by("id")
    )
    if len(accounts) != len(selected_ids):
        raise BillingError("部分上游账号不存在或已删除", code="account_not_found")
    invalid_accounts = [account.chatgpt_username for account in accounts if not supports_commercial_pool_account(account)]
    if invalid_accounts:
        raise BillingError(
            "套餐号池只允许加入 Plus、Pro、Team 或 Business 账号",
            code="unsupported_commercial_account",
        )

    current_policies = list(
        PoolAccountPolicy.objects.select_for_update()
        .select_related("account", "pool")
        .filter(Q(pool=pool) | Q(account_id__in=selected_ids))
        .order_by("account_id")
    )
    policy_by_account_id = {policy.account_id: policy for policy in current_policies}
    current_pool_policies = [policy for policy in current_policies if policy.pool_id == pool.id]
    affected_account_ids = {policy.account_id for policy in current_pool_policies} | set(selected_ids)
    list(
        AccountAssignment.objects.select_for_update()
        .filter(account_id__in=affected_account_ids, active=True)
        .values_list("id", flat=True)
    )
    binding_counts = {
        row["account_id"]: row["total"]
        for row in AccountAssignment.objects.filter(account_id__in=affected_account_ids, active=True)
        .values("account_id")
        .annotate(total=Count("id"))
    }

    selected_id_set = set(selected_ids)
    for policy in current_pool_policies:
        if policy.account_id not in selected_id_set and binding_counts.get(policy.account_id, 0):
            raise BillingError(
                f"账号 {policy.account.chatgpt_username} 仍有用户正在使用，请先迁移后再移出号池",
                code="account_in_use",
            )

    for account in accounts:
        policy = policy_by_account_id.get(account.id)
        active_bindings = binding_counts.get(account.id, 0)
        if policy and policy.pool_id != pool.id:
            raise BillingError(
                f"账号 {account.chatgpt_username} 已属于号池 {policy.pool.car_name}，请先从原号池移出",
                code="account_already_in_pool",
            )
        if policy and policy.tier != tier and active_bindings:
            raise BillingError(
                f"账号 {account.chatgpt_username} 仍有用户正在使用，不能直接跨等级移动",
                code="account_in_use",
            )
        next_limit = default_binding_limit if apply_binding_limit or not policy else policy.binding_limit
        if next_limit < active_bindings:
            raise BillingError(
                f"账号 {account.chatgpt_username} 当前已有 {active_bindings} 人使用，绑定上限不能低于当前人数",
                code="binding_limit_below_usage",
            )

    removed_policy_ids = []
    for policy in current_pool_policies:
        if policy.account_id not in selected_id_set:
            removed_policy_ids.append(policy.id)
            policy.delete()

    saved_policy_ids = []
    for account in accounts:
        policy = policy_by_account_id.get(account.id) or PoolAccountPolicy(account=account)
        policy.pool = pool
        policy.tier = tier
        if apply_binding_limit or not policy.pk:
            policy.binding_limit = default_binding_limit
        policy.save()
        saved_policy_ids.append(policy.id)

    for candidate in ChatgptCar.objects.select_for_update().all():
        next_account_ids = [
            item for item in (candidate.gpt_account_list or [])
            if item not in affected_account_ids and item not in selected_id_set
        ]
        if candidate.id == pool.id:
            next_account_ids.extend(selected_ids)
        if next_account_ids != (candidate.gpt_account_list or []):
            candidate.gpt_account_list = next_account_ids
            candidate.updated_time = int(time.time())
            candidate.save(update_fields=["gpt_account_list", "updated_time"])

    audit(
        "commercial_pool.synced",
        pool,
        actor=actor,
        ip_address=ip_address,
        detail={
            "tier": tier,
            "pool_name": pool.car_name,
            "account_ids": selected_ids,
            "saved_policy_ids": saved_policy_ids,
            "removed_policy_ids": removed_policy_ids,
            "default_binding_limit": default_binding_limit,
            "apply_binding_limit": bool(apply_binding_limit),
        },
    )
    return pool


@transaction.atomic
def archive_upstream_account(account, *, actor=None):
    account = ChatgptAccount.objects.select_for_update().get(pk=account.pk)
    active_bindings = AccountAssignment.objects.filter(account=account, active=True).count()
    if active_bindings:
        raise BillingError(
            "该账号仍有用户正在使用，请先迁移用户后再删除",
            code="account_in_use",
        )

    policy_ids = list(PoolAccountPolicy.objects.filter(account=account).values_list("id", flat=True))
    PoolAccountPolicy.objects.filter(account=account).delete()
    sync_commercial_account_membership(account, None)

    account.access_token = ""
    account.session_token = None
    account.extra_cookies = []
    account.refresh_token = None
    account.refresh_client_id = None
    account.access_token_valid = False
    account.session_token_valid = False
    account.auth_status = False
    account.proxy_node_id = None
    account.last_error = ""
    account.is_archived = True
    account.archived_at = timezone.now()
    account.updated_time = int(time.time())
    account.save(update_fields=[
        "access_token",
        "session_token",
        "extra_cookies",
        "refresh_token",
        "refresh_client_id",
        "access_token_valid",
        "session_token_valid",
        "auth_status",
        "proxy_node_id",
        "last_error",
        "is_archived",
        "archived_at",
        "updated_time",
    ])
    audit(
        "chatgpt_account.deleted",
        account,
        actor=actor,
        detail={"removed_policy_ids": policy_ids, "credentials_cleared": True},
    )
    return account


def _web_probe_invalidates_credentials(error_code):
    return str(error_code or "").strip().lower() in {
        "token_invalidated",
        "invalid_authentication",
        "authentication_token_invalid",
    }


@transaction.atomic
def ensure_assignment(subscription, *, reason="first_use", force=False, preferred_policy_id=None):
    subscription = Subscription.objects.select_for_update().select_related(
        "user",
        "plan",
        "plan__pool",
    ).prefetch_related("plan__pool_links__pool").get(pk=subscription.pk)
    if not subscription.is_service_active:
        raise SubscriptionInactive("套餐已到期或暂停，请续费后再使用")

    assignment = AccountAssignment.objects.select_for_update().select_related("account").filter(
        subscription=subscription,
    ).first()
    linked_pool_ids = plan_pool_ids(subscription.plan)
    if assignment and assignment.active and not force and preferred_policy_id is None:
        policy = PoolAccountPolicy.objects.select_related("account").filter(
            account=assignment.account,
            pool_id__in=linked_pool_ids,
            tier=subscription.plan.pool_tier,
        ).first()
        if policy and _policy_is_usable(policy):
            return assignment

    policies = list(
        PoolAccountPolicy.objects.select_for_update()
        .select_related("account")
        .filter(
            pool_id__in=linked_pool_ids,
            tier=subscription.plan.pool_tier,
            enabled=True,
            account__is_archived=False,
            account__auth_status=True,
        )
        .filter(health_status="HEALTHY")
        .order_by("account_id")
    )
    policies = [policy for policy in policies if _policy_is_usable(policy)]
    account_ids = [policy.account_id for policy in policies]
    binding_counts = {
        row["account_id"]: row["total"]
        for row in AccountAssignment.objects.filter(account_id__in=account_ids, active=True)
        .values("account_id")
        .annotate(total=Count("id"))
    }
    recent_since = timezone.now() - timedelta(hours=3)
    recent_counts = {
        row["account_id"]: row["total"]
        for row in UsageEvent.objects.filter(account_id__in=account_ids, request_at__gte=recent_since)
        .values("account_id")
        .annotate(total=Count("id"))
    }
    reservation = PoolReservation.objects.select_for_update().filter(
        subscription=subscription,
        plan=subscription.plan,
        status__in=(ReservationStatus.ACTIVE, ReservationStatus.ASSIGNED),
    ).order_by("-created_at").first()
    reserved_pool_id = reservation.pool_id if reservation else None
    if preferred_policy_id is not None:
        selected = next((policy for policy in policies if policy.id == preferred_policy_id), None)
        if selected is None:
            raise BillingError("所选账号不属于当前套餐号池或当前不可用", code="invalid_account_choice")
        if (
            assignment
            and assignment.active
            and assignment.account_id == selected.account_id
            and assignment.pool_id == selected.pool_id
        ):
            return assignment
        if binding_counts.get(selected.account_id, 0) >= selected.binding_limit:
            raise CapacityUnavailable("所选账号当前人数已满，请选择其他账号")
    else:
        candidates = [
            policy
            for policy in policies
            if binding_counts.get(policy.account_id, 0) < policy.binding_limit
            and not (force and assignment and policy.account_id == assignment.account_id)
        ]
        if not candidates:
            raise CapacityUnavailable("当前套餐号池没有可分配的健康上游账号")
        pool_priorities = {pool_id: index for index, pool_id in enumerate(linked_pool_ids)}
        selected = min(
            candidates,
            key=lambda policy: (
                0 if reserved_pool_id and policy.pool_id == reserved_pool_id else 1,
                binding_counts.get(policy.account_id, 0) / policy.binding_limit,
                recent_counts.get(policy.account_id, 0),
                pool_priorities.get(policy.pool_id, len(pool_priorities)),
                policy.account_id,
            ),
        )
    old_account = assignment.account if assignment else None
    now = timezone.now()
    if assignment:
        assignment.pool = selected.pool
        assignment.account = selected.account
        assignment.active = True
        assignment.assigned_at = now
        assignment.released_at = None
        assignment.save()
        event_type = AssignmentEventType.MIGRATED
    else:
        assignment = AccountAssignment.objects.create(
            subscription=subscription,
            user=subscription.user,
            pool=selected.pool,
            account=selected.account,
            active=True,
            assigned_at=now,
        )
        event_type = AssignmentEventType.ASSIGNED
    AccountAssignmentEvent.objects.create(
        assignment=assignment,
        subscription=subscription,
        user=subscription.user,
        old_account=old_account,
        new_account=selected.account,
        event_type=event_type,
        reason=reason,
    )
    reservation_queryset = PoolReservation.objects.filter(
        subscription=subscription,
        plan=subscription.plan,
        status__in=(ReservationStatus.ACTIVE, ReservationStatus.ASSIGNED),
    )
    matching_reservation = reservation_queryset.filter(pool=selected.pool).first()
    if matching_reservation:
        reservation_queryset.exclude(pk=matching_reservation.pk).update(
            status=ReservationStatus.RELEASED,
            released_at=now,
            updated_at=now,
        )
        matching_reservation.status = ReservationStatus.ASSIGNED
        matching_reservation.released_at = None
        matching_reservation.save(update_fields=["status", "released_at", "updated_at"])
    else:
        reservation_queryset.update(
            status=ReservationStatus.RELEASED,
            released_at=now,
            updated_at=now,
        )
        PoolReservation.objects.create(
            user=subscription.user,
            plan=subscription.plan,
            pool=selected.pool,
            subscription=subscription,
            status=ReservationStatus.ASSIGNED,
        )
    audit(
        "assignment.created" if not old_account else "assignment.migrated",
        assignment,
        detail={"reason": reason, "pool_id": selected.pool_id},
    )
    return assignment


def managed_account_options(user):
    if not billing_enabled() or user.is_staff or user.is_superuser:
        return None, []
    subscription = refresh_subscription_state(user)
    if not subscription:
        if billing_enforced():
            raise SubscriptionInactive("当前账号尚未开通套餐")
        return None, []
    if not subscription.is_service_active:
        raise SubscriptionInactive("套餐已到期或暂停，请先续费")

    assignment = ensure_assignment(subscription)
    linked_pool_ids = plan_pool_ids(subscription.plan)
    policies = list(
        PoolAccountPolicy.objects.select_related("account", "pool")
        .filter(
            pool_id__in=linked_pool_ids,
            tier=subscription.plan.pool_tier,
            enabled=True,
            health_status="HEALTHY",
            account__is_archived=False,
            account__auth_status=True,
        )
        .order_by("pool_id", "account_id")
    )
    policies = [policy for policy in policies if _policy_is_usable(policy)]
    binding_counts = {
        row["account_id"]: row["total"]
        for row in AccountAssignment.objects.filter(account_id__in=[item.account_id for item in policies], active=True)
        .values("account_id")
        .annotate(total=Count("id"))
    }
    pool_priorities = {pool_id: index for index, pool_id in enumerate(linked_pool_ids)}
    options = [
        policy
        for policy in policies
        if policy.account_id == assignment.account_id
        or binding_counts.get(policy.account_id, 0) < policy.binding_limit
    ]
    options.sort(key=lambda policy: (
        0 if policy.account_id == assignment.account_id else 1,
        pool_priorities.get(policy.pool_id, len(pool_priorities)),
        policy.account_id,
    ))
    return assignment, options


def resolve_managed_account(user, *, preferred_policy_id=None):
    if not billing_enabled() or user.is_staff or user.is_superuser:
        return None
    subscription = refresh_subscription_state(user)
    if subscription:
        if not subscription.is_service_active:
            raise SubscriptionInactive("套餐已到期或暂停，请先续费")
        reason = "user_selected" if preferred_policy_id is not None else "first_use"
        return ensure_assignment(
            subscription,
            reason=reason,
            preferred_policy_id=preferred_policy_id,
        ).account
    if billing_enforced():
        raise SubscriptionInactive("当前账号尚未开通套餐")
    return None


def record_usage(user, account, *, result=UsageResult.SUCCESS, duration_ms=0, model_name="", error_code=""):
    subscription = Subscription.objects.filter(user=user).first() if billing_enabled() else None
    event = UsageEvent.objects.create(
        user=user,
        subscription=subscription,
        plan=subscription.plan if subscription else None,
        account=account,
        model_name=model_name,
        result=result,
        duration_ms=max(int(duration_ms or 0), 0),
        error_code=error_code,
    )
    if subscription:
        AccountAssignment.objects.filter(subscription=subscription, active=True).update(
            last_used_at=event.request_at,
            updated_at=event.request_at,
        )
    return event


@transaction.atomic
def suspend_subscription(subscription, *, actor=None, reason="manual_suspend"):
    subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
    subscription.status = SubscriptionStatus.SUSPENDED
    subscription.save(update_fields=["status", "updated_at"])
    _release_assignment(subscription, reason)
    _release_reservations(subscription)
    audit("subscription.suspended", subscription, actor=actor, detail={"reason": reason})
    return subscription


def publish_announcement(announcement, *, actor=None):
    now = timezone.now()
    announcement.is_published = True
    announcement.published_at = announcement.published_at or now
    announcement.save(update_fields=["is_published", "published_at", "updated_at"])
    from app.accounts.models import User

    users = User.objects.filter(is_active=True)
    if announcement.audience == AnnouncementAudience.ACTIVE_SUBSCRIBERS:
        users = users.filter(
            billing_subscription__status=SubscriptionStatus.ACTIVE,
            billing_subscription__ends_at__gt=now,
        )
    elif announcement.audience == AnnouncementAudience.PLAN and announcement.plan_id:
        users = users.filter(billing_subscription__plan=announcement.plan)
    notifications = [
        UserNotification(
            user=user,
            announcement=announcement,
            kind="ANNOUNCEMENT",
            title=announcement.title,
            content=announcement.content,
            dedupe_key=f"announcement:{announcement.id}",
        )
        for user in users.iterator()
    ]
    UserNotification.objects.bulk_create(notifications, ignore_conflicts=True)
    audit("announcement.published", announcement, actor=actor, detail={"recipients": len(notifications)})
    return announcement


def expire_stale_reservations():
    now = timezone.now()
    queryset = PoolReservation.objects.filter(
        status=ReservationStatus.HELD,
        expires_at__lte=now,
    )
    count = queryset.count()
    queryset.update(
        status=ReservationStatus.EXPIRED,
        released_at=now,
        updated_at=now,
    )
    return count


def reconcile_pending_alipay_orders():
    now = timezone.now()
    processed = 0
    queryset = (
        Order.objects.filter(
            provider="alipay",
            status=OrderStatus.PENDING,
            payment_expires_at__isnull=False,
            payment_expires_at__gt=now,
        )
        .order_by("created_at")[: max(int(settings.ALIPAY_RECONCILE_BATCH_SIZE), 1)]
    )
    for order in queryset:
        try:
            reconcile_provider_order(order)
            processed += 1
        except BillingError as exc:
            audit("payment.reconcile_failed", order, detail={"code": exc.code})
    return processed


def expire_pending_alipay_orders():
    now = timezone.now()
    processed = 0
    queryset = (
        Order.objects.filter(
            provider="alipay",
            status=OrderStatus.PENDING,
            payment_expires_at__isnull=False,
            payment_expires_at__lte=now,
        )
        .order_by("payment_expires_at")[: max(int(settings.ALIPAY_RECONCILE_BATCH_SIZE), 1)]
    )
    for order in queryset:
        try:
            close_provider_order(order)
            processed += 1
        except BillingError as exc:
            audit("payment.expire_close_failed", order, detail={"code": exc.code})
    return processed


def maintain_subscriptions():
    user_ids = Subscription.objects.filter(
        status=SubscriptionStatus.ACTIVE,
        ends_at__lte=timezone.now(),
    ).values_list("user_id", flat=True)
    from app.accounts.models import User

    processed = 0
    for user in User.objects.filter(id__in=user_ids).iterator():
        refresh_subscription_state(user)
        processed += 1
    return processed


def send_expiry_reminders():
    now = timezone.now()
    created = 0
    subscriptions = Subscription.objects.select_related("user", "plan").filter(
        status=SubscriptionStatus.ACTIVE,
        ends_at__gt=now,
        ends_at__lte=now + timedelta(days=8),
    )
    for subscription in subscriptions.iterator():
        days = (timezone.localtime(subscription.ends_at).date() - timezone.localdate()).days
        if days not in (7, 3, 1, 0):
            continue
        _, was_created = UserNotification.objects.get_or_create(
            user=subscription.user,
            dedupe_key=f"expiry-reminder:{subscription.id}:{days}:{timezone.localdate().isoformat()}",
            defaults={
                "kind": "EXPIRY",
                "title": "套餐即将到期" if days else "套餐今天到期",
                "content": f"你的{subscription.plan.name}将在 {days} 天后到期，请及时续费。" if days else f"你的{subscription.plan.name}将在今天到期，请及时续费。",
            },
        )
        created += int(was_created)
    return created


def refresh_pool_health():
    from app.cron import probe_web_session

    updated = 0
    policies = PoolAccountPolicy.objects.select_related("account").filter(enabled=True)
    for policy in policies.iterator():
        try:
            policy.account.refresh_auth_diagnostics(force=True)
            policy.account.refresh_from_db()
            web_healthy = True
            web_error = ""
            if settings.PUBLIC_SITE_URL and policy.account.session_token_valid:
                web_healthy, web_error = probe_web_session(
                    policy.account,
                    public_url=settings.PUBLIC_SITE_URL,
                )
                if not web_healthy and _web_probe_invalidates_credentials(web_error):
                    policy.account.access_token_valid = False
                    policy.account.session_token_valid = False
                if not web_healthy:
                    policy.account.last_error = web_error
                    update_fields = ["last_error", "updated_time"]
                    if _web_probe_invalidates_credentials(web_error):
                        update_fields.extend(["access_token_valid", "session_token_valid"])
                    policy.account.save(update_fields=update_fields)
            policy.health_status = (
                "HEALTHY"
                if policy.account.auth_status and (
                    policy.account.access_token_valid or policy.account.session_token_valid
                ) and web_healthy
                else "DEGRADED"
            )
        except Exception:
            policy.health_status = "DEGRADED"
        policy.last_health_check_at = timezone.now()
        policy.save(update_fields=["health_status", "last_health_check_at", "updated_at"])
        updated += 1
    return updated

import calendar
import secrets
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
)
from app.billing.payment import PaymentEvent
from app.billing.selectors import CAPACITY_STATUSES, capacity_snapshot
from app.chatgpt.models import ChatgptAccount


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


def audit(action, target, *, actor=None, detail=None, ip_address=None):
    return AuditLog.objects.create(
        actor=actor,
        action=action,
        target_type=target.__class__.__name__,
        target_id=str(getattr(target, "pk", "") or ""),
        detail=detail or {},
        ip_address=ip_address,
    )


def _sanitize_payment_payload(value):
    blocked_fragments = ("secret", "token", "cookie", "password", "signature", "private_key")
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if any(fragment in str(key).lower() for fragment in blocked_fragments) else _sanitize_payment_payload(item)
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
        raise CapacityUnavailable(f"{plan.name}号池已满，请联系管理员增加 Plus 账号")
    return PoolReservation.objects.create(
        user=user,
        plan=plan,
        pool=plan.pool,
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
def create_order(user, offer, *, provider="mock", idempotency_key=None, actor=None, metadata=None):
    if not isinstance(offer, PlanOffer):
        offer = PlanOffer.objects.select_related("plan", "plan__pool").get(pk=offer)
    else:
        offer = PlanOffer.objects.select_related("plan", "plan__pool").get(pk=offer.pk)
    if offer.is_archived or offer.is_draft or not offer.is_purchase_enabled:
        raise BillingError("该套餐当前不可购买", code="offer_unavailable")
    if offer.plan.is_archived or not offer.plan.is_active:
        raise BillingError("该套餐已下架", code="plan_unavailable")
    if idempotency_key:
        existing = Order.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            if existing.user_id != user.id:
                raise BillingError("幂等键已被其他订单使用", code="idempotency_conflict")
            return existing

    subscription = refresh_subscription_state(user)
    order_type = _determine_order_type(subscription, offer.plan)
    if order_type == OrderType.DOWNGRADE and subscription.scheduled_plan_id:
        raise BillingError("已经存在待生效的降级套餐", code="downgrade_already_scheduled")

    order = Order.objects.create(
        order_no=f"B{timezone.now():%Y%m%d%H%M%S}{secrets.token_hex(4).upper()}",
        user=user,
        plan=offer.plan,
        offer=offer,
        order_type=order_type,
        provider=provider,
        idempotency_key=idempotency_key,
        plan_snapshot={
            "code": offer.plan.code,
            "name": offer.plan.name,
            "tagline": offer.plan.tagline,
            "pool_tier": offer.plan.pool_tier,
        },
        offer_snapshot={
            "code": offer.code,
            "name": offer.name,
            "months": offer.months,
        },
        price_cents=offer.price_cents,
        currency=offer.currency,
        metadata=metadata or {},
    )
    if order_type != OrderType.RENEW:
        _reserve_capacity(user=user, plan=offer.plan, order=order, subscription=subscription)
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
            subscription.ends_at = add_months(now, order.offer.months)
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
                ends_at=add_months(now, order.offer.months),
            )
        _activate_order_reservation(order, subscription)
    elif order.order_type == OrderType.RENEW:
        if not subscription:
            raise PaymentRejected("续费订单缺少原订阅")
        base = max(subscription.ends_at, now)
        subscription.ends_at = add_months(base, order.offer.months)
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
        subscription.ends_at = add_months(subscription.ends_at, order.offer.months)
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
        subscription.scheduled_months = order.offer.months
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
    return subscription


def complete_order(order, event: PaymentEvent, *, actor=None):
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
                    order.provider_order_id = event.provider_transaction_id
                    order.save(update_fields=["status", "paid_at", "provider_order_id", "updated_at"])
                    UserNotification.objects.create(
                        user=order.user,
                        kind="PAYMENT",
                        title="套餐已开通",
                        content=f"{order.plan.name}已开通，有效期至 {timezone.localtime(subscription.ends_at):%Y-%m-%d %H:%M}。",
                    )
                    audit("order.paid", order, actor=actor, detail={"event_id": event.event_id})
    if rejection:
        raise rejection
    return order, subscription


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
        and policy.health_status != "DISABLED"
        and not account.is_archived
        and account.auth_status
        and (account.access_token_valid or account.session_token_valid)
        and "plus" in (account.plan_type or "").lower()
    )


@transaction.atomic
def ensure_assignment(subscription, *, reason="first_use", force=False):
    subscription = Subscription.objects.select_for_update().select_related(
        "user",
        "plan",
        "plan__pool",
    ).get(pk=subscription.pk)
    if not subscription.is_service_active:
        raise SubscriptionInactive("套餐已到期或暂停，请续费后再使用")

    assignment = AccountAssignment.objects.select_for_update().select_related("account").filter(
        subscription=subscription,
    ).first()
    if assignment and assignment.active and not force:
        policy = PoolAccountPolicy.objects.select_related("account").filter(
            account=assignment.account,
            pool=subscription.plan.pool,
            tier=subscription.plan.pool_tier,
        ).first()
        if policy and _policy_is_usable(policy):
            return assignment

    policies = list(
        PoolAccountPolicy.objects.select_for_update()
        .select_related("account")
        .filter(
            pool=subscription.plan.pool,
            tier=subscription.plan.pool_tier,
            enabled=True,
            account__is_archived=False,
            account__auth_status=True,
        )
        .exclude(health_status="DISABLED")
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
    candidates = [
        policy
        for policy in policies
        if binding_counts.get(policy.account_id, 0) < policy.binding_limit
        and not (force and assignment and policy.account_id == assignment.account_id)
    ]
    if not candidates:
        raise CapacityUnavailable("当前套餐号池没有可分配的健康 Plus 账号")
    selected = min(
        candidates,
        key=lambda policy: (
            binding_counts.get(policy.account_id, 0) / policy.binding_limit,
            recent_counts.get(policy.account_id, 0),
            policy.account_id,
        ),
    )
    old_account = assignment.account if assignment else None
    now = timezone.now()
    if assignment:
        assignment.pool = subscription.plan.pool
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
            pool=subscription.plan.pool,
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
    PoolReservation.objects.filter(
        subscription=subscription,
        plan=subscription.plan,
        status=ReservationStatus.ACTIVE,
    ).update(status=ReservationStatus.ASSIGNED, updated_at=now)
    audit(
        "assignment.created" if not old_account else "assignment.migrated",
        assignment,
        detail={"reason": reason, "pool_id": subscription.plan.pool_id},
    )
    return assignment


def resolve_managed_account(user):
    if not billing_enabled() or user.is_staff or user.is_superuser:
        return None
    subscription = refresh_subscription_state(user)
    if subscription:
        if not subscription.is_service_active:
            raise SubscriptionInactive("套餐已到期或暂停，请先续费")
        return ensure_assignment(subscription).account
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
    updated = 0
    policies = PoolAccountPolicy.objects.select_related("account").filter(enabled=True)
    for policy in policies.iterator():
        try:
            policy.account.refresh_auth_diagnostics(force=True)
            policy.account.refresh_from_db()
            policy.health_status = (
                "HEALTHY"
                if policy.account.auth_status and (
                    policy.account.access_token_valid or policy.account.session_token_valid
                )
                else "DEGRADED"
            )
        except Exception:
            policy.health_status = "DEGRADED"
        policy.last_health_check_at = timezone.now()
        policy.save(update_fields=["health_status", "last_health_check_at", "updated_at"])
        updated += 1
    return updated

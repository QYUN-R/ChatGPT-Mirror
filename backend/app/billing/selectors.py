from django.db.models import Count, Q, Sum
from django.utils import timezone

from app.billing.models import (
    AccountAssignment,
    Order,
    Plan,
    PoolAccountPolicy,
    PoolReservation,
    ReservationStatus,
    Subscription,
    SubscriptionStatus,
    UsageEvent,
)


CAPACITY_STATUSES = (
    ReservationStatus.HELD,
    ReservationStatus.ACTIVE,
    ReservationStatus.ASSIGNED,
)


def current_subscription(user):
    return (
        Subscription.objects.select_related(
            "plan",
            "plan__pool",
            "offer",
            "scheduled_plan",
            "scheduled_offer",
        )
        .filter(user=user)
        .first()
    )


def capacity_snapshot(plan, *, lock=False):
    policies = PoolAccountPolicy.objects.filter(
        pool=plan.pool,
        tier=plan.pool_tier,
        enabled=True,
        account__is_archived=False,
        account__auth_status=True,
    ).filter(
        Q(account__access_token_valid=True) | Q(account__session_token_valid=True)
    ).exclude(health_status="DISABLED")
    if lock:
        policies = policies.select_for_update()
    total = policies.aggregate(total=Sum("binding_limit"))["total"] or 0
    now = timezone.now()
    used = (
        PoolReservation.objects.filter(
            pool=plan.pool,
            status__in=CAPACITY_STATUSES,
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .aggregate(total=Sum("slot_count"))["total"]
        or 0
    )
    return {
        "total": int(total),
        "used": int(used),
        "available": max(int(total) - int(used), 0),
        "is_full": int(used) >= int(total),
    }


def usage_snapshot(user):
    now = timezone.now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    queryset = UsageEvent.objects.filter(user=user)
    return {
        "today": queryset.filter(request_at__gte=day_start).count(),
        "month": queryset.filter(request_at__gte=month_start).count(),
        "success": queryset.filter(result="SUCCESS", request_at__gte=month_start).count(),
    }


def admin_overview():
    now = timezone.now()
    return {
        "plans": Plan.objects.filter(is_archived=False).count(),
        "active_subscriptions": Subscription.objects.filter(
            status=SubscriptionStatus.ACTIVE,
            ends_at__gt=now,
        ).count(),
        "pending_orders": Order.objects.filter(status="PENDING").count(),
        "active_assignments": AccountAssignment.objects.filter(active=True).count(),
    }

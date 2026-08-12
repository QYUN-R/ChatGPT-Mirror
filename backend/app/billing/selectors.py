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


def plan_pool_ids(plan):
    links = plan.pool_links.filter(is_active=True).order_by("priority", "id")
    pool_ids = list(links.values_list("pool_id", flat=True))
    if not pool_ids and plan.pool_id:
        pool_ids = [plan.pool_id]
    return pool_ids


def pool_capacity_snapshots(plan, *, lock=False):
    pool_ids = plan_pool_ids(plan)
    policies = PoolAccountPolicy.objects.filter(
        pool_id__in=pool_ids,
        tier=plan.pool_tier,
        enabled=True,
        account__is_archived=False,
        account__auth_status=True,
    ).filter(
        Q(account__access_token_valid=True) | Q(account__session_token_valid=True)
    ).exclude(health_status="DISABLED")
    if lock:
        list(policies.select_for_update(of=("self",)).values_list("id", flat=True))
    totals = {
        row["pool_id"]: int(row["total"] or 0)
        for row in policies.values("pool_id").annotate(total=Sum("binding_limit"))
    }
    now = timezone.now()
    reservations = PoolReservation.objects.filter(
        pool_id__in=pool_ids,
        status__in=CAPACITY_STATUSES,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    if lock:
        list(reservations.select_for_update().values_list("id", flat=True))
    used = {
        row["pool_id"]: int(row["total"] or 0)
        for row in (
            reservations
            .values("pool_id")
            .annotate(total=Sum("slot_count"))
        )
    }
    return [
        {
            "pool_id": pool_id,
            "total": totals.get(pool_id, 0),
            "used": used.get(pool_id, 0),
            "available": max(totals.get(pool_id, 0) - used.get(pool_id, 0), 0),
            "is_full": used.get(pool_id, 0) >= totals.get(pool_id, 0),
        }
        for pool_id in pool_ids
    ]


def capacity_snapshot(plan, *, lock=False):
    pools = pool_capacity_snapshots(plan, lock=lock)
    physical_total = sum(item["total"] for item in pools)
    physical_used = sum(item["used"] for item in pools)
    physical_available = sum(item["available"] for item in pools)
    now = timezone.now()
    plan_reservations = PoolReservation.objects.filter(
        plan=plan,
        status__in=CAPACITY_STATUSES,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    if lock:
        list(plan_reservations.select_for_update().values_list("id", flat=True))
    plan_used = (
        plan_reservations
        .aggregate(total=Sum("slot_count"))["total"]
        or 0
    )
    user_limit = max(int(plan.user_limit or 0), 0)
    plan_available = max(user_limit - int(plan_used), 0) if user_limit else physical_available
    available = min(physical_available, plan_available)
    return {
        "total": int(physical_total),
        "used": int(physical_used),
        "available": max(int(available), 0),
        "is_full": int(available) < 1,
        "user_limit": user_limit,
        "plan_used": int(plan_used),
        "physical_total": int(physical_total),
        "physical_used": int(physical_used),
        "physical_available": int(physical_available),
        "pools": pools,
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

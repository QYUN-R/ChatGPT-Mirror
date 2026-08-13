from django.conf import settings
from django.utils import timezone


def effective_device_policy(user):
    if user.device_policy_managed_by_plan:
        enabled = bool(settings.DEFAULT_MULTI_DEVICE_ENABLED)
        limit = max(int(settings.DEFAULT_DEVICE_LIMIT), 1)
        source = "system"
    else:
        enabled = bool(user.multi_device_enabled)
        limit = max(int(user.device_limit or 1), 1)
        source = "user"
    plan_id = None
    plan_name = ""

    if user.device_policy_managed_by_plan:
        from app.billing.models import SubscriptionStatus

        try:
            subscription = user.billing_subscription
        except Exception:
            subscription = None
        if (
            subscription
            and subscription.status == SubscriptionStatus.ACTIVE
            and subscription.ends_at > timezone.now()
        ):
            enabled = bool(subscription.plan.multi_device_enabled)
            limit = max(int(subscription.plan.device_limit or 1), 1)
            source = "plan"
            plan_id = subscription.plan_id
            plan_name = subscription.plan.name

    if not enabled:
        limit = 1
    return {
        "enabled": enabled,
        "limit": limit,
        "source": source,
        "plan_id": plan_id,
        "plan_name": plan_name,
        "new_device_verification_enabled": bool(user.new_device_verification_enabled),
    }


def enforce_device_policy(user):
    from app.accounts.authentication import revoke_registered_device_sessions

    policy = effective_device_policy(user)
    return revoke_registered_device_sessions(user, keep_count=policy["limit"])


def enforce_device_policy_with_gateway(user):
    from rest_framework.exceptions import ValidationError

    from app.utils import req_gateway

    revoked_subjects = enforce_device_policy(user)
    if revoked_subjects:
        try:
            req_gateway("post", "/api/logout", json={"user_name": user.username})
        except ValidationError:
            pass
    return revoked_subjects


def schedule_device_policy_enforcement(user_id):
    from django.db import transaction

    def enforce_after_commit():
        from app.accounts.models import User

        user = User.objects.filter(pk=user_id).first()
        if user:
            enforce_device_policy_with_gateway(user)

    transaction.on_commit(enforce_after_commit)

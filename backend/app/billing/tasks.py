from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

from app.billing.services import (
    expire_pending_alipay_orders,
    expire_stale_reservations,
    maintain_subscriptions,
    reconcile_pending_alipay_orders,
    refresh_pool_health,
    send_expiry_reminders,
)
from app.billing.models import UserNotification


@shared_task(name="app.billing.tasks.expire_stale_reservations_task")
def expire_stale_reservations_task():
    if not settings.BILLING_ENABLED:
        return 0
    return expire_stale_reservations()


@shared_task(name="app.billing.tasks.reconcile_pending_alipay_orders_task")
def reconcile_pending_alipay_orders_task():
    if not settings.BILLING_ENABLED:
        return 0
    return reconcile_pending_alipay_orders()


@shared_task(name="app.billing.tasks.expire_pending_alipay_orders_task")
def expire_pending_alipay_orders_task():
    if not settings.BILLING_ENABLED:
        return 0
    return expire_pending_alipay_orders()


@shared_task(name="app.billing.tasks.subscription_maintenance_task")
def subscription_maintenance_task():
    if not settings.BILLING_ENABLED:
        return 0
    return maintain_subscriptions()


@shared_task(name="app.billing.tasks.expiry_reminders_task")
def expiry_reminders_task():
    if not settings.BILLING_ENABLED:
        return 0
    return send_expiry_reminders()


@shared_task(name="app.billing.tasks.account_health_task")
def account_health_task():
    if not settings.BILLING_ENABLED:
        return 0
    return refresh_pool_health()


@shared_task(name="app.billing.tasks.send_notification_email_task", autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def send_notification_email_task(notification_id):
    if not settings.EMAIL_NOTIFICATIONS_ENABLED:
        return False
    notification = UserNotification.objects.select_related("user").get(pk=notification_id)
    recipient = (notification.user.email or notification.user.username or "").strip()
    if "@" not in recipient:
        return False
    send_mail(
        subject=notification.title,
        message=notification.content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient],
        fail_silently=False,
    )
    return True

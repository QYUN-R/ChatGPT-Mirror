from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from app.billing.models import Subscription, UserNotification


@receiver(post_save, sender=UserNotification)
def enqueue_notification_email(sender, instance, created, **kwargs):
    if not created or not getattr(settings, "EMAIL_NOTIFICATIONS_ENABLED", False):
        return

    def enqueue():
        try:
            from app.billing.tasks import send_notification_email_task

            send_notification_email_task.delay(instance.id)
        except Exception:
            # 站内通知已经持久化；邮件队列异常不能回滚业务事务。
            return

    transaction.on_commit(enqueue)


@receiver(post_save, sender=Subscription)
def enforce_subscription_device_policy(sender, instance, **kwargs):
    from app.accounts.device_policy import schedule_device_policy_enforcement

    schedule_device_policy_enforcement(instance.user_id)

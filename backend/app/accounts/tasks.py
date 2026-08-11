import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from app.accounts.email_auth import verification_message
from app.accounts.models import EmailDeliveryStatus, EmailVerificationChallenge


logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="app.accounts.tasks.send_verification_email_task",
    acks_late=True,
    reject_on_worker_lost=True,
)
def send_verification_email_task(self, challenge_pk):
    with transaction.atomic():
        challenge = EmailVerificationChallenge.objects.select_for_update().filter(pk=challenge_pk).first()
        if not challenge or challenge.delivery_status == EmailDeliveryStatus.SENT:
            return False
        if (
            challenge.invalidated_at
            or challenge.locked_at
            or challenge.consumed_at
            or challenge.expires_at <= timezone.now()
        ):
            return False

        challenge.delivery_attempt_count += 1
        attempt = challenge.delivery_attempt_count
        challenge.delivery_error = ""
        delivery_code = challenge.delivery_token
        if not delivery_code:
            challenge.delivery_status = EmailDeliveryStatus.FAILED
            challenge.invalidated_at = timezone.now()
            challenge.save(update_fields=["delivery_status", "invalidated_at"])
            return False
        challenge.save(update_fields=["delivery_attempt_count", "delivery_error"])

    try:
        sent_count = send_mail(
            subject="chat2 邮箱验证码",
            message=verification_message(delivery_code, challenge.purpose),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[challenge.email],
            fail_silently=False,
        )
        if sent_count != 1:
            raise RuntimeError("SMTP backend did not accept the verification email")
    except Exception as exc:
        max_attempts = max(1, settings.EMAIL_VERIFICATION_DELIVERY_MAX_ATTEMPTS)
        with transaction.atomic():
            live_challenge = EmailVerificationChallenge.objects.select_for_update().filter(pk=challenge_pk).first()
            if not live_challenge or live_challenge.delivery_status == EmailDeliveryStatus.SENT:
                return False
            live_challenge.delivery_error = exc.__class__.__name__[:64]
            if attempt >= max_attempts:
                live_challenge.delivery_status = EmailDeliveryStatus.FAILED
                live_challenge.invalidated_at = timezone.now()
                live_challenge.delivery_token = ""
                live_challenge.save(
                    update_fields=["delivery_status", "delivery_error", "invalidated_at", "delivery_token"]
                )
                logger.warning(
                    "Verification email delivery failed: purpose=%s attempts=%s error=%s",
                    live_challenge.purpose,
                    attempt,
                    exc.__class__.__name__,
                )
                return False
            live_challenge.save(update_fields=["delivery_error"])

        retry_delay = min(60, 2 ** max(0, attempt - 1))
        raise self.retry(
            exc=exc,
            countdown=retry_delay,
            max_retries=max_attempts - 1,
        )

    with transaction.atomic():
        live_challenge = EmailVerificationChallenge.objects.select_for_update().filter(pk=challenge_pk).first()
        if not live_challenge or live_challenge.delivery_status == EmailDeliveryStatus.FAILED:
            return False
        live_challenge.delivery_status = EmailDeliveryStatus.SENT
        live_challenge.delivery_error = ""
        live_challenge.delivered_at = timezone.now()
        live_challenge.delivery_token = ""
        live_challenge.save(
            update_fields=["delivery_status", "delivery_error", "delivered_at", "delivery_token"]
        )
    return True


@shared_task(name="app.accounts.tasks.recover_pending_verification_emails_task")
def recover_pending_verification_emails_task():
    now = timezone.now()
    expired = EmailVerificationChallenge.objects.filter(
        delivery_status=EmailDeliveryStatus.PENDING,
        expires_at__lte=now,
    ).update(
        delivery_status=EmailDeliveryStatus.FAILED,
        invalidated_at=now,
        delivery_token="",
        delivery_error="Expired",
    )
    recovery_cutoff = now - timedelta(seconds=90)
    pending_ids = list(
        EmailVerificationChallenge.objects.filter(
            delivery_status=EmailDeliveryStatus.PENDING,
            delivery_queued_at__lt=recovery_cutoff,
            expires_at__gt=now,
            invalidated_at__isnull=True,
            locked_at__isnull=True,
            consumed_at__isnull=True,
        ).values_list("pk", flat=True)[:100]
    )
    queued = 0
    for challenge_id in pending_ids:
        try:
            send_verification_email_task.delay(challenge_id)
        except Exception:
            continue
        EmailVerificationChallenge.objects.filter(
            pk=challenge_id,
            delivery_status=EmailDeliveryStatus.PENDING,
        ).update(delivery_queued_at=now)
        queued += 1
    return expired + queued

import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core import signing
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from app.accounts.models import (
    EmailDeliveryStatus,
    EmailVerificationChallenge,
    EmailVerificationPurpose,
    User,
)


BINDING_TICKET_SALT = "accounts.email-binding.v1"
DEVICE_LOGIN_TICKET_SALT = "accounts.device-login.v1"
logger = logging.getLogger(__name__)


def normalize_email(value):
    email = str(value or "").strip().lower()
    if "@" not in email or email.count("@") != 1:
        raise ValidationError({"email": "请输入有效邮箱地址"})

    local, domain = email.rsplit("@", 1)
    if not local or domain not in settings.EMAIL_ALLOWED_DOMAINS:
        raise ValidationError({"email": "仅支持 QQ、网易或 Google 邮箱"})
    return email


def has_verified_email(user):
    return bool(user.email and user.email_verified_at)


def needs_email_binding(user):
    return not (user.is_staff or user.is_superuser) and not has_verified_email(user)


def issue_binding_ticket(user):
    if not user.is_active or has_verified_email(user):
        raise ValidationError({"message": "邮箱绑定会话无效，请重新登录"})
    return signing.dumps({"user_id": user.pk}, salt=BINDING_TICKET_SALT, compress=True)


def load_binding_ticket(value):
    try:
        payload = signing.loads(
            str(value or ""),
            salt=BINDING_TICKET_SALT,
            max_age=settings.EMAIL_BINDING_TICKET_TTL_SECONDS,
        )
    except signing.BadSignature as exc:
        raise ValidationError({"message": "邮箱绑定会话已过期，请重新登录"}) from exc

    user = User.objects.filter(pk=payload.get("user_id"), is_active=True).first()
    # 管理员不被强制绑定邮箱，但可以在账户中心自愿完成同一套安全流程。
    if not user or has_verified_email(user):
        raise ValidationError({"message": "邮箱绑定会话无效，请重新登录"})
    return user


def _ip_digest(ip_address):
    if not ip_address:
        return ""
    return hashlib.sha256(str(ip_address).encode("utf-8")).hexdigest()


def _ensure_email_delivery_ready():
    if not settings.EMAIL_VERIFICATION_ENABLED:
        raise ValidationError({"message": "邮箱验证服务暂未开放"})
    if not settings.EMAIL_HOST or not settings.EMAIL_HOST_USER or not settings.EMAIL_HOST_PASSWORD:
        raise ValidationError({"message": "邮箱验证服务暂未配置完成"})


def verification_message(code, purpose):
    label = {
        EmailVerificationPurpose.REGISTER: "注册",
        EmailVerificationPurpose.PASSWORD_RESET: "重置密码",
        EmailVerificationPurpose.EMAIL_BINDING: "绑定邮箱",
        EmailVerificationPurpose.EMAIL_CHANGE: "修改绑定邮箱",
        EmailVerificationPurpose.DEVICE_LOGIN: "新设备登录",
    }.get(purpose, "安全验证")
    return (
        f"你正在进行{label}。\n\n"
        f"验证码：{code}\n"
        f"有效期：{settings.EMAIL_VERIFICATION_CODE_TTL_SECONDS // 60} 分钟。\n\n"
        "请勿将验证码、密码或登录信息提供给任何人。"
    )


def issue_device_login_ticket(user, device_key_hash):
    return signing.dumps(
        {"user_id": user.pk, "device_key_hash": str(device_key_hash)},
        salt=DEVICE_LOGIN_TICKET_SALT,
        compress=True,
    )


def load_device_login_ticket(value):
    try:
        payload = signing.loads(
            str(value or ""),
            salt=DEVICE_LOGIN_TICKET_SALT,
            max_age=settings.EMAIL_BINDING_TICKET_TTL_SECONDS,
        )
    except signing.BadSignature as exc:
        raise ValidationError({"message": "新设备验证会话已过期，请重新登录"}) from exc
    user = User.objects.filter(pk=payload.get("user_id"), is_active=True).first()
    device_key_hash = str(payload.get("device_key_hash") or "")
    if not user or len(device_key_hash) != 64:
        raise ValidationError({"message": "新设备验证会话无效，请重新登录"})
    return user, device_key_hash


def create_verification_challenge(*, email, purpose, user=None, ip_address=""):
    _ensure_email_delivery_ready()
    now = timezone.now()
    email = normalize_email(email)
    window_start = now - timedelta(seconds=settings.EMAIL_VERIFICATION_RATE_WINDOW_SECONDS)

    with transaction.atomic():
        recent = EmailVerificationChallenge.objects.select_for_update().filter(
            email=email,
            purpose=purpose,
            created_at__gte=window_start,
        )
        if user is None:
            recent = recent.filter(user__isnull=True)
        else:
            recent = recent.filter(user=user)

        latest = recent.order_by("-created_at").first()
        if latest and latest.created_at >= now - timedelta(
            seconds=settings.EMAIL_VERIFICATION_RESEND_SECONDS
        ):
            raise ValidationError({"message": "验证码已发送，请稍后再试"})
        if recent.count() >= settings.EMAIL_VERIFICATION_MAX_SENDS_PER_WINDOW:
            raise ValidationError({"message": "验证码发送次数过多，请稍后再试"})

        recent.filter(
            consumed_at__isnull=True,
            invalidated_at__isnull=True,
            locked_at__isnull=True,
        ).update(invalidated_at=now)

        code = f"{secrets.randbelow(1_000_000):06d}"
        challenge = EmailVerificationChallenge.objects.create(
            user=user,
            email=email,
            purpose=purpose,
            code_hash=make_password(code),
            delivery_status=EmailDeliveryStatus.PENDING,
            delivery_token=code,
            delivery_queued_at=now,
            requested_ip_hash=_ip_digest(ip_address),
            expires_at=now + timedelta(seconds=settings.EMAIL_VERIFICATION_CODE_TTL_SECONDS),
        )

    try:
        from app.accounts.tasks import send_verification_email_task

        send_verification_email_task.delay(challenge.pk)
    except Exception as exc:
        logger.warning(
            "Verification email queue unavailable: purpose=%s error=%s",
            purpose,
            exc.__class__.__name__,
        )

    return challenge


def consume_verification_challenge(*, purpose, code, email=None, user=None):
    now = timezone.now()
    if not str(code or "").isdigit() or len(str(code)) != 6:
        raise ValidationError({"code": "验证码无效或已过期"})

    failure = False
    with transaction.atomic():
        queryset = EmailVerificationChallenge.objects.select_for_update().filter(
            purpose=purpose,
            consumed_at__isnull=True,
            invalidated_at__isnull=True,
            locked_at__isnull=True,
            expires_at__gt=now,
        )
        if user is None:
            queryset = queryset.filter(user__isnull=True, email=normalize_email(email))
        else:
            queryset = queryset.filter(user=user)
            if email:
                queryset = queryset.filter(email=normalize_email(email))

        challenge = queryset.order_by("-created_at").first()
        if not challenge:
            failure = True
        elif not check_password(str(code), challenge.code_hash):
            challenge.attempt_count += 1
            update_fields = ["attempt_count"]
            if challenge.attempt_count >= settings.EMAIL_VERIFICATION_MAX_ATTEMPTS:
                challenge.locked_at = now
                update_fields.append("locked_at")
            challenge.save(update_fields=update_fields)
            failure = True
        else:
            challenge.consumed_at = now
            challenge.save(update_fields=["consumed_at"])
            return challenge

    if failure:
        raise ValidationError({"code": "验证码无效或已过期"})

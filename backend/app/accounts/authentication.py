from datetime import timedelta
import hashlib
import secrets
import re
import uuid

from django.conf import settings
from django.middleware.csrf import CsrfViewMiddleware
from django.utils import timezone
from rest_framework.authentication import TokenAuthentication
from rest_framework.authentication import get_authorization_header
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied

from app.accounts.models import UserDeviceSession
from app.settings import FREE_ACCOUNT_USERNAME


AUTH_COOKIE_NAME = "mirror_api_session"
DEVICE_COOKIE_NAME = "mirror_device_session"
DEVICE_ID_COOKIE_NAME = "mirror_device_id"


def _device_token_hash(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _device_key_hash(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _device_identity(request):
    cached = getattr(request, "_mirror_device_identity", None)
    if cached:
        return cached
    raw = request.COOKIES.get(DEVICE_ID_COOKIE_NAME, "").strip()
    if len(raw) < 32:
        raw = secrets.token_urlsafe(32)
    identity = (raw, _device_key_hash(raw))
    request._mirror_device_identity = identity
    return identity


def device_identity(request):
    return _device_identity(request)


def _device_details(request):
    user_agent = str(request.headers.get("User-Agent") or "")[:512]
    value = user_agent.lower()
    if re.search(r"ipad|tablet|playbook|silk", value):
        device_type = "tablet"
    elif re.search(r"iphone|ipod|android.*mobile|mobile", value):
        device_type = "mobile"
    else:
        device_type = "desktop"

    if "edg/" in value:
        browser = "Edge"
    elif "opr/" in value or "opera" in value:
        browser = "Opera"
    elif "firefox/" in value:
        browser = "Firefox"
    elif "crios/" in value:
        browser = "Chrome iOS"
    elif "chrome/" in value:
        browser = "Chrome"
    elif "safari/" in value:
        browser = "Safari"
    else:
        browser = "其他浏览器"

    if "iphone" in value or "ipad" in value or "ios" in value:
        os_name = "iOS"
    elif "android" in value:
        os_name = "Android"
    elif "windows" in value:
        os_name = "Windows"
    elif "mac os" in value or "macintosh" in value:
        os_name = "macOS"
    elif "linux" in value:
        os_name = "Linux"
    else:
        os_name = "其他系统"
    return user_agent, device_type, browser, os_name


def _device_cookie_options():
    return {
        "max_age": settings.API_TOKEN_TTL_SECONDS,
        "httponly": True,
        "secure": settings.SESSION_COOKIE_SECURE,
        "samesite": "Strict",
        "path": "/0x/",
    }


def issue_device_session(request, user, *, policy=None, verified=False):
    from app.accounts.device_policy import effective_device_policy

    now = timezone.now()
    policy = policy or effective_device_policy(user)
    revoked_subjects = []
    device_id, device_key_hash = _device_identity(request)
    user_agent, device_type, browser_name, os_name = _device_details(request)
    active_sessions = UserDeviceSession.objects.select_for_update().filter(
        user=user,
        revoked_at__isnull=True,
        expires_at__gt=now,
    )
    registered_session = (
        UserDeviceSession.objects.select_for_update()
        .filter(user=user, device_key_hash=device_key_hash)
        .first()
    )

    if (
        policy.get("new_device_verification_enabled")
        and not (user.is_staff or user.is_superuser)
        and not verified
        and (not registered_session or not registered_session.verified_at)
    ):
        raise AuthenticationFailed("新设备需要先完成邮箱验证")

    current_is_active = bool(
        registered_session
        and registered_session.revoked_at is None
        and registered_session.expires_at > now
    )

    if not policy["enabled"]:
        revoked_subjects = [
            device_subject(user, session)
            for session in active_sessions.exclude(pk=getattr(registered_session, "pk", None))
        ]
        active_sessions.exclude(pk=getattr(registered_session, "pk", None)).update(revoked_at=now)
    elif not current_is_active and active_sessions.count() >= policy["limit"]:
        raise AuthenticationFailed(
            f"已达到 {policy['limit']} 台设备上限，请先在账户中心移除旧设备"
        )

    raw_token = secrets.token_urlsafe(32)
    if registered_session:
        session = registered_session
        session.token_hash = _device_token_hash(raw_token)
        session.user_agent = user_agent
        session.device_type = device_type
        session.browser_name = browser_name
        session.os_name = os_name
        session.ip_address = _request_ip(request)
        session.expires_at = now + timedelta(seconds=settings.API_TOKEN_TTL_SECONDS)
        session.revoked_at = None
        if verified and not session.verified_at:
            session.verified_at = now
        session.save()
    else:
        is_primary = not UserDeviceSession.objects.filter(user=user, is_primary=True).exists()
        subject_id = uuid.uuid4()
        gateway_subject = user.username if is_primary else f"{user.username}:device:{subject_id}"
        session = UserDeviceSession.objects.create(
            user=user,
            token_hash=_device_token_hash(raw_token),
            device_key_hash=device_key_hash,
            subject_id=subject_id,
            gateway_subject=gateway_subject,
            is_primary=is_primary,
            user_agent=user_agent,
            device_type=device_type,
            browser_name=browser_name,
            os_name=os_name,
            ip_address=_request_ip(request),
            verified_at=now if verified else None,
            expires_at=now + timedelta(seconds=settings.API_TOKEN_TTL_SECONDS),
        )
    return raw_token, device_id, session, revoked_subjects


def registered_device_session(request, user):
    raw, device_key_hash = _device_identity(request)
    return UserDeviceSession.objects.filter(
        user=user,
        device_key_hash=device_key_hash,
    ).first()


def active_device_sessions(user):
    return UserDeviceSession.objects.filter(
        user=user,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    )


def ensure_device_capacity(request, user, policy):
    if not policy["enabled"]:
        return
    registered = registered_device_session(request, user)
    if (
        registered
        and registered.revoked_at is None
        and registered.expires_at > timezone.now()
    ):
        return
    if active_device_sessions(user).count() >= policy["limit"]:
        raise AuthenticationFailed(
            f"已达到 {policy['limit']} 台设备上限，请先在账户中心移除旧设备"
        )


def get_device_session(request, user, *, touch=True):
    raw_token = request.COOKIES.get(DEVICE_COOKIE_NAME, "").strip()
    if not raw_token:
        return None
    session = UserDeviceSession.objects.filter(
        user=user,
        token_hash=_device_token_hash(raw_token),
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).first()
    if session and touch:
        now = timezone.now()
        if session.last_seen_at <= now - timedelta(minutes=5):
            session.last_seen_at = now
            session.save(update_fields=["last_seen_at"])
    return session


def revoke_device_session(request, user):
    session = get_device_session(request, user, touch=False)
    if session:
        session.revoked_at = timezone.now()
        session.save(update_fields=["revoked_at"])
    return session


def has_device_cookie(request):
    return bool(request.COOKIES.get(DEVICE_COOKIE_NAME, "").strip())


def revoke_all_device_sessions(user):
    now = timezone.now()
    active_sessions = UserDeviceSession.objects.filter(
        user=user,
        revoked_at__isnull=True,
        expires_at__gt=now,
    )
    subjects = [
        device_subject(user, session)
        for session in active_sessions.only("subject_id")
    ]
    active_sessions.update(revoked_at=now)
    return subjects


def revoke_registered_device_sessions(user, *, keep_count):
    now = timezone.now()
    active_sessions = list(
        UserDeviceSession.objects.filter(
            user=user,
            revoked_at__isnull=True,
            expires_at__gt=now,
        ).order_by("-is_primary", "-last_seen_at", "-id")
    )
    revoked = active_sessions[max(int(keep_count or 1), 1):]
    if revoked:
        UserDeviceSession.objects.filter(pk__in=[item.pk for item in revoked]).update(revoked_at=now)
    return [device_subject(user, item) for item in revoked]


def device_subject(user, session):
    if not session:
        return user.username
    return session.gateway_subject or (
        user.username if session.is_primary else f"{user.username}:device:{session.subject_id.hex}"
    )


def _request_ip(request):
    from app.utils import get_client_ip

    return get_client_ip(request) or None


class _CsrfCheck(CsrfViewMiddleware):
    def _reject(self, request, reason):
        return reason


class ExpiringCookieTokenAuthentication(TokenAuthentication):
    """Authenticate API clients by header or by a scoped HttpOnly cookie."""

    def authenticate(self, request):
        header = get_authorization_header(request)
        cookie_token = request.COOKIES.get(AUTH_COOKIE_NAME, "").strip()
        if header:
            result = super().authenticate(request)
        elif cookie_token:
            result = self.authenticate_credentials(cookie_token)
            self._enforce_csrf(request)
        else:
            return None

        user, token = result
        if cookie_token and user.username != FREE_ACCOUNT_USERNAME:
            device_session = get_device_session(request, user)
            if not device_session:
                raise AuthenticationFailed("当前设备登录已失效，请重新登录")

        expires_at = token.created + timedelta(seconds=settings.API_TOKEN_TTL_SECONDS)
        expired_account = user.expired_date and user.expired_date <= timezone.localdate()
        if expired_account:
            from app.billing.services import has_managed_subscription

            expired_account = not has_managed_subscription(user)
        if timezone.now() >= expires_at or not user.is_active or expired_account:
            type(token).objects.filter(user=user).delete()
            try:
                from app.utils import req_gateway

                subjects = revoke_all_device_sessions(user)
                for subject in subjects or [user.username]:
                    req_gateway("post", "/api/logout", json={"user_name": subject})
            except Exception:
                pass
            raise AuthenticationFailed("登录已过期，请重新登录")
        return user, token

    @staticmethod
    def _enforce_csrf(request):
        check = _CsrfCheck(lambda _request: None)
        check.process_request(request)
        reason = check.process_view(request, None, (), {})
        if reason:
            raise PermissionDenied(f"CSRF 验证失败: {reason}")


def set_auth_cookie(response, token, device_token=None, device_id=None):
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token.key,
        max_age=settings.API_TOKEN_TTL_SECONDS,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite="Strict",
        path="/0x/",
    )
    if device_token:
        response.set_cookie(DEVICE_COOKIE_NAME, device_token, **_device_cookie_options())
    if device_id:
        set_device_id_cookie(response, device_id)


def set_device_id_cookie(response, device_id):
    response.set_cookie(
        DEVICE_ID_COOKIE_NAME,
        device_id,
        max_age=365 * 24 * 60 * 60,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite="Strict",
        path="/",
    )


def clear_auth_cookie(response):
    response.delete_cookie(AUTH_COOKIE_NAME, path="/0x/", samesite="Strict")
    response.delete_cookie(DEVICE_COOKIE_NAME, path="/0x/", samesite="Strict")

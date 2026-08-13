import time
import hashlib

import requests
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.middleware.csrf import get_token, rotate_token
from django.utils import timezone
from requests.exceptions import RequestException
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from app.accounts.models import EmailDeliveryStatus, EmailVerificationChallenge, User
from app.accounts.authentication import (
    active_device_sessions,
    clear_auth_cookie,
    device_identity,
    device_subject,
    ensure_device_capacity,
    get_device_session,
    issue_device_session,
    registered_device_session,
    revoke_all_device_sessions,
    revoke_device_session,
    set_auth_cookie,
    set_device_id_cookie,
)
from app.accounts.captcha import issue_local_captcha, verify_local_captcha
from app.accounts.email_auth import (
    EmailVerificationPurpose,
    consume_verification_challenge,
    create_verification_challenge,
    issue_binding_ticket,
    issue_device_login_ticket,
    load_device_login_ticket,
    load_binding_ticket,
    needs_email_binding,
    normalize_email,
)
from app.accounts.serializers import (
    DeviceLoginVerificationConfirmSerializer,
    DeviceLoginVerificationRequestSerializer,
    EmailBindingConfirmSerializer,
    EmailBindingRequestSerializer,
    EmailChangeConfirmSerializer,
    EmailChangeRequestSerializer,
    EmailVerificationRequestSerializer,
    PasswordResetConfirmSerializer,
    UserRegisterSerializer,
)
from app.accounts.device_policy import effective_device_policy
from app.chatgpt.models import ChatgptAccount
from app.settings import ADMIN_USERNAME, FREE_ACCOUNT_USERNAME
from app.settings import (
    ALLOW_REGISTER,
    LOCAL_CAPTCHA_ENABLED,
    TURNSTILE_ENABLED,
    TURNSTILE_SECRET_KEY,
)
from app.utils import get_client_ip, get_request_subject, issue_free_session, save_visit_log, req_gateway


TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def mask_email(value):
    local, _, domain = str(value or "").partition("@")
    if not local or not domain:
        return ""
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}{'*' * max(2, len(local) - len(visible))}@{domain}"


def verify_turnstile(request, expected_action):
    if LOCAL_CAPTCHA_ENABLED:
        token = request.data.get("captcha_token")
        answer = request.data.get("captcha_answer")
        verify_local_captcha(token, answer, expected_action)
        return

    if not TURNSTILE_ENABLED:
        return

    token = str(request.data.get("turnstile_token") or "").strip()
    if not token:
        raise ValidationError({"message": "请完成人机验证"})

    try:
        response = requests.post(
            TURNSTILE_VERIFY_URL,
            data={
                "secret": TURNSTILE_SECRET_KEY,
                "response": token,
                "remoteip": get_client_ip(request),
            },
            timeout=5,
        )
        result = response.json()
    except (RequestException, ValueError):
        raise ValidationError({"message": "人机验证服务暂时不可用，请稍后重试"})

    if not result.get("success") or result.get("action") != expected_action:
        raise ValidationError({"message": "人机验证无效或已过期，请重新验证"})


class LoginIpRateThrottle(SimpleRateThrottle):
    scope = "login_ip"

    def get_cache_key(self, request, view):
        digest = hashlib.sha256(get_client_ip(request).encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}


class LoginAccountRateThrottle(SimpleRateThrottle):
    scope = "login_account"

    def get_cache_key(self, request, view):
        identifier = str(
            request.data.get("identifier")
            or request.data.get("email")
            or request.data.get("username")
            or ""
        ).strip().lower()
        if not identifier:
            return None
        digest = hashlib.sha256(identifier.encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}


class EmailVerificationIpRateThrottle(SimpleRateThrottle):
    scope = "email_verification_ip"

    def get_cache_key(self, request, view):
        digest = hashlib.sha256(get_client_ip(request).encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}


class EmailVerificationAddressRateThrottle(SimpleRateThrottle):
    scope = "email_verification_address"

    def get_cache_key(self, request, view):
        email = str(request.data.get("email") or "").strip().lower()
        if not email:
            return None
        digest = hashlib.sha256(email.encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}


class EmailVerificationAttemptRateThrottle(SimpleRateThrottle):
    scope = "email_verification_attempt"

    def get_cache_key(self, request, view):
        digest = hashlib.sha256(get_client_ip(request).encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}


class LocalCaptchaIssueRateThrottle(SimpleRateThrottle):
    scope = "captcha_issue_ip"

    def get_cache_key(self, request, view):
        digest = hashlib.sha256(get_client_ip(request).encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}


class LocalCaptchaView(APIView):
    authentication_classes = ()
    permission_classes = ()
    throttle_classes = (LocalCaptchaIssueRateThrottle,)

    def get(self, request):
        if not LOCAL_CAPTCHA_ENABLED:
            raise ValidationError({"message": "本地验证码未启用"})
        action = str(request.query_params.get("action") or "").strip()
        if action not in {"login", "register", "password_reset", "bind_email"}:
            raise ValidationError({"message": "验证码用途无效"})
        return Response(issue_local_captcha(action))


def issue_user_token(user, *, rotate=False):
    if rotate:
        Token.objects.filter(user=user).delete()
        return Token.objects.create(user=user)
    token, _ = Token.objects.get_or_create(user=user)
    return token


def _authenticated_response(request, user, *, device_verified=False):
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        token = issue_user_token(user)
        device_token, device_id, _device_session, revoked_subjects = issue_device_session(
            request,
            user,
            verified=device_verified,
        )

    for subject in revoked_subjects:
        try:
            req_gateway("post", "/api/logout", json={"user_name": subject})
        except ValidationError:
            pass
    request.user = user
    save_visit_log(request, "login")
    rotate_token(request)
    result = {
        "authenticated": True,
        "username": user.username,
        "email": user.email,
        "email_verified": bool(user.email and user.email_verified_at),
        "csrf_token": get_token(request),
    }
    if user.is_staff or user.is_superuser:
        result["is_admin"] = True
    response = Response(result)
    set_auth_cookie(response, token, device_token, device_id)
    return response


def _find_login_user(identifier):
    value = str(identifier or "").strip()
    if not value:
        return None
    if "@" in value:
        user = User.objects.filter(email__iexact=value).first()
        if user:
            return user
        # A verified-email account must not keep accepting its former email-like
        # username after an administrator changes the bound address.
        legacy_user = User.objects.filter(username__iexact=value).first()
        if legacy_user and not legacy_user.email:
            return legacy_user
        return None
    return User.objects.filter(username__iexact=value).first()


def _revoke_user_tokens(user):
    Token.objects.filter(user=user).delete()
    subjects = revoke_all_device_sessions(user)
    for subject in subjects or [user.username]:
        try:
            req_gateway("post", "/api/logout", json={"user_name": subject})
        except ValidationError:
            pass


class UserFreeLoginView(APIView):
    authentication_classes = ()
    throttle_classes = (LoginIpRateThrottle,)

    def post(self, request):
        verify_turnstile(request, "login")
        user = User.objects.filter(username=FREE_ACCOUNT_USERNAME, is_active=True).first()
        if not user:
            raise ValidationError({"message": "当前系统无免费账号可用"})
        request.user = user

        token = issue_user_token(user)
        save_visit_log(request, "login")

        rotate_token(request)
        response = Response({
            "authenticated": True,
            "is_admin": False,
            "username": user.username,
            "csrf_token": get_token(request),
        })
        set_auth_cookie(response, token)
        response.set_cookie(
            "free_session",
            issue_free_session(),
            max_age=7 * 24 * 60 * 60,
            httponly=True,
            secure=settings.SESSION_COOKIE_SECURE,
            samesite="Strict",
            path="/",
        )
        return response


class AccountLogin(APIView):
    authentication_classes = ()
    throttle_classes = (LoginIpRateThrottle, LoginAccountRateThrottle)

    def post(self, request):
        verify_turnstile(request, "login")
        identifier = str(
            request.data.get("identifier")
            or request.data.get("email")
            or request.data.get("username")
            or ""
        ).strip()
        password = str(request.data.get("password") or "")
        user = _find_login_user(identifier)
        if not user or not password or not user.is_active or not user.check_password(password):
            raise ValidationError({"message": "邮箱（旧账号可使用用户名）或密码不正确"})
        from app.billing.services import has_managed_subscription

        if (
            user.expired_date
            and user.expired_date <= timezone.now().date()
            and not has_managed_subscription(user)
        ):
            raise ValidationError({"message": "账号已过期"})

        if needs_email_binding(user):
            return Response({
                "authenticated": False,
                "email_binding_required": True,
                "binding_ticket": issue_binding_ticket(user),
                "username": user.username,
                "message": "请先完成邮箱验证后继续使用",
            })
        policy = effective_device_policy(user)
        ensure_device_capacity(request, user, policy)
        registered = registered_device_session(request, user)
        requires_verification = (
            policy["new_device_verification_enabled"]
            and not (user.is_staff or user.is_superuser)
            and (not registered or not registered.verified_at)
        )
        if requires_verification:
            device_id, device_key_hash = device_identity(request)
            response = Response({
                "authenticated": False,
                "device_verification_required": True,
                "device_ticket": issue_device_login_ticket(user, device_key_hash),
                "masked_email": mask_email(user.email),
                "message": "检测到新设备，请完成邮箱验证",
            })
            set_device_id_cookie(response, device_id)
            return response
        return _authenticated_response(request, user)


class AccountLogout(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        user_name = get_request_subject(request)
        try:
            req_gateway("post", "/api/logout", json={"user_name": user_name})
        except ValidationError:
            pass

        if request.user.username != FREE_ACCOUNT_USERNAME:
            revoked_session = revoke_device_session(request, request.user)
            if request.auth and (
                not revoked_session or not active_device_sessions(request.user).exists()
            ):
                Token.objects.filter(key=str(request.auth)).delete()

        response = Response({"message": "退出成功"})
        response.delete_cookie("free_session", path="/", samesite="Strict")
        clear_auth_cookie(response)
        return response


class DeviceLoginVerificationRequestView(APIView):
    authentication_classes = ()
    throttle_classes = (EmailVerificationIpRateThrottle, EmailVerificationAddressRateThrottle)

    def post(self, request):
        serializer = DeviceLoginVerificationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user, expected_device_key_hash = load_device_login_ticket(
            serializer.validated_data["device_ticket"]
        )
        _device_id, current_device_key_hash = device_identity(request)
        if current_device_key_hash != expected_device_key_hash:
            raise ValidationError({"message": "新设备验证会话无效，请重新登录"})
        policy = effective_device_policy(user)
        ensure_device_capacity(request, user, policy)
        challenge = create_verification_challenge(
            email=user.email,
            purpose=EmailVerificationPurpose.DEVICE_LOGIN,
            user=user,
            ip_address=get_client_ip(request),
        )
        return Response(
            {
                "message": "验证码正在发送，请查收邮箱",
                "challenge_id": str(challenge.challenge_id),
                "delivery_status": challenge.delivery_status,
                "masked_email": mask_email(user.email),
            },
            status=202,
        )


class DeviceLoginVerificationConfirmView(APIView):
    authentication_classes = ()
    throttle_classes = (EmailVerificationAttemptRateThrottle,)

    def post(self, request):
        serializer = DeviceLoginVerificationConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user, expected_device_key_hash = load_device_login_ticket(
            serializer.validated_data["device_ticket"]
        )
        _device_id, current_device_key_hash = device_identity(request)
        if current_device_key_hash != expected_device_key_hash:
            raise ValidationError({"message": "新设备验证会话无效，请重新登录"})
        policy = effective_device_policy(user)
        ensure_device_capacity(request, user, policy)
        consume_verification_challenge(
            purpose=EmailVerificationPurpose.DEVICE_LOGIN,
            user=user,
            email=user.email,
            code=serializer.validated_data["verification_code"],
        )
        return _authenticated_response(request, user, device_verified=True)


class AccountRegister(APIView):
    authentication_classes = ()
    throttle_classes = (EmailVerificationAttemptRateThrottle,)

    def post(self, request, *args, **kwargs):
        if not ALLOW_REGISTER:
            raise ValidationError({"message": "当前系统禁止注册账号"})

        serializer = UserRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        email = data["email"]
        if email == ADMIN_USERNAME:
            raise ValidationError({"message": "该用户名不可注册"})
        if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username__iexact=email).exists():
            raise ValidationError({"message": "该邮箱已注册"})

        consume_verification_challenge(
            purpose=EmailVerificationPurpose.REGISTER,
            email=email,
            code=data["verification_code"],
        )
        with transaction.atomic():
            if settings.BILLING_ENABLED:
                try:
                    user = User.objects.create_user(
                        username=email,
                        email=email,
                        email_verified_at=timezone.now(),
                        password=data["password"],
                        gptcar_list=[],
                    )
                except IntegrityError as exc:
                    raise ValidationError({"email": "该邮箱已注册"}) from exc
            else:
                res_json = req_gateway("post", "/api/get-user-info", json={
                    "chatgpt_token": data["chatgpt_token"],
                })
                from app.chatgpt.models import ChatgptCar

                chatgptaccount_id = ChatgptAccount.save_data(res_json)
                chatgptcar = ChatgptCar.objects.create(
                    car_name=f"reg_{email}",
                    gpt_account_list=[chatgptaccount_id],
                    created_time=int(time.time()),
                    updated_time=int(time.time()),
                    remark="用户注册时，系统自动创建",
                )
                user = User.objects.create_user(
                    username=email,
                    email=email,
                    email_verified_at=timezone.now(),
                    password=data["password"],
                    gptcar_list=[chatgptcar.id],
                )
        return _authenticated_response(request, user, device_verified=True)


class EmailVerificationRequestView(APIView):
    authentication_classes = ()
    throttle_classes = (EmailVerificationIpRateThrottle, EmailVerificationAddressRateThrottle)

    def post(self, request):
        verify_turnstile(request, "register")
        if not ALLOW_REGISTER:
            raise ValidationError({"message": "当前系统禁止注册账号"})
        serializer = EmailVerificationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username__iexact=email).exists():
            raise ValidationError({"email": "该邮箱已注册"})
        challenge = create_verification_challenge(
            email=email,
            purpose=EmailVerificationPurpose.REGISTER,
            ip_address=get_client_ip(request),
        )
        return Response(
            {
                "message": "验证码正在发送，请查收邮箱",
                "challenge_id": str(challenge.challenge_id),
                "delivery_status": challenge.delivery_status,
            },
            status=202,
        )


class PasswordResetRequestView(APIView):
    authentication_classes = ()
    throttle_classes = (EmailVerificationIpRateThrottle, EmailVerificationAddressRateThrottle)

    def post(self, request):
        verify_turnstile(request, "password_reset")
        serializer = EmailVerificationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        user = User.objects.filter(
            email__iexact=email,
            email_verified_at__isnull=False,
            is_active=True,
        ).first()
        if user:
            create_verification_challenge(
                email=email,
                purpose=EmailVerificationPurpose.PASSWORD_RESET,
                user=user,
                ip_address=get_client_ip(request),
            )
        return Response({"message": "如该邮箱已注册，验证码已发送，请查收邮箱"})


class PasswordResetConfirmView(APIView):
    authentication_classes = ()
    throttle_classes = (EmailVerificationAttemptRateThrottle,)

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = User.objects.filter(
            email__iexact=data["email"],
            email_verified_at__isnull=False,
            is_active=True,
        ).first()
        if not user:
            raise ValidationError({"code": "验证码无效或已过期"})
        consume_verification_challenge(
            purpose=EmailVerificationPurpose.PASSWORD_RESET,
            email=data["email"],
            user=user,
            code=data["verification_code"],
        )
        with transaction.atomic():
            user.set_password(data["new_password"])
            user.save(update_fields=["password"])
            _revoke_user_tokens(user)
        return Response({"message": "密码已重置，请使用新密码登录"})


class EmailBindingRequestView(APIView):
    authentication_classes = ()
    throttle_classes = (EmailVerificationIpRateThrottle, EmailVerificationAddressRateThrottle)

    def post(self, request):
        verify_turnstile(request, "bind_email")
        serializer = EmailBindingRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = load_binding_ticket(data["binding_ticket"])
        email = data["email"]
        if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
            raise ValidationError({"email": "该邮箱已被其他账户使用"})
        challenge = create_verification_challenge(
            email=email,
            purpose=EmailVerificationPurpose.EMAIL_BINDING,
            user=user,
            ip_address=get_client_ip(request),
        )
        return Response(
            {
                "message": "验证码正在发送，请查收邮箱",
                "challenge_id": str(challenge.challenge_id),
                "delivery_status": challenge.delivery_status,
            },
            status=202,
        )


class EmailBindingConfirmView(APIView):
    authentication_classes = ()
    throttle_classes = (EmailVerificationAttemptRateThrottle,)

    def post(self, request):
        serializer = EmailBindingConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = load_binding_ticket(data["binding_ticket"])
        challenge = consume_verification_challenge(
            purpose=EmailVerificationPurpose.EMAIL_BINDING,
            user=user,
            code=data["verification_code"],
        )
        with transaction.atomic():
            if User.objects.filter(email__iexact=challenge.email).exclude(pk=user.pk).exists():
                raise ValidationError({"email": "该邮箱已被其他账户使用"})
            user.email = challenge.email
            user.email_verified_at = timezone.now()
            try:
                user.save(update_fields=["email", "email_verified_at"])
            except IntegrityError as exc:
                raise ValidationError({"email": "该邮箱已被其他账户使用"}) from exc
        return _authenticated_response(request, user, device_verified=True)


class EmailVerificationStatusView(APIView):
    authentication_classes = ()
    permission_classes = ()

    def get(self, request, challenge_id):
        challenge = EmailVerificationChallenge.objects.filter(challenge_id=challenge_id).first()
        if not challenge:
            raise ValidationError({"message": "验证码发送状态不存在"})

        delivery_status = challenge.delivery_status
        if challenge.invalidated_at and delivery_status != EmailDeliveryStatus.SENT:
            delivery_status = EmailDeliveryStatus.FAILED
        return Response({"delivery_status": delivery_status})


class EmailChangeRequestView(APIView):
    permission_classes = (IsAuthenticated,)

    def get_throttles(self):
        return [EmailVerificationIpRateThrottle(), EmailVerificationAddressRateThrottle()]

    def post(self, request):
        serializer = EmailChangeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not request.user.check_password(data["current_password"]):
            raise ValidationError({"current_password": "当前密码不正确"})

        email = data["email"]
        if email == request.user.email:
            raise ValidationError({"email": "新邮箱与当前绑定邮箱一致"})
        if User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).exclude(pk=request.user.pk).exists():
            raise ValidationError({"email": "该邮箱已被其他账户使用"})

        challenge = create_verification_challenge(
            email=email,
            purpose=EmailVerificationPurpose.EMAIL_CHANGE,
            user=request.user,
            ip_address=get_client_ip(request),
        )
        return Response(
            {
                "message": "验证码正在发送，请查收新邮箱",
                "challenge_id": str(challenge.challenge_id),
                "delivery_status": challenge.delivery_status,
            },
            status=202,
        )


class EmailChangeConfirmView(APIView):
    permission_classes = (IsAuthenticated,)
    throttle_classes = (EmailVerificationAttemptRateThrottle,)

    def post(self, request):
        serializer = EmailChangeConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        challenge = consume_verification_challenge(
            purpose=EmailVerificationPurpose.EMAIL_CHANGE,
            user=request.user,
            code=serializer.validated_data["verification_code"],
        )

        with transaction.atomic():
            if User.objects.filter(
                Q(email__iexact=challenge.email) | Q(username__iexact=challenge.email)
            ).exclude(pk=request.user.pk).exists():
                raise ValidationError({"email": "该邮箱已被其他账户使用"})
            request.user.email = challenge.email
            request.user.email_verified_at = timezone.now()
            try:
                request.user.save(update_fields=["email", "email_verified_at"])
            except IntegrityError as exc:
                raise ValidationError({"email": "该邮箱已被其他账户使用"}) from exc

            from app.billing.services import audit

            audit(
                "user.email_changed_self",
                request.user,
                actor=request.user,
                detail={"verification_reset": False},
                ip_address=get_client_ip(request),
            )

        _revoke_user_tokens(request.user)
        return _authenticated_response(request, request.user, device_verified=True)

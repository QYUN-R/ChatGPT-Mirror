from unittest.mock import Mock, patch
from datetime import timedelta
import json
import random

from celery.exceptions import Retry
from django.core.cache import cache
from django.db import connection
from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.test.utils import override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from app.accounts.models import (
    EmailVerificationChallenge,
    EmailVerificationPurpose,
    User,
    UserDeviceSession,
    VisitLog,
)
from app.accounts.views import BatchUserActionView, UserAccountView, VisitLogView, ChangePasswordView
from app.accounts.authentication import (
    AUTH_COOKIE_NAME,
    DEVICE_COOKIE_NAME,
    DEVICE_ID_COOKIE_NAME,
    ExpiringCookieTokenAuthentication,
)
from app.accounts.device_policy import effective_device_policy
from app.accounts.model_limits import normalize_model_limits
from app.utils import get_request_subject
from app.accounts.captcha import (
    CAPTCHA_ALPHABET,
    CAPTCHA_LENGTH,
    _challenge_cache_key,
    _glyph_layout,
    inspect_local_captcha_token,
    issue_local_captcha,
    verify_local_captcha,
)
from app.accounts.views.cfg import AccessControlView
from app.accounts.views.login import (
    AccountLogin,
    AccountLogout,
    AccountRegister,
    EmailBindingConfirmView,
    EmailChangeConfirmView,
    DeviceLoginVerificationConfirmView,
    PasswordResetConfirmView,
    UserFreeLoginView,
    LocalCaptchaView,
    verify_turnstile,
)
from app.chatgpt.models import ChatgptAccount
from app.chatgpt.serializers import ShowChatgptTokenSerializer
from app.settings import ADMIN_USERNAME, FREE_ACCOUNT_USERNAME
from app.utils import get_client_ip, req_gateway


class SecurityRegressionTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def test_force_chat_mode_is_enabled_for_new_users(self):
        user = User.objects.create_user(username="work-mode-user", password="Strong-password-123!")
        self.assertTrue(user.force_chat_mode)

    def test_model_limits_drop_object_values_and_legacy_placeholders(self):
        self.assertEqual(
            normalize_model_limits([
                " gpt-5 ",
                {"value": "gpt-4o"},
                "[object Object]",
                "gpt-5",
                "",
                "gpt-4o",
            ]),
            ["gpt-5", "gpt-4o"],
        )

    def test_user_serializer_masks_invalid_legacy_model_limits(self):
        from app.accounts.serializers import ShowUserAccountModelSerializer

        user = User.objects.create_user(username="legacy-model-user", password="Strong-password-123!")
        user.model_limit = ["[object Object]", {"label": "GPT"}, "gpt-5"]
        user.save(update_fields=["model_limit"])

        self.assertEqual(ShowUserAccountModelSerializer(user).data["model_limit"], ["gpt-5"])

    def test_user_update_rejects_object_model_limit_values(self):
        admin = User.objects.create_superuser(username="model-limit-admin", password="Strong-password-123!")
        user = User.objects.create_user(username="model-limit-user", password="Strong-password-123!")
        request = self.factory.post(
            "/0x/user",
            {
                "username": user.username,
                "email": "",
                "is_active": True,
                "isolated_session": True,
                "gptcar_list": [],
                "model_limit": [{"value": "gpt-5"}],
                "remark": "",
                "daily_quota": 0,
                "monthly_quota": 0,
            },
            format="json",
        )
        force_authenticate(request, user=admin)

        response = UserAccountView.as_view()(request)

        self.assertEqual(response.status_code, 400)

    def test_empty_account_pool_is_fail_closed(self):
        self.assertFalse(ChatgptAccount.get_by_gptcar_list([]).exists())

    def test_client_ip_prefers_gateway_forwarded_address(self):
        request = self.factory.get(
            "/0x/user/visit-log",
            HTTP_X_CHATGPT_MIRROR_CLIENT_IP="203.0.113.9",
            HTTP_X_FORWARDED_FOR="172.18.0.1",
            REMOTE_ADDR="172.18.0.2",
        )
        self.assertEqual(get_client_ip(request), "203.0.113.9")

    def test_client_ip_ignores_invalid_values_and_uses_remote_address(self):
        request = self.factory.get(
            "/0x/user/visit-log",
            HTTP_X_CHATGPT_MIRROR_CLIENT_IP="invalid",
            REMOTE_ADDR="2001:db8::9",
        )
        self.assertEqual(get_client_ip(request), "2001:db8::9")

    def test_access_control_requires_admin(self):
        user = User.objects.create_user(username="normal-user", password="password-123")
        request = self.factory.post("/0x/user/access-control", {"hash_paths": []}, format="json")
        force_authenticate(request, user=user)
        response = AccessControlView.as_view()(request)
        self.assertEqual(response.status_code, 403)

    @patch("app.accounts.views.cfg.req_gateway", return_value={"paths": ["/pricing"]})
    def test_access_control_forwards_custom_paths(self, req_gateway):
        admin = User.objects.create_superuser(username="path-admin", password="password-123")
        request = self.factory.post(
            "/0x/user/access-control", {"paths": ["pricing"]}, format="json"
        )
        force_authenticate(request, user=admin)
        response = AccessControlView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        req_gateway.assert_called_once_with(
            "post", "/api/blocked-paths", json={"paths": ["pricing"]}
        )

    @patch("app.accounts.views.login.req_gateway", return_value={"message": "退出成功"})
    def test_logout_revokes_drf_and_gateway_sessions(self, req_gateway):
        user = User.objects.create_user(username="logout-user", password="password-123")
        token = Token.objects.create(user=user)
        request = self.factory.post("/0x/user/logout", {}, format="json")
        force_authenticate(request, user=user, token=token)
        response = AccountLogout.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Token.objects.filter(key=token.key).exists())
        req_gateway.assert_called_once_with(
            "post",
            "/api/logout",
            json={"user_name": "logout-user"},
        )

    def test_stale_auth_cookie_does_not_block_public_login_with_csrf_403(self):
        user = User.objects.create_user(username="stale-login", password="Strong-password-123!")
        token = Token.objects.create(user=user)
        request = self.factory.post(
            "/0x/user/login",
            {"username": "stale-login", "password": "wrong-password"},
            format="json",
            HTTP_COOKIE=f"{AUTH_COOKIE_NAME}={token.key}",
        )

        response = AccountLogin.as_view()(request)

        self.assertEqual(response.status_code, 400)

    @patch("app.accounts.views.login.LOCAL_CAPTCHA_ENABLED", False)
    @patch("app.accounts.views.login.TURNSTILE_ENABLED", False)
    @patch("app.accounts.views.login.req_gateway", return_value={"message": "ok"})
    def test_single_device_login_revokes_previous_device(self, _req_gateway):
        user = User.objects.create_user(
            username="single-device-user",
            email="single-device-user@qq.com",
            email_verified_at=timezone.now(),
            password="Strong-password-123!",
            device_policy_managed_by_plan=False,
            multi_device_enabled=False,
            new_device_verification_enabled=False,
        )
        first_client = APIClient()
        second_client = APIClient()

        first_login = first_client.post(
            "/0x/user/login",
            {"identifier": user.email, "password": "Strong-password-123!"},
            format="json",
        )
        second_login = second_client.post(
            "/0x/user/login",
            {"identifier": user.email, "password": "Strong-password-123!"},
            format="json",
        )

        self.assertEqual(first_login.status_code, 200)
        self.assertEqual(second_login.status_code, 200)
        self.assertNotEqual(
            first_login.cookies[DEVICE_COOKIE_NAME].value,
            second_login.cookies[DEVICE_COOKIE_NAME].value,
        )
        self.assertEqual(first_client.get("/0x/user/me").status_code, 401)
        self.assertEqual(second_client.get("/0x/user/me").status_code, 200)
        self.assertEqual(
            UserDeviceSession.objects.filter(user=user, revoked_at__isnull=True).count(),
            1,
        )

    @patch("app.accounts.views.login.LOCAL_CAPTCHA_ENABLED", False)
    @patch("app.accounts.views.login.TURNSTILE_ENABLED", False)
    @patch("app.accounts.views.login.req_gateway", return_value={"message": "ok"})
    def test_multi_device_login_keeps_both_devices_and_uses_distinct_subjects(self, _req_gateway):
        user = User.objects.create_user(
            username="multi-device-user",
            email="multi-device-user@qq.com",
            email_verified_at=timezone.now(),
            password="Strong-password-123!",
            device_policy_managed_by_plan=False,
            multi_device_enabled=True,
            new_device_verification_enabled=False,
        )
        first_client = APIClient()
        second_client = APIClient()

        first_login = first_client.post(
            "/0x/user/login",
            {"identifier": user.email, "password": "Strong-password-123!"},
            format="json",
        )
        second_login = second_client.post(
            "/0x/user/login",
            {"identifier": user.email, "password": "Strong-password-123!"},
            format="json",
        )

        self.assertEqual(first_login.status_code, 200)
        self.assertEqual(second_login.status_code, 200)
        self.assertEqual(first_client.get("/0x/user/me").status_code, 200)
        self.assertEqual(second_client.get("/0x/user/me").status_code, 200)
        sessions = list(
            UserDeviceSession.objects.filter(user=user, revoked_at__isnull=True)
        )
        self.assertEqual(len(sessions), 2)
        self.assertNotEqual(sessions[0].subject_id, sessions[1].subject_id)

        first_request = self.factory.get(
            "/0x/user/get-mirror-token",
            HTTP_COOKIE=f"{DEVICE_COOKIE_NAME}={first_client.cookies[DEVICE_COOKIE_NAME].value}",
        )
        first_request.user = user
        second_request = self.factory.get(
            "/0x/user/get-mirror-token",
            HTTP_COOKIE=f"{DEVICE_COOKIE_NAME}={second_client.cookies[DEVICE_COOKIE_NAME].value}",
        )
        second_request.user = user
        self.assertNotEqual(
            get_request_subject(first_request),
            get_request_subject(second_request),
        )

    @patch("app.accounts.views.login.LOCAL_CAPTCHA_ENABLED", False)
    @patch("app.accounts.views.login.TURNSTILE_ENABLED", False)
    @patch("app.accounts.views.login.req_gateway", return_value={"message": "ok"})
    def test_multi_device_logout_only_revokes_current_device(self, _req_gateway):
        user = User.objects.create_user(
            username="multi-device-logout",
            email="multi-device-logout@qq.com",
            email_verified_at=timezone.now(),
            password="Strong-password-123!",
            device_policy_managed_by_plan=False,
            multi_device_enabled=True,
            new_device_verification_enabled=False,
        )
        first_client = APIClient()
        second_client = APIClient()
        credentials = {
            "identifier": user.email,
            "password": "Strong-password-123!",
        }
        self.assertEqual(first_client.post("/0x/user/login", credentials, format="json").status_code, 200)
        self.assertEqual(second_client.post("/0x/user/login", credentials, format="json").status_code, 200)

        self.assertEqual(first_client.post("/0x/user/logout", {}, format="json").status_code, 200)
        self.assertEqual(first_client.get("/0x/user/me").status_code, 401)
        self.assertEqual(second_client.get("/0x/user/me").status_code, 200)
        self.assertEqual(
            UserDeviceSession.objects.filter(user=user, revoked_at__isnull=True).count(),
            1,
        )

    @patch("app.accounts.views.login.LOCAL_CAPTCHA_ENABLED", False)
    @patch("app.accounts.views.login.TURNSTILE_ENABLED", False)
    @patch("app.accounts.views.login.req_gateway", return_value={"message": "ok"})
    def test_same_browser_reuses_stable_device_identity_after_logout(self, _req_gateway):
        user = User.objects.create_user(
            username="stable-device-user",
            email="stable-device-user@qq.com",
            email_verified_at=timezone.now(),
            password="Strong-password-123!",
            device_policy_managed_by_plan=False,
            multi_device_enabled=True,
            new_device_verification_enabled=False,
        )
        client = APIClient()
        credentials = {"identifier": user.email, "password": "Strong-password-123!"}
        first = client.post("/0x/user/login", credentials, format="json")
        self.assertEqual(first.status_code, 200)
        device_id = client.cookies[DEVICE_ID_COOKIE_NAME].value
        first_session = UserDeviceSession.objects.get(user=user)

        self.assertEqual(client.post("/0x/user/logout", {}, format="json").status_code, 200)
        self.assertEqual(client.cookies[DEVICE_ID_COOKIE_NAME].value, device_id)
        second = client.post("/0x/user/login", credentials, format="json")

        self.assertEqual(second.status_code, 200)
        self.assertEqual(UserDeviceSession.objects.filter(user=user).count(), 1)
        first_session.refresh_from_db()
        self.assertIsNone(first_session.revoked_at)

    @patch("app.accounts.views.login.LOCAL_CAPTCHA_ENABLED", False)
    @patch("app.accounts.views.login.TURNSTILE_ENABLED", False)
    def test_new_device_requires_email_verification(self):
        user = User.objects.create_user(
            username="verify-device-user",
            email="verify-device-user@qq.com",
            email_verified_at=timezone.now(),
            password="Strong-password-123!",
            new_device_verification_enabled=True,
        )
        client = APIClient()
        login = client.post(
            "/0x/user/login",
            {"identifier": user.email, "password": "Strong-password-123!"},
            format="json",
        )
        self.assertEqual(login.status_code, 200)
        self.assertFalse(login.data["authenticated"])
        self.assertTrue(login.data["device_verification_required"])
        self.assertIn(DEVICE_ID_COOKIE_NAME, login.cookies)

    @override_settings(DEFAULT_MULTI_DEVICE_ENABLED=True, DEFAULT_DEVICE_LIMIT=3)
    def test_device_policy_user_override_and_restore_system_default(self):
        user = User.objects.create_user(username="policy-user", password="Strong-password-123!")
        self.assertEqual(effective_device_policy(user)["limit"], 3)
        self.assertEqual(effective_device_policy(user)["source"], "system")

        user.device_policy_managed_by_plan = False
        user.multi_device_enabled = True
        user.device_limit = 7
        user.save(update_fields=["device_policy_managed_by_plan", "multi_device_enabled", "device_limit"])
        self.assertEqual(effective_device_policy(user)["limit"], 7)
        self.assertEqual(effective_device_policy(user)["source"], "user")

    @override_settings(DEFAULT_MULTI_DEVICE_ENABLED=True, DEFAULT_DEVICE_LIMIT=3)
    def test_active_plan_policy_and_user_override_precedence(self):
        from app.billing.models import Plan, PoolTier, Subscription, SubscriptionStatus
        from app.chatgpt.models import ChatgptCar

        pool = ChatgptCar.objects.create(
            car_name="device-policy-pool",
            gpt_account_list=[],
            is_commercial=True,
            created_time=1,
            updated_time=1,
        )
        plan = Plan.objects.create(
            code="device-policy-plan",
            name="设备策略套餐",
            pool=pool,
            pool_tier=PoolTier.STANDARD,
            multi_device_enabled=True,
            device_limit=2,
        )
        user = User.objects.create_user(username="plan-policy-user", password="Strong-password-123!")
        Subscription.objects.create(
            user=user,
            plan=plan,
            status=SubscriptionStatus.ACTIVE,
            starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(days=30),
        )
        self.assertEqual(effective_device_policy(user)["limit"], 2)
        self.assertEqual(effective_device_policy(user)["source"], "plan")

        user.device_policy_managed_by_plan = False
        user.multi_device_enabled = True
        user.device_limit = 6
        user.save(update_fields=["device_policy_managed_by_plan", "multi_device_enabled", "device_limit"])
        self.assertEqual(effective_device_policy(user)["limit"], 6)
        self.assertEqual(effective_device_policy(user)["source"], "user")

    @patch("app.accounts.views.req_gateway", return_value={"message": "ok"})
    def test_admin_batch_device_override_and_restore_follow_plan(self, _req_gateway):
        admin = User.objects.create_superuser(username="device-batch-admin", password="Strong-password-123!")
        first = User.objects.create_user(username="device-batch-one", password="Strong-password-123!")
        second = User.objects.create_user(username="device-batch-two", password="Strong-password-123!")
        factory = APIRequestFactory()

        override_request = factory.post(
            "/0x/user/batch",
            {
                "user_id_list": [first.id, second.id],
                "action": "device_override",
                "multi_device_enabled": True,
                "device_limit": 5,
                "new_device_verification_enabled": False,
            },
            format="json",
        )
        force_authenticate(override_request, user=admin)
        response = BatchUserActionView.as_view()(override_request)
        self.assertEqual(response.status_code, 200)
        for user in (first, second):
            user.refresh_from_db()
            self.assertFalse(user.device_policy_managed_by_plan)
            self.assertEqual(user.device_limit, 5)
            self.assertFalse(user.new_device_verification_enabled)

        follow_request = factory.post(
            "/0x/user/batch",
            {
                "user_id_list": [first.id, second.id],
                "action": "device_follow_plan",
                "new_device_verification_enabled": True,
            },
            format="json",
        )
        force_authenticate(follow_request, user=admin)
        response = BatchUserActionView.as_view()(follow_request)
        self.assertEqual(response.status_code, 200)
        for user in (first, second):
            user.refresh_from_db()
            self.assertTrue(user.device_policy_managed_by_plan)
            self.assertTrue(user.new_device_verification_enabled)

    @patch("app.accounts.views.login.LOCAL_CAPTCHA_ENABLED", False)
    @patch("app.accounts.views.login.TURNSTILE_ENABLED", False)
    @patch("app.accounts.views.login.req_gateway", return_value={"message": "ok"})
    def test_default_device_limit_rejects_fourth_browser(self, _req_gateway):
        user = User.objects.create_user(
            username="device-limit-user",
            email="device-limit-user@qq.com",
            email_verified_at=timezone.now(),
            password="Strong-password-123!",
            new_device_verification_enabled=False,
        )
        credentials = {"identifier": user.email, "password": "Strong-password-123!"}
        clients = [APIClient() for _ in range(4)]
        for client in clients[:3]:
            self.assertEqual(client.post("/0x/user/login", credentials, format="json").status_code, 200)
        rejected = clients[3].post("/0x/user/login", credentials, format="json")
        self.assertNotEqual(rejected.status_code, 200)
        self.assertIn("3 台设备上限", str(rejected.data))

    @patch("app.accounts.views.login.req_gateway", return_value={"message": "ok"})
    @patch("app.accounts.views.login.LOCAL_CAPTCHA_ENABLED", False)
    @patch("app.accounts.views.login.TURNSTILE_ENABLED", False)
    def test_device_email_code_completes_login(self, _req_gateway):
        user = User.objects.create_user(
            username="device-confirm-user",
            email="device-confirm-user@qq.com",
            email_verified_at=timezone.now(),
            password="Strong-password-123!",
        )
        client = APIClient()
        login = client.post(
            "/0x/user/login",
            {"identifier": user.email, "password": "Strong-password-123!"},
            format="json",
        )
        EmailVerificationChallenge.objects.create(
            user=user,
            email=user.email,
            purpose=EmailVerificationPurpose.DEVICE_LOGIN,
            code_hash=make_password("123456"),
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        confirmed = client.post(
            "/0x/user/device-login/confirm",
            {"device_ticket": login.data["device_ticket"], "verification_code": "123456"},
            format="json",
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertTrue(confirmed.data["authenticated"])
        self.assertIn(DEVICE_COOKIE_NAME, confirmed.cookies)
        self.assertEqual(client.get("/0x/user/me").status_code, 200)

    def test_invalid_auth_cookie_does_not_block_public_version_config(self):
        client = APIClient()
        client.cookies[AUTH_COOKIE_NAME] = "invalid-token"

        response = client.get("/0x/user/version-cfg")

        self.assertEqual(response.status_code, 200)
        self.assertIn("allow_register", response.data)
        self.assertIn("billing_enabled", response.data)

    @override_settings(CSRF_TRUSTED_ORIGINS=["https://mirror.example"])
    @patch("app.accounts.views.login.LOCAL_CAPTCHA_ENABLED", False)
    @patch("app.accounts.views.login.TURNSTILE_ENABLED", False)
    def test_admin_login_issues_csrf_cookie_for_unsafe_api_requests(self):
        User.objects.create_superuser(username="csrf-admin", password="Strong-password-123!")
        client = APIClient(enforce_csrf_checks=True)

        login = client.post(
            "/0x/user/login",
            {"username": "csrf-admin", "password": "Strong-password-123!"},
            format="json",
            HTTP_USER_AGENT="security-regression-test",
            HTTP_ORIGIN="https://mirror.example",
            HTTP_X_FORWARDED_PROTO="https",
        )
        self.assertEqual(login.status_code, 200)
        self.assertIn("csrftoken", login.cookies)
        self.assertTrue(login.data["csrf_token"])

        rejected = client.post(
            "/0x/user/",
            {},
            format="json",
            HTTP_ORIGIN="https://mirror.example",
            HTTP_X_FORWARDED_PROTO="https",
        )
        self.assertEqual(rejected.status_code, 403)

        me = client.get("/0x/user/me")
        self.assertEqual(me.status_code, 200)
        self.assertTrue(me.data["csrf_token"])

        response = client.post(
            "/0x/user/",
            {},
            format="json",
            HTTP_X_CSRFTOKEN=me.data["csrf_token"],
            HTTP_ORIGIN="https://mirror.example",
            HTTP_REFERER="https://mirror.example/admin/",
            HTTP_X_FORWARDED_PROTO="https",
        )
        self.assertNotEqual(response.status_code, 403)

    @patch("app.accounts.views.login.LOCAL_CAPTCHA_ENABLED", False)
    @patch("app.accounts.views.login.save_visit_log")
    def test_free_login_keeps_shared_token_but_rotates_visitor_subject(self, _save_visit_log):
        User.objects.create_user(username=FREE_ACCOUNT_USERNAME, password="password-123")
        view = UserFreeLoginView.as_view()
        first = view(self.factory.post("/0x/user/login-free", {}, format="json"))
        second = view(self.factory.post("/0x/user/login-free", {}, format="json"))
        self.assertNotIn("admin_token", first.data)
        self.assertEqual(
            first.cookies[AUTH_COOKIE_NAME].value,
            second.cookies[AUTH_COOKIE_NAME].value,
        )
        self.assertNotEqual(
            first.cookies["free_session"].value,
            second.cookies["free_session"].value,
        )
        self.assertTrue(first.cookies["free_session"]["httponly"])
        self.assertTrue(first.cookies[AUTH_COOKIE_NAME]["httponly"])

    @override_settings(API_TOKEN_TTL_SECONDS=60)
    def test_expired_drf_token_is_rejected(self):
        user = User.objects.create_user(username="expired-token", password="password-123")
        token = Token.objects.create(user=user)
        Token.objects.filter(pk=token.pk).update(created=timezone.now() - timedelta(seconds=61))
        token.refresh_from_db()
        with patch("app.utils.req_gateway") as req_gateway:
            with self.assertRaises(Exception):
                request = self.factory.get(
                    "/0x/user/me",
                    HTTP_AUTHORIZATION=f"Token {token.key}",
                )
                ExpiringCookieTokenAuthentication().authenticate(request)
            req_gateway.assert_called_once_with(
                "post", "/api/logout", json={"user_name": user.username}
            )

    @patch("app.accounts.views.login.req_gateway")
    @patch("app.accounts.views.login.ALLOW_REGISTER", True)
    @patch("app.accounts.views.login.TURNSTILE_ENABLED", False)
    def test_registration_conflict_is_checked_before_upstream_write(self, req_gateway):
        User.objects.create_user(username="existing-user@qq.com", password="Strong-password-123!")
        request = self.factory.post(
            "/0x/user/register",
            {
                "email": "existing-user@qq.com",
                "password": "Another-strong-password-123!",
                "verification_code": "123456",
                "chatgpt_token": "upstream-secret",
            },
            format="json",
        )
        response = AccountRegister.as_view()(request)
        self.assertEqual(response.status_code, 400)
        req_gateway.assert_not_called()

    @override_settings(BILLING_ENABLED=True)
    @patch("app.accounts.views.login.ALLOW_REGISTER", True)
    @patch("app.accounts.views.login.req_gateway")
    @patch("app.accounts.views.login.TURNSTILE_ENABLED", False)
    def test_billing_registration_creates_portal_user_without_upstream_token(self, req_gateway):
        EmailVerificationChallenge.objects.create(
            email="new-billing-user@qq.com",
            purpose=EmailVerificationPurpose.REGISTER,
            code_hash=make_password("123456"),
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        request = self.factory.post(
            "/0x/user/register",
            {
                "email": "new-billing-user@qq.com",
                "password": "Strong-password-123!",
                "verification_code": "123456",
            },
            format="json",
        )
        response = AccountRegister.as_view()(request)
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(username="new-billing-user@qq.com")
        self.assertEqual(user.email, "new-billing-user@qq.com")
        self.assertIsNotNone(user.email_verified_at)
        req_gateway.assert_not_called()

    @patch("app.accounts.views.req_gateway", return_value={"message": "ok"})
    def test_password_change_revokes_old_token_and_issues_new_cookie(self, _req_gateway):
        user = User.objects.create_user(
            username="change-password-user",
            password="Old-strong-password-123!",
        )
        old_token = Token.objects.create(user=user)
        request = self.factory.post(
            "/0x/user/change-password",
            {
                "current_password": "Old-strong-password-123!",
                "new_password": "New-strong-password-456!",
            },
            format="json",
        )
        force_authenticate(request, user=user, token=old_token)
        response = ChangePasswordView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Token.objects.filter(key=old_token.key).exists())
        self.assertTrue(response.cookies[AUTH_COOKIE_NAME]["httponly"])

    def test_chatgpt_credentials_are_encrypted_at_rest(self):
        account = ChatgptAccount.objects.create(
            chatgpt_username="encrypted@example.com",
            plan_type="plus",
            access_token="plain-access-secret",
            session_token="plain-session-secret",
            extra_cookies=[{"name": "session", "value": "plain-cookie-secret"}],
            created_time=1,
            updated_time=1,
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT access_token, session_token, extra_cookies FROM chatgpt_chatgptaccount WHERE id = %s",
                [account.id],
            )
            stored = cursor.fetchone()
        self.assertTrue(stored[0].startswith("enc:v1:"))
        self.assertTrue(stored[1].startswith("enc:v1:"))
        self.assertTrue(stored[2].startswith("enc:v1:"))
        self.assertNotIn("plain", "".join(stored))

    def test_account_serializer_excludes_raw_credentials(self):
        account = ChatgptAccount.objects.create(
            chatgpt_username="shared@example.com",
            plan_type="plus",
            access_token="secret-access",
            session_token="secret-session",
            refresh_token="secret-refresh",
            refresh_client_id="secret-client",
            extra_cookies=[{"name": "secret", "value": "cookie"}],
            last_error="access_token=secret-access; cookie=secret-cookie-error",
            created_time=1,
            updated_time=1,
        )
        data = ShowChatgptTokenSerializer(account).data
        for field in (
            "access_token",
            "session_token",
            "refresh_token",
            "refresh_client_id",
            "extra_cookies",
        ):
            self.assertNotIn(field, data)
        self.assertNotIn("secret-access", data["last_error"])
        self.assertNotIn("secret-cookie-error", data["last_error"])

    @patch("app.chatgpt.views.chatgpt.req_gateway")
    def test_refresh_token_import_still_works_without_echoing_credentials(self, gateway):
        submitted_refresh = "submitted-refresh-secret-123456"
        imported_access = "imported-access-secret-123456"
        rotated_refresh = "rotated-refresh-secret-123456"
        gateway.side_effect = [
            {
                "user_info": {"email": "imported@example.com", "plan_type": "plus"},
                "access_token": imported_access,
                "refresh_token": rotated_refresh,
                "refresh_client_id": "app-test-client",
                "extra_cookies": [],
                "access_token_valid": True,
                "session_token_valid": False,
                "last_check_at": int(timezone.now().timestamp()),
            },
            {},
        ]
        admin = User.objects.create_superuser(
            username="credential-admin",
            password="Strong-password-123!",
        )
        client = APIClient()
        client.force_authenticate(admin)

        response = client.post(
            "/0x/chatgpt/",
            {
                "auth_type": "refresh_token",
                "client_id": "app-test-client",
                "refresh_token": submitted_refresh,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            gateway.call_args_list[0].kwargs["json"]["refresh_token"],
            submitted_refresh,
        )
        account = ChatgptAccount.objects.get(chatgpt_username="imported@example.com")
        self.assertEqual(account.access_token, imported_access)
        self.assertEqual(account.refresh_token, rotated_refresh)
        rendered = json.dumps(response.data, ensure_ascii=False)
        for secret in (submitted_refresh, imported_access, rotated_refresh):
            self.assertNotIn(secret, rendered)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT access_token, refresh_token FROM chatgpt_chatgptaccount WHERE id = %s",
                [account.id],
            )
            stored_access, stored_refresh = cursor.fetchone()
        self.assertTrue(stored_access.startswith("enc:v1:"))
        self.assertTrue(stored_refresh.startswith("enc:v1:"))
        self.assertNotIn(imported_access, stored_access)
        self.assertNotIn(rotated_refresh, stored_refresh)

    @patch("app.utils.requests.request")
    def test_gateway_errors_redact_submitted_credentials(self, request_mock):
        submitted_token = "submitted-token-secret-123456"
        submitted_cookie = "submitted-cookie-secret-123456"
        response = Mock(status_code=400, text="")
        response.json.return_value = {
            "message": (
                f"access_token={submitted_token}; "
                f"cookie={submitted_cookie}; Authorization: Bearer abcdefghijklmnop"
            )
        }
        request_mock.return_value = response

        with self.assertRaises(ValidationError) as captured:
            req_gateway(
                "post",
                "/api/get-user-info",
                json={
                    "chatgpt_token": submitted_token,
                    "cookie": submitted_cookie,
                },
            )

        rendered = json.dumps(captured.exception.detail, ensure_ascii=False)
        self.assertNotIn(submitted_token, rendered)
        self.assertNotIn(submitted_cookie, rendered)
        self.assertNotIn("abcdefghijklmnop", rendered)
        self.assertIn("[redacted]", rendered)

    @patch("app.chatgpt.views.chatgpt.req_gateway")
    @patch("app.chatgpt.views.chatgpt.resolve_managed_account")
    @patch("app.chatgpt.views.chatgpt.managed_account_options")
    def test_chatgpt_login_response_does_not_echo_upstream_credentials(
        self,
        managed_options,
        resolve_account,
        gateway,
    ):
        account = ChatgptAccount.objects.create(
            chatgpt_username="managed-login@example.com",
            plan_type="plus",
            access_token="login-access-secret-123456",
            session_token="login-session-secret-123456",
            access_token_valid=True,
            session_token_valid=True,
            created_time=1,
            updated_time=1,
        )
        managed_options.return_value = (Mock(account_id=account.id), [])
        resolve_account.return_value = account
        gateway.return_value = {
            "login_url": "https://www.tuwugpt.com/session/ready",
            "message": "ok",
            "access_token": account.access_token,
            "session_token": account.session_token,
            "cookie": "gateway-cookie-secret-123456",
        }
        user = User.objects.create_user(
            username="managed-login-user",
            password="Strong-password-123!",
        )
        request = self.factory.post(
            "/0x/chatgpt/login",
            {"login_mode": "web"},
            format="json",
        )
        force_authenticate(request, user=user)

        from app.chatgpt.views.chatgpt import ChatGPTLoginView

        response = ChatGPTLoginView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {
                "login_url": "https://www.tuwugpt.com/session/ready",
                "message": "ok",
            },
        )
        rendered = json.dumps(response.data, ensure_ascii=False)
        self.assertNotIn(account.access_token, rendered)
        self.assertNotIn(account.session_token, rendered)
        self.assertNotIn("gateway-cookie-secret-123456", rendered)

    def test_clear_visit_logs_preserves_admin_login_logs(self):
        admin = User.objects.create_superuser(username="log-admin", password="password-123")
        VisitLog.objects.create(
            username=ADMIN_USERNAME,
            log_type="login",
            created_at=1,
            ip="127.0.0.1",
            user_agent="test",
        )
        VisitLog.objects.create(
            username=ADMIN_USERNAME,
            log_type="logout",
            created_at=2,
            ip="127.0.0.1",
            user_agent="test",
        )
        VisitLog.objects.create(
            username="normal-user",
            log_type="login",
            created_at=3,
            ip="127.0.0.1",
            user_agent="test",
        )

        request = self.factory.delete("/0x/user/visit-log")
        force_authenticate(request, user=admin)
        response = VisitLogView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["deleted_count"], 2)
        self.assertEqual(response.data["protected_count"], 1)
        self.assertEqual(VisitLog.objects.count(), 1)
        self.assertTrue(
            VisitLog.objects.filter(username=ADMIN_USERNAME, log_type="login").exists()
        )

    @patch("app.accounts.views.login.TURNSTILE_SECRET_KEY", "test-secret")
    @patch("app.accounts.views.login.LOCAL_CAPTCHA_ENABLED", False)
    @patch("app.accounts.views.login.TURNSTILE_ENABLED", True)
    @patch("app.accounts.views.login.requests.post")
    def test_turnstile_validation_checks_action(self, post):
        post.return_value.json.return_value = {
            "success": True,
            "action": "login",
        }
        request = self.factory.post(
            "/0x/user/login",
            {"turnstile_token": "test-token"},
            format="json",
        )
        request.data = {"turnstile_token": "test-token"}

        verify_turnstile(request, "login")

        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["data"]["secret"], "test-secret")
        self.assertEqual(post.call_args.kwargs["data"]["response"], "test-token")

    @patch("app.accounts.views.login.TURNSTILE_SECRET_KEY", "test-secret")
    @patch("app.accounts.views.login.LOCAL_CAPTCHA_ENABLED", False)
    @patch("app.accounts.views.login.TURNSTILE_ENABLED", True)
    @patch("app.accounts.views.login.requests.post")
    def test_turnstile_rejects_wrong_action(self, post):
        post.return_value.json.return_value = {
            "success": True,
            "action": "register",
        }
        request = self.factory.post(
            "/0x/user/login",
            {"turnstile_token": "test-token"},
            format="json",
        )
        request.data = {"turnstile_token": "test-token"}

        with self.assertRaises(ValidationError):
            verify_turnstile(request, "login")

    @patch("app.accounts.captcha.secrets.choice", side_effect=list("234678"))
    def test_local_captcha_is_png_scoped_and_single_use(self, _choice):
        captcha = issue_local_captcha("login")
        self.assertTrue(captcha["image_data_url"].startswith("data:image/png;base64,"))
        payload = inspect_local_captcha_token(captcha["captcha_token"])
        self.assertEqual(payload["action"], "login")
        self.assertNotIn("answer", payload)
        self.assertIsNotNone(cache.get(_challenge_cache_key(payload["nonce"])))
        self.assertEqual(captcha["expires_in"], 120)
        verify_local_captcha(captcha["captcha_token"], "234678", "login")
        self.assertIsNone(cache.get(_challenge_cache_key(payload["nonce"])))
        with self.assertRaises(ValidationError):
            verify_local_captcha(captcha["captcha_token"], "234678", "login")

    def test_local_captcha_alphabet_avoids_ambiguous_characters(self):
        self.assertEqual(CAPTCHA_LENGTH, 6)
        self.assertFalse(set("05BILOSZ").intersection(CAPTCHA_ALPHABET))
        self.assertGreaterEqual(len(CAPTCHA_ALPHABET) ** CAPTCHA_LENGTH, 300_000_000)

    @patch("app.accounts.views.login.issue_local_captcha")
    @patch("app.accounts.views.login.LOCAL_CAPTCHA_ENABLED", True)
    def test_local_captcha_issue_rate_is_limited_per_ip(self, issue_captcha):
        issue_captcha.return_value = {
            "captcha_token": "signed-token",
            "image_data_url": "data:image/png;base64,AA==",
            "expires_in": 120,
        }
        cache.clear()
        view = LocalCaptchaView.as_view()
        responses = [
            view(
                self.factory.get(
                    "/0x/user/captcha?action=login",
                    REMOTE_ADDR="198.51.100.77",
                )
            )
            for _ in range(301)
        ]
        self.assertTrue(all(response.status_code == 200 for response in responses[:300]))
        self.assertEqual(responses[300].status_code, 429)
        cache.clear()

    @patch("app.accounts.captcha.secrets.choice", side_effect=list("234678"))
    def test_wrong_answer_consumes_the_captcha(self, _choice):
        captcha = issue_local_captcha("login")
        with self.assertRaises(ValidationError):
            verify_local_captcha(captcha["captcha_token"], "AAAAAA", "login")
        with self.assertRaises(ValidationError) as reused:
            verify_local_captcha(captcha["captcha_token"], "234678", "login")
        self.assertIn("已使用", str(reused.exception.detail))

    def test_captcha_layout_has_visible_vertical_variation(self):
        layout = _glyph_layout(CAPTCHA_LENGTH, random.Random(20260812))
        y_positions = [item["y"] for item in layout]
        x_gaps = [layout[index + 1]["x"] - layout[index]["x"] for index in range(CAPTCHA_LENGTH - 1)]
        self.assertGreaterEqual(max(y_positions) - min(y_positions), 10)
        self.assertGreaterEqual(len(set(item["rotation"] for item in layout)), 3)
        self.assertGreater(len(set(x_gaps)), 1)

    @patch("app.accounts.views.login.issue_local_captcha")
    @patch("app.accounts.views.login.LOCAL_CAPTCHA_ENABLED", True)
    def test_local_captcha_endpoint_uses_same_origin_payload(self, issue_captcha):
        issue_captcha.return_value = {
            "captcha_token": "signed-token",
            "image_data_url": "data:image/png;base64,AA==",
            "expires_in": 300,
        }
        request = self.factory.get("/0x/user/captcha?action=login", REMOTE_ADDR="203.0.113.9")
        response = LocalCaptchaView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["captcha_token"], "signed-token")
        issue_captcha.assert_called_once_with("login")

    @patch("app.accounts.views.login.verify_local_captcha")
    @patch("app.accounts.views.login.LOCAL_CAPTCHA_ENABLED", True)
    @patch("app.accounts.views.login.TURNSTILE_ENABLED", False)
    def test_local_captcha_replaces_external_turnstile(self, verify_captcha):
        request = APIRequestFactory().post(
            "/0x/user/login",
            {"captcha_token": "signed-token", "captcha_answer": "ABC234"},
            format="json",
        )
        request.data = {"captcha_token": "signed-token", "captcha_answer": "ABC234"}
        verify_turnstile(request, "login")
        verify_captcha.assert_called_once_with("signed-token", "ABC234", "login")


class EmailAuthenticationTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def create_challenge(self, email, purpose, *, user=None, code="123456", expires_at=None):
        return EmailVerificationChallenge.objects.create(
            user=user,
            email=email,
            purpose=purpose,
            code_hash=make_password(code),
            delivery_token=code,
            delivery_queued_at=timezone.now(),
            expires_at=expires_at or timezone.now() + timedelta(minutes=10),
        )

    @override_settings(BILLING_ENABLED=True)
    @patch("app.accounts.views.login.ALLOW_REGISTER", True)
    def test_registration_requires_a_valid_email_challenge(self):
        payload = {
            "email": "new-user@qq.com",
            "password": "Strong-password-123!",
            "verification_code": "123456",
        }
        missing = AccountRegister.as_view()(self.factory.post("/0x/user/register", payload, format="json"))
        self.assertEqual(missing.status_code, 400)
        self.assertFalse(User.objects.filter(username=payload["email"]).exists())

        self.create_challenge(payload["email"], EmailVerificationPurpose.REGISTER)
        registered = AccountRegister.as_view()(self.factory.post("/0x/user/register", payload, format="json"))
        self.assertEqual(registered.status_code, 200)
        self.assertTrue(registered.data["authenticated"])
        self.assertTrue(User.objects.filter(email=payload["email"], email_verified_at__isnull=False).exists())

    @override_settings(BILLING_ENABLED=True)
    @patch("app.accounts.views.login.ALLOW_REGISTER", True)
    def test_registration_rejects_disallowed_email_domain(self):
        response = AccountRegister.as_view()(
            self.factory.post(
                "/0x/user/register",
                {
                    "email": "new-user@example.com",
                    "password": "Strong-password-123!",
                    "verification_code": "123456",
                },
                format="json",
            )
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(username="new-user@example.com").exists())

    def test_failed_codes_lock_the_challenge_and_cannot_be_replayed(self):
        challenge = self.create_challenge("locked@qq.com", EmailVerificationPurpose.REGISTER)
        from app.accounts.email_auth import consume_verification_challenge

        for _ in range(5):
            with self.assertRaises(ValidationError):
                consume_verification_challenge(
                    purpose=EmailVerificationPurpose.REGISTER,
                    email="locked@qq.com",
                    code="000000",
                )

        challenge.refresh_from_db()
        self.assertIsNotNone(challenge.locked_at)
        with self.assertRaises(ValidationError):
            consume_verification_challenge(
                purpose=EmailVerificationPurpose.REGISTER,
                email="locked@qq.com",
                code="123456",
            )

    @override_settings(
        EMAIL_VERIFICATION_ENABLED=True,
        EMAIL_HOST="smtp.example.test",
        EMAIL_HOST_USER="sender@example.test",
        EMAIL_HOST_PASSWORD="test-password",
    )
    @patch("app.accounts.tasks.send_verification_email_task.delay")
    def test_verification_challenge_queues_without_plaintext_code(self, delay):
        from app.accounts.email_auth import create_verification_challenge

        challenge = create_verification_challenge(
            email="queued-user@qq.com",
            purpose=EmailVerificationPurpose.REGISTER,
        )

        delay.assert_called_once()
        self.assertEqual(delay.call_args.args, (challenge.pk,))
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT delivery_token FROM accounts_emailverificationchallenge WHERE id = %s",
                [challenge.pk],
            )
            stored_token = cursor.fetchone()[0]
        self.assertTrue(str(stored_token).startswith("enc:v1:"))
        self.assertNotIn("123456", str(stored_token))

    @override_settings(EMAIL_VERIFICATION_DELIVERY_MAX_ATTEMPTS=3)
    @patch("app.accounts.tasks.send_mail", return_value=1)
    def test_verification_delivery_marks_challenge_sent_after_smtp_accepts(self, send_mail):
        from app.accounts.tasks import send_verification_email_task
        challenge = self.create_challenge("deliver-user@qq.com", EmailVerificationPurpose.REGISTER)
        result = send_verification_email_task.apply(args=[challenge.pk])

        self.assertTrue(result.successful())
        self.assertTrue(result.result)
        challenge.refresh_from_db()
        self.assertEqual(challenge.delivery_status, "SENT")
        self.assertEqual(challenge.delivery_attempt_count, 1)
        send_mail.assert_called_once()

    @override_settings(EMAIL_VERIFICATION_DELIVERY_MAX_ATTEMPTS=3)
    @patch("app.accounts.tasks.send_verification_email_task.retry", side_effect=Retry())
    @patch("app.accounts.tasks.send_mail", side_effect=OSError("temporary failure"))
    def test_verification_delivery_retries_after_transient_smtp_failure(self, send_mail, retry):
        from app.accounts.tasks import send_verification_email_task
        challenge = self.create_challenge("retry-user@qq.com", EmailVerificationPurpose.REGISTER)
        with self.assertRaises(Retry):
            send_verification_email_task.run(challenge.pk)

        challenge.refresh_from_db()
        self.assertEqual(challenge.delivery_status, "PENDING")
        self.assertEqual(challenge.delivery_attempt_count, 1)
        self.assertEqual(challenge.delivery_error, "OSError")
        send_mail.assert_called_once()
        retry.assert_called_once()

    @override_settings(EMAIL_VERIFICATION_DELIVERY_MAX_ATTEMPTS=3)
    @patch("app.accounts.tasks.send_mail", side_effect=OSError("permanent failure"))
    def test_failed_verification_delivery_invalidates_challenge(self, send_mail):
        from app.accounts.tasks import send_verification_email_task
        challenge = self.create_challenge("failed-send@qq.com", EmailVerificationPurpose.REGISTER)
        challenge.delivery_attempt_count = 2
        challenge.save(update_fields=["delivery_attempt_count"])

        result = send_verification_email_task.apply(args=[challenge.pk])

        self.assertTrue(result.successful())
        self.assertFalse(result.result)
        challenge.refresh_from_db()
        self.assertEqual(challenge.delivery_status, "FAILED")
        self.assertIsNotNone(challenge.invalidated_at)
        self.assertEqual(send_mail.call_count, 1)

    @patch("app.accounts.views.req_gateway")
    def test_admin_email_change_requires_reverification_and_revokes_tokens(self, _req_gateway):
        admin = User.objects.create_superuser(username="email-admin", password="Strong-password-123!")
        user = User.objects.create_user(
            username="email-edit-user",
            email="old-email@qq.com",
            email_verified_at=timezone.now(),
            password="Strong-password-123!",
        )
        token = Token.objects.create(user=user)
        self.create_challenge(
            user.email,
            EmailVerificationPurpose.PASSWORD_RESET,
            user=user,
        )
        request = self.factory.post(
            "/0x/user",
            {
                "username": user.username,
                "email": "new-email@qq.com",
                "is_active": True,
                "isolated_session": True,
                "gptcar_list": [],
                "model_limit": [],
                "remark": "",
                "daily_quota": 0,
                "monthly_quota": 0,
            },
            format="json",
        )
        force_authenticate(request, user=admin)

        response = UserAccountView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.email, "new-email@qq.com")
        self.assertIsNone(user.email_verified_at)
        self.assertFalse(Token.objects.filter(pk=token.pk).exists())
        self.assertFalse(
            EmailVerificationChallenge.objects.filter(
                user=user,
                purpose=EmailVerificationPurpose.PASSWORD_RESET,
                invalidated_at__isnull=True,
            ).exists()
        )

    def test_admin_can_update_user_with_expiration_date(self):
        admin = User.objects.create_superuser(username="expiry-admin", password="Strong-password-123!")
        user = User.objects.create_user(username="expiry-edit-user", password="Strong-password-123!")
        expires_on = timezone.localdate() + timedelta(days=30)
        request = self.factory.post(
            "/0x/user/",
            {
                "username": user.username,
                "email": "",
                "is_active": True,
                "isolated_session": True,
                "gptcar_list": [1, 2, 3],
                "model_limit": [],
                "remark": "",
                "expired_date": expires_on.isoformat(),
                "daily_quota": 1000,
                "monthly_quota": 1999,
            },
            format="json",
        )
        force_authenticate(request, user=admin)

        response = UserAccountView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.expired_date, expires_on)
        self.assertEqual(user.gptcar_list, [1, 2, 3])

    def test_email_like_legacy_username_stops_working_after_email_binding(self):
        from app.accounts.views.login import _find_login_user

        user = User.objects.create_user(
            username="former-email@qq.com",
            email="new-email@qq.com",
            email_verified_at=timezone.now(),
            password="Strong-password-123!",
        )

        self.assertIsNone(_find_login_user("former-email@qq.com"))
        self.assertEqual(_find_login_user("new-email@qq.com"), user)

    @patch("app.accounts.views.login.req_gateway")
    def test_user_can_confirm_own_email_change(self, _req_gateway):
        user = User.objects.create_user(
            username="self-email-user",
            email="old-self@qq.com",
            email_verified_at=timezone.now(),
            password="Strong-password-123!",
        )
        old_token = Token.objects.create(user=user)
        self.create_challenge(
            "new-self@qq.com",
            EmailVerificationPurpose.EMAIL_CHANGE,
            user=user,
        )
        request = self.factory.post(
            "/0x/user/email-change/confirm",
            {"verification_code": "123456"},
            format="json",
        )
        force_authenticate(request, user=user, token=old_token)

        response = EmailChangeConfirmView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.email, "new-self@qq.com")
        self.assertIsNotNone(user.email_verified_at)
        self.assertFalse(Token.objects.filter(pk=old_token.pk).exists())

    @patch("app.accounts.views.login.req_gateway", return_value={"message": "ok"})
    def test_password_reset_revokes_existing_tokens(self, _req_gateway):
        user = User.objects.create_user(
            username="reset-user",
            email="reset-user@qq.com",
            email_verified_at=timezone.now(),
            password="Old-strong-password-123!",
        )
        old_token = Token.objects.create(user=user)
        self.create_challenge(
            user.email,
            EmailVerificationPurpose.PASSWORD_RESET,
            user=user,
        )
        response = PasswordResetConfirmView.as_view()(
            self.factory.post(
                "/0x/user/password-reset/confirm",
                {
                    "email": user.email,
                    "verification_code": "123456",
                    "new_password": "New-strong-password-456!",
                },
                format="json",
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Token.objects.filter(pk=old_token.pk).exists())
        user.refresh_from_db()
        self.assertTrue(user.check_password("New-strong-password-456!"))

    @patch("app.accounts.views.login.LOCAL_CAPTCHA_ENABLED", False)
    @patch("app.accounts.views.login.TURNSTILE_ENABLED", False)
    def test_legacy_user_must_bind_email_before_a_token_is_issued(self):
        user = User.objects.create_user(username="legacy-user", password="Strong-password-123!")
        login = AccountLogin.as_view()(
            self.factory.post(
                "/0x/user/login",
                {"identifier": "legacy-user", "password": "Strong-password-123!"},
                format="json",
            )
        )
        self.assertEqual(login.status_code, 200)
        self.assertFalse(login.data["authenticated"])
        self.assertTrue(login.data["email_binding_required"])
        self.assertFalse(Token.objects.filter(user=user).exists())

        self.create_challenge(
            "legacy-user@qq.com",
            EmailVerificationPurpose.EMAIL_BINDING,
            user=user,
        )
        bound = EmailBindingConfirmView.as_view()(
            self.factory.post(
                "/0x/user/email-binding/confirm",
                {"binding_ticket": login.data["binding_ticket"], "verification_code": "123456"},
                format="json",
            )
        )
        self.assertEqual(bound.status_code, 200)
        self.assertTrue(bound.data["authenticated"])
        user.refresh_from_db()
        self.assertEqual(user.email, "legacy-user@qq.com")
        self.assertIsNotNone(user.email_verified_at)
        self.assertTrue(Token.objects.filter(user=user).exists())

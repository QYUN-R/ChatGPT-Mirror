from unittest.mock import patch
from datetime import timedelta

from celery.exceptions import Retry
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
    VisitLog,
)
from app.accounts.views import UserAccountView, VisitLogView, ChangePasswordView
from app.accounts.authentication import AUTH_COOKIE_NAME, ExpiringCookieTokenAuthentication
from app.accounts.views.cfg import AccessControlView
from app.accounts.views.login import (
    AccountLogin,
    AccountLogout,
    AccountRegister,
    EmailBindingConfirmView,
    EmailChangeConfirmView,
    PasswordResetConfirmView,
    UserFreeLoginView,
    verify_turnstile,
)
from app.chatgpt.models import ChatgptAccount
from app.chatgpt.serializers import ShowChatgptTokenSerializer
from app.settings import ADMIN_USERNAME, FREE_ACCOUNT_USERNAME
from app.utils import get_client_ip


class SecurityRegressionTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def test_force_chat_mode_is_enabled_for_new_users(self):
        user = User.objects.create_user(username="work-mode-user", password="Strong-password-123!")
        self.assertTrue(user.force_chat_mode)

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

    def test_invalid_auth_cookie_does_not_block_public_version_config(self):
        client = APIClient()
        client.cookies[AUTH_COOKIE_NAME] = "invalid-token"

        response = client.get("/0x/user/version-cfg")

        self.assertEqual(response.status_code, 200)
        self.assertIn("allow_register", response.data)
        self.assertIn("billing_enabled", response.data)

    @override_settings(CSRF_TRUSTED_ORIGINS=["https://mirror.example"])
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

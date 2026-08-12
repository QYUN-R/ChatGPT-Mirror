from unittest.mock import Mock, patch

from django.test import TestCase

from app.chatgpt.models import ChatgptAccount
from app.cron import _update_token, probe_web_session, update_access_token
from app.billing.services import _web_probe_invalidates_credentials


class UpdateAccessTokenTests(TestCase):
    def make_account(self, **overrides):
        values = {
            "chatgpt_username": "account@example.com",
            "auth_status": True,
            "plan_type": "plus",
            "access_token": "access-token",
            "session_token": "session-token",
            "access_token_valid": True,
            "session_token_valid": True,
            "created_time": 1,
            "updated_time": 1,
        }
        values.update(overrides)
        return ChatgptAccount.objects.create(**values)

    @patch("app.cron._update_token")
    @patch("app.cron._access_token_is_fresh", return_value=True)
    def test_skips_fresh_and_valid_access_token(self, _fresh, update_token):
        self.make_account()

        update_access_token()

        update_token.assert_not_called()

    @patch("app.cron._update_token", return_value=True)
    @patch("app.cron._access_token_is_fresh", return_value=True)
    def test_refreshes_invalidated_access_token_from_session(self, _fresh, update_token):
        account = self.make_account(access_token_valid=False)

        update_access_token()

        update_token.assert_called_once_with(account.chatgpt_username, account.session_token)

    @patch("app.cron._update_token")
    @patch("app.cron._access_token_is_fresh", return_value=True)
    def test_does_not_retry_known_invalid_web_session(self, _fresh, update_token):
        self.make_account(access_token_valid=False, session_token_valid=False)

        update_access_token()

        update_token.assert_not_called()

    @patch("app.cron.ChatgptAccount.save_data")
    @patch("app.cron.requests.post")
    def test_successful_refresh_marks_access_token_valid(self, post, save_data):
        post.return_value = Mock(
            status_code=200,
            json=lambda: {
                "user_info": {"email": "account@example.com", "plan_type": "plus"},
                "access_token": "rotated-access-token",
                "session_token": "session-token",
            },
        )

        result = _update_token("account@example.com", "session-token")

        self.assertTrue(result)
        payload = save_data.call_args.args[0]
        self.assertTrue(payload["auth_status"])
        self.assertTrue(payload["access_token_valid"])

    @patch("app.cron.requests.post")
    @patch("app.cron.requests.Session")
    def test_web_probe_rejects_invalidated_backend_token(self, session_factory, post):
        post.return_value = Mock(
            status_code=200,
            json=lambda: {"login_url": "/api/not-login?user_gateway_token=test"},
            raise_for_status=lambda: None,
        )
        client = session_factory.return_value
        client.get.side_effect = [
            Mock(status_code=200),
            Mock(
                status_code=401,
                json=lambda: {"error": {"code": "token_invalidated"}},
            ),
        ]
        account = self.make_account()

        healthy, error = probe_web_session(account, public_url="https://mirror.example")

        self.assertFalse(healthy)
        self.assertEqual(error, "token_invalidated")

    def test_only_explicit_auth_errors_invalidate_stored_credentials(self):
        self.assertTrue(_web_probe_invalidates_credentials("token_invalidated"))
        self.assertFalse(_web_probe_invalidates_credentials("ReadTimeout"))
        self.assertFalse(_web_probe_invalidates_credentials("status_503"))

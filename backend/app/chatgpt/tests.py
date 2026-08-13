import importlib.util
import json
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase, override_settings

from app.chatgpt.gateway_sessions import gateway_session_identity, shared_gateway_login


MIRROR_TOKEN = "mirror-token-abcdefghijklmnopqrstuvwxyz-1234567890"


def create_gateway_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE gateway_sessions (
            id INTEGER PRIMARY KEY,
            user_name TEXT NOT NULL,
            chatgpt_username TEXT NOT NULL,
            access_token TEXT,
            session_token TEXT,
            extra_cookies TEXT,
            login_mode TEXT NOT NULL,
            mirror_token TEXT NOT NULL,
            isolated_session BOOLEAN NOT NULL,
            force_chat_mode BOOLEAN NOT NULL,
            limits TEXT,
            proxy_node_id INTEGER,
            daily_quota INTEGER NOT NULL,
            monthly_quota INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            UNIQUE(user_name, chatgpt_username)
        );
        CREATE TABLE conversation_owners (
            chatgpt_username TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(chatgpt_username, conversation_id)
        );
        CREATE TABLE visit_logs (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            chatgpt_username TEXT,
            log_type TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            ip TEXT,
            user_agent TEXT
        );
        """
    )
    connection.commit()
    return connection


class GatewaySessionReuseTests(SimpleTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.tempdir.name) / "gateway.db")
        self.connection = create_gateway_database(self.database_path)
        self.user = SimpleNamespace(pk=7, username="shared-user")
        self.account = SimpleNamespace(
            pk=9,
            chatgpt_username="plus@example.com",
            access_token="access-token-current",
            session_token="session-token-current",
        )
        self.payload = {
            "user_name": "ignored-device-subject",
            "login_mode": "web",
            "isolated_session": True,
            "force_chat_mode": True,
            "limits": ["gpt-5"],
            "proxy_node_id": 3,
            "daily_quota": 12,
            "monthly_quota": 240,
        }

    def tearDown(self):
        self.connection.close()
        self.tempdir.cleanup()

    def insert_session(self, **overrides):
        values = {
            "user_name": self.user.username,
            "chatgpt_username": self.account.chatgpt_username,
            "access_token": self.account.access_token,
            "session_token": self.account.session_token,
            "login_mode": "web",
            "mirror_token": MIRROR_TOKEN,
            "isolated_session": 1,
            "force_chat_mode": 1,
            "limits": json.dumps(["gpt-5"]),
            "proxy_node_id": 3,
            "daily_quota": 12,
            "monthly_quota": 240,
            "created_at": 100,
            "updated_at": 110,
        }
        values.update(overrides)
        self.connection.execute(
            """
            INSERT INTO gateway_sessions (
                user_name, chatgpt_username, access_token, session_token,
                login_mode, mirror_token,
                isolated_session, force_chat_mode, limits, proxy_node_id,
                daily_quota, monthly_quota, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(values[key] for key in (
                "user_name", "chatgpt_username", "access_token", "session_token",
                "login_mode", "mirror_token",
                "isolated_session", "force_chat_mode", "limits", "proxy_node_id",
                "daily_quota", "monthly_quota", "created_at", "updated_at",
            )),
        )
        self.connection.commit()

    @override_settings()
    def test_second_device_reuses_existing_session_without_rotating_token(self):
        self.insert_session()
        login_callable = Mock()
        with override_settings(GATEWAY_SESSION_DATABASE_PATH=self.database_path):
            result = shared_gateway_login(
                self.user,
                self.account,
                self.payload,
                login_callable,
            )
        self.assertEqual(
            result["login_url"],
            f"/api/not-login?user_gateway_token={MIRROR_TOKEN}",
        )
        self.assertTrue(result["reused"])
        login_callable.assert_not_called()

    def test_changed_session_token_rebuilds_web_session(self):
        self.insert_session(session_token="old-session-token")
        login_callable = Mock(return_value={"login_url": "/new", "message": "ok"})
        with override_settings(GATEWAY_SESSION_DATABASE_PATH=self.database_path):
            result = shared_gateway_login(
                self.user,
                self.account,
                self.payload,
                login_callable,
            )
        self.assertEqual(result["login_url"], "/new")
        login_payload = login_callable.call_args.args[0]
        self.assertEqual(login_payload["user_name"], self.user.username)

    def test_gateway_session_identity_resolves_only_exact_mirror_token(self):
        self.insert_session()
        with override_settings(GATEWAY_SESSION_DATABASE_PATH=self.database_path):
            identity = gateway_session_identity(MIRROR_TOKEN)
            unknown = gateway_session_identity(MIRROR_TOKEN + "-unknown")

        self.assertEqual(identity["user_name"], self.user.username)
        self.assertEqual(identity["chatgpt_username"], self.account.chatgpt_username)
        self.assertEqual(identity["login_mode"], "web")
        self.assertIsNone(unknown)


class DeviceHistoryMergeTests(SimpleTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.tempdir.name) / "gateway.db")
        self.connection = create_gateway_database(self.database_path)
        script_path = Path(__file__).resolve().parents[3] / "scripts" / "merge_device_chat_history.py"
        spec = importlib.util.spec_from_file_location("merge_device_chat_history", script_path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def tearDown(self):
        self.connection.close()
        self.tempdir.cleanup()

    def test_alias_history_is_merged_without_replacing_canonical_session(self):
        alias = "1111:device:9dd681ee-471e-4cde-801e-97b024618f97"
        session_values = (
            "plus@example.com", "web", MIRROR_TOKEN, 1, 1, "[]", 0, 0, 100, 110,
        )
        for user_name, token in (("1111", MIRROR_TOKEN), (alias, MIRROR_TOKEN + "-alias")):
            self.connection.execute(
                """
                INSERT INTO gateway_sessions (
                    user_name, chatgpt_username, login_mode, mirror_token,
                    isolated_session, force_chat_mode, limits, daily_quota,
                    monthly_quota, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_name, session_values[0], session_values[1], token, *session_values[3:]),
            )
        self.connection.execute(
            "INSERT INTO conversation_owners VALUES (?, ?, ?, ?, ?)",
            ("plus@example.com", "conversation-mobile", alias, 100, 100),
        )
        self.connection.execute(
            "INSERT INTO visit_logs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, alias, "plus@example.com", "proxy", 100, "127.0.0.1", "mobile"),
        )
        self.connection.commit()

        dry_run = self.module.merge(self.database_path)
        self.assertEqual(dry_run["conversation_aliases"], 1)
        applied = self.module.merge(self.database_path, apply_changes=True)
        self.assertEqual(applied["conversation_rows"], 1)
        self.assertEqual(applied["sessions_removed"], 1)

        owner = self.connection.execute(
            "SELECT user_name FROM conversation_owners WHERE conversation_id = ?",
            ("conversation-mobile",),
        ).fetchone()[0]
        self.assertEqual(owner, "1111")
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM gateway_sessions WHERE user_name = ?",
                (alias,),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM gateway_sessions WHERE user_name = ?",
                ("1111",),
            ).fetchone()[0],
            1,
        )


class GatewayRecoveryScriptTests(SimpleTestCase):
    def test_recovery_script_uses_authenticated_csrf_protected_api_path(self):
        script_path = Path(__file__).resolve().parents[3] / "deploy" / "nginx" / "gateway-session-recovery.js"
        script = script_path.read_text(encoding="utf-8")

        self.assertIn("/0x/chatgpt/session-failure", script)
        self.assertIn("X-CSRFToken", script)
        self.assertIn("window.location.replace(recoveryUrl)", script)
        self.assertIn("tuwugpt-session-recovery-control", script)
        self.assertNotIn("fetch('/gateway/session-failure'", script)

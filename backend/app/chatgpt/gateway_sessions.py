import hashlib
import json
import sqlite3
import time
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from rest_framework.exceptions import ValidationError


SESSION_LOCK_SECONDS = 15
SESSION_LOCK_WAIT_SECONDS = 8


def canonical_gateway_subject(user):
    return user.username


def mirror_login_url(mirror_token):
    return "/api/not-login?" + urlencode({"user_gateway_token": mirror_token})


def _json_value(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _session_row(user_name, chatgpt_username):
    database_path = str(getattr(settings, "GATEWAY_SESSION_DATABASE_PATH", "") or "").strip()
    if not database_path:
        return None
    for attempt in range(3):
        try:
            connection = sqlite3.connect(
                f"file:{database_path}?mode=ro",
                uri=True,
                timeout=1.5,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA busy_timeout = 1500")
            try:
                return connection.execute(
                    """
                    SELECT user_name, chatgpt_username, access_token, session_token,
                           login_mode, mirror_token, isolated_session,
                           force_chat_mode, limits, proxy_node_id, daily_quota,
                           monthly_quota, updated_at
                      FROM gateway_sessions
                     WHERE user_name = ? AND chatgpt_username = ?
                     ORDER BY updated_at DESC
                     LIMIT 1
                    """,
                    (user_name, chatgpt_username),
                ).fetchone()
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            if attempt == 2:
                return None
            time.sleep(0.05 * (attempt + 1))
    return None


def gateway_session_identity(mirror_token):
    mirror_token = str(mirror_token or "").strip()
    if not 32 <= len(mirror_token) <= 512 or any(ord(char) < 33 for char in mirror_token):
        return None

    database_path = str(getattr(settings, "GATEWAY_SESSION_DATABASE_PATH", "") or "").strip()
    if not database_path:
        return None
    for attempt in range(3):
        try:
            connection = sqlite3.connect(
                f"file:{database_path}?mode=ro",
                uri=True,
                timeout=1.5,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA busy_timeout = 1500")
            try:
                row = connection.execute(
                    """
                    SELECT user_name, chatgpt_username, login_mode, updated_at
                      FROM gateway_sessions
                     WHERE mirror_token = ?
                     ORDER BY updated_at DESC
                     LIMIT 1
                    """,
                    (mirror_token,),
                ).fetchone()
                return dict(row) if row else None
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            if attempt == 2:
                return None
            time.sleep(0.05 * (attempt + 1))
    return None


def reusable_gateway_session(user_name, account, payload):
    row = _session_row(user_name, account.chatgpt_username)
    if not row:
        return None

    mirror_token = str(row["mirror_token"] or "").strip()
    if not 32 <= len(mirror_token) <= 512 or any(ord(char) < 33 for char in mirror_token):
        return None

    expected_limits = payload.get("limits") or []
    login_mode = str(payload.get("login_mode") or "")
    credential_matches = (
        str(row["session_token"] or "") == str(account.session_token or "")
        if login_mode == "web"
        else str(row["access_token"] or "") == str(account.access_token or "")
    )
    checks = (
        credential_matches,
        str(row["login_mode"] or "") == login_mode,
        bool(row["isolated_session"]) == bool(payload.get("isolated_session")),
        bool(row["force_chat_mode"]) == bool(payload.get("force_chat_mode")),
        _json_value(row["limits"], []) == expected_limits,
        row["proxy_node_id"] == payload.get("proxy_node_id"),
        int(row["daily_quota"] or 0) == int(payload.get("daily_quota") or 0),
        int(row["monthly_quota"] or 0) == int(payload.get("monthly_quota") or 0),
    )
    if not all(checks):
        return None
    return {
        "login_url": mirror_login_url(mirror_token),
        "message": "已连接现有会话",
        "reused": True,
    }


def shared_gateway_login(user, account, payload, login_callable):
    user_name = canonical_gateway_subject(user)
    payload = {**payload, "user_name": user_name}
    existing = reusable_gateway_session(user_name, account, payload)
    if existing:
        return existing

    lock_digest = hashlib.sha256(
        f"{user.pk}:{account.pk}:{payload.get('login_mode')}".encode("utf-8")
    ).hexdigest()
    lock_key = f"gateway-session-login:{lock_digest}"
    lock_value = hashlib.sha256(f"{time.time_ns()}:{lock_digest}".encode()).hexdigest()
    deadline = time.monotonic() + SESSION_LOCK_WAIT_SECONDS

    while not cache.add(lock_key, lock_value, timeout=SESSION_LOCK_SECONDS):
        existing = reusable_gateway_session(user_name, account, payload)
        if existing:
            return existing
        if time.monotonic() >= deadline:
            break
        time.sleep(0.1)

    owns_lock = cache.get(lock_key) == lock_value
    if not owns_lock:
        existing = reusable_gateway_session(user_name, account, payload)
        if existing:
            return existing
        raise ValidationError({"message": "正在建立共享会话，请稍后重试"})
    try:
        existing = reusable_gateway_session(user_name, account, payload)
        if existing:
            return existing
        return login_callable(payload)
    finally:
        if owns_lock and cache.get(lock_key) == lock_value:
            cache.delete(lock_key)

# myapp/cron.py
import logging
import time

import jwt
import requests
from django.db import transaction

from app.chatgpt.models import ChatgptAccount
from app.settings import CHATGPT_GATEWAY_URL
from app.settings import GATEWAY_ADMIN_SECRET

logger = logging.getLogger("cron")
DEFAULT_REFRESH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REFRESH_WINDOW_SECONDS = 100 * 60


def _access_token_is_fresh(access_token):
    try:
        at_info = jwt.decode(access_token, options={"verify_signature": False})
        return int(time.time()) < at_info["exp"] - REFRESH_WINDOW_SECONDS
    except Exception:
        return False


def _update_token(chatgpt_username, chatgpt_token, client_id=None):
    url = CHATGPT_GATEWAY_URL + "/api/get-user-info"
    headers = {
        "Authorization": "Bearer {}".format(GATEWAY_ADMIN_SECRET),
    }
    payload = {"chatgpt_token": chatgpt_token}
    if client_id:
        payload = {
            "auth_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": chatgpt_token,
        }
    res = requests.post(url, headers=headers, json=payload)
    res_json = res.json()

    if res.status_code != 200:
        logger.info("token 更新失败: account=%s status=%s", chatgpt_username, res.status_code)
        message = res_json.get("message", "") if isinstance(res_json, dict) else str(res_json)
        if (
            "token 失效" in message
            or "authentication token" in message
            or "refresh_token" in message
        ):
            logger.warning("token 失效: account=%s status=%s", chatgpt_username, res.status_code)
            return False
        return None

    res_json["auth_status"] = True
    res_json["access_token_valid"] = True
    ChatgptAccount.save_data(res_json)
    return True


def update_access_token():
    for line in ChatgptAccount.objects.filter(is_archived=False):
        if (
            _access_token_is_fresh(line.access_token)
            and line.auth_status
            and line.access_token_valid
        ):
            continue

        if line.refresh_token:
            with transaction.atomic():
                locked = ChatgptAccount.objects.select_for_update().get(id=line.id)
                if (
                    _access_token_is_fresh(locked.access_token)
                    and locked.auth_status
                    and locked.access_token_valid
                ):
                    continue

                client_id = locked.refresh_client_id or DEFAULT_REFRESH_CLIENT_ID
                update_status = _update_token(locked.chatgpt_username, locked.refresh_token, client_id)
                if update_status is False:
                    locked.refresh_token = None
                    locked.save()
                    logger.warning("refresh_token 已经过期: account=%s", locked.chatgpt_username)

        elif line.session_token and line.session_token_valid:
            update_status = _update_token(line.chatgpt_username, line.session_token)
            if update_status is False:
                line.session_token = None
                line.save()
                logger.warning("session_token 已经过期: account=%s", line.chatgpt_username)


def check_access_token():

    need_to_update = int(time.time() - 3600)
    for line in ChatgptAccount.objects.filter(updated_time__lte=need_to_update, auth_status=True).all():
        if line.access_token:
            if _update_token(line.chatgpt_username, line.access_token) is False:
                line.auth_status = False
                line.updated_time = int(time.time())
                line.save()
                logger.warning(f"access_token 已经过期: {line.chatgpt_username}")
                return
            else:
                line.updated_time = int(time.time())
                line.save()
                logger.info(f"access_token 有效: {line.chatgpt_username}")


def probe_web_session(account, *, public_url):
    """Verify the complete web login path without exposing account secrets."""
    import uuid
    from urllib.parse import urljoin

    subject = f"health-{account.id}-{uuid.uuid4().hex}"
    try:
        login = requests.post(
            CHATGPT_GATEWAY_URL + "/api/login",
            headers={"Authorization": f"Bearer {GATEWAY_ADMIN_SECRET}"},
            json={
                "user_name": subject,
                "access_token": account.access_token,
                "session_token": account.session_token,
                "extra_cookies": account.extra_cookies,
                "login_mode": "web",
                "isolated_session": True,
                "limits": [],
                "proxy_node_id": account.proxy_node_id,
                "daily_quota": 0,
                "monthly_quota": 0,
                "force_chat_mode": True,
            },
            timeout=30,
        )
        login.raise_for_status()
        login_url = (login.json() or {}).get("login_url") or ""
        if not login_url:
            return False, "login_url_missing"

        client = requests.Session()
        bootstrap = client.get(
            urljoin(public_url.rstrip("/") + "/", login_url.lstrip("/")),
            allow_redirects=True,
            timeout=30,
        )
        if bootstrap.status_code >= 400:
            return False, f"bootstrap_{bootstrap.status_code}"
        response = client.get(public_url.rstrip("/") + "/backend-api/me", timeout=30)
        if response.status_code == 200:
            try:
                payload = response.json()
            except Exception:
                return False, "me_invalid_json"
            if isinstance(payload, dict) and (payload.get("user") or payload.get("id")):
                return True, ""
            return False, "me_missing_user"
        try:
            payload = response.json()
            code = (payload.get("error") or {}).get("code") or ""
        except Exception:
            code = ""
        return False, str(code or f"status_{response.status_code}")[:80]
    except requests.RequestException as exc:
        return False, type(exc).__name__
    finally:
        try:
            requests.post(
                CHATGPT_GATEWAY_URL + "/api/logout",
                headers={"Authorization": f"Bearer {GATEWAY_ADMIN_SECRET}"},
                json={"user_name": subject},
                timeout=10,
            )
        except requests.RequestException:
            pass

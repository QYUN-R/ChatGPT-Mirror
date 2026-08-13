import hashlib
import ipaddress
import json
import re
import time
import uuid

import requests
from django.conf import settings
from django.core import signing
from django.core.signing import BadSignature, SignatureExpired
from requests.exceptions import RequestException
from rest_framework.exceptions import ValidationError

from app.accounts.models import VisitLog
from app.settings import CHATGPT_GATEWAY_URL, FREE_ACCOUNT_USERNAME
from app.settings import GATEWAY_ADMIN_SECRET

FREE_SESSION_SALT = "chatgpt-mirror.free-session.v1"
FREE_SESSION_MAX_AGE = 7 * 24 * 60 * 60
SENSITIVE_KEY_FRAGMENTS = (
    "token",
    "secret",
    "credential",
    "access_token",
    "session_token",
    "refresh_token",
    "client_secret",
    "private_key",
    "password",
    "cookie",
    "authorization",
)
SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]{12,}"),
    re.compile(r"\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\b"),
    re.compile(r"\b(?:sk|sess)-[a-zA-Z0-9_-]{12,}\b"),
    re.compile(
        r"(?i)((?:access|session|refresh|chatgpt)?[_-]?token|cookie|authorization|password|secret)"
        r"(\s*[:=]\s*)([^\s,;]+)"
    ),
)


def generate_md5(input_string):
    md5_object = hashlib.md5()
    md5_object.update(input_string.encode('utf-8'))
    return md5_object.hexdigest()

def get_client_ip(request):
    candidates = (
        request.META.get("HTTP_X_CHATGPT_MIRROR_CLIENT_IP"),
        request.META.get("HTTP_CF_CONNECTING_IP"),
        request.META.get("HTTP_TRUE_CLIENT_IP"),
        request.META.get("HTTP_X_REAL_IP"),
        request.META.get("HTTP_X_FORWARDED_FOR"),
        request.META.get("REMOTE_ADDR"),
    )
    for candidate in candidates:
        if not candidate:
            continue
        for value in candidate.split(","):
            value = value.strip().strip("[]")
            try:
                return str(ipaddress.ip_address(value))
            except ValueError:
                continue
    return ""


def _parse_ip(value):
    if not value:
        return None
    value = str(value).split(",", 1)[0].strip().strip("[]")
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _in_networks(address, cidrs):
    if address is None:
        return False
    for cidr in cidrs:
        try:
            if address in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def get_redemption_client_ip(request):
    """Return a client IP only from headers written by a trusted reverse proxy."""

    remote = _parse_ip(request.META.get("REMOTE_ADDR"))
    if not _in_networks(remote, settings.REDEMPTION_TRUSTED_PROXY_CIDRS):
        return str(remote) if remote else ""

    gateway_client = _parse_ip(request.META.get("HTTP_X_CHATGPT_MIRROR_CLIENT_IP"))
    if gateway_client:
        return str(gateway_client)

    proxy_peer = _parse_ip(request.META.get("HTTP_X_REAL_IP"))
    cloudflare_client = _parse_ip(request.META.get("HTTP_CF_CONNECTING_IP"))
    if _in_networks(proxy_peer, settings.REDEMPTION_CLOUDFLARE_CIDRS) and cloudflare_client:
        return str(cloudflare_client)
    if proxy_peer:
        return str(proxy_peer)
    return str(remote) if remote else ""


def issue_free_session():
    return signing.dumps({"sid": uuid.uuid4().hex}, salt=FREE_SESSION_SALT, compress=True)


def redact_sensitive_data(value, *, secrets=()):
    secret_values = tuple(
        str(item) for item in secrets if item is not None and len(str(item)) >= 6
    )
    if isinstance(value, dict):
        return {
            str(key): "[redacted]"
            if any(fragment in str(key).lower() for fragment in SENSITIVE_KEY_FRAGMENTS)
            else redact_sensitive_data(item, secrets=secret_values)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_data(item, secrets=secret_values) for item in value[:100]]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_data(item, secrets=secret_values) for item in value[:100])
    if isinstance(value, str):
        redacted = value
        for secret in secret_values:
            redacted = redacted.replace(secret, "[redacted]")
        redacted = SENSITIVE_TEXT_PATTERNS[0].sub("Bearer [redacted]", redacted)
        redacted = SENSITIVE_TEXT_PATTERNS[1].sub("[redacted]", redacted)
        redacted = SENSITIVE_TEXT_PATTERNS[2].sub("[redacted]", redacted)
        redacted = SENSITIVE_TEXT_PATTERNS[3].sub(r"\1\2[redacted]", redacted)
        return redacted[:2000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_sensitive_data(str(value), secrets=secret_values)


def get_request_subject(request):
    if request.user.username != FREE_ACCOUNT_USERNAME:
        from app.accounts.authentication import get_device_session, has_device_cookie

        session = get_device_session(request, request.user)
        if has_device_cookie(request) and not session:
            raise ValidationError({"message": "当前设备登录已失效，请重新登录"})
        return request.user.username

    token = request.COOKIES.get("free_session", "").strip()
    try:
        payload = signing.loads(
            token,
            salt=FREE_SESSION_SALT,
            max_age=FREE_SESSION_MAX_AGE,
        )
    except (BadSignature, SignatureExpired):
        raise ValidationError({"message": "免费访客会话已失效，请重新进入"})

    sid = str(payload.get("sid", "")).strip()
    if len(sid) != 32 or not all(char in "0123456789abcdef" for char in sid):
        raise ValidationError({"message": "免费访客会话无效"})
    return f"{FREE_ACCOUNT_USERNAME}:{sid}"

def req_gateway(method, uri, *args, **kwargs):
    url = CHATGPT_GATEWAY_URL + uri
    headers = {
        "Authorization": "Bearer {}".format(GATEWAY_ADMIN_SECRET),
    }
    try:
        res = requests.request(method, url, headers=headers, *args, **kwargs, allow_redirects=False)
    except RequestException as e:
        raise ValidationError("请求异常, 网关服务未正常启用")

    if res.status_code != 200:
        try:
            err_msg = res.json()
        except:
            err_msg = res.text

        request_json = kwargs.get("json") or {}
        request_secrets = []
        if isinstance(request_json, dict):
            for key, value in request_json.items():
                if any(fragment in str(key).lower() for fragment in SENSITIVE_KEY_FRAGMENTS):
                    if isinstance(value, (list, tuple)):
                        request_secrets.extend(value)
                    else:
                        request_secrets.append(value)
        raise ValidationError(redact_sensitive_data(err_msg, secrets=request_secrets))

    return res.json()


def clean_int_list(data_list):
    if isinstance(data_list, str):
        data_list = json.loads(data_list)

    new_list = []
    for i in data_list:
        if isinstance(i, int):
            new_list.append(i)
        elif isinstance(i, str) and i.isdigit():
            new_list.append(int(i))

    return new_list


def save_visit_log(request, log_type, chatgpt_username=None):

    VisitLog.save_data({
        "ip": get_client_ip(request),
        "log_type": log_type,
        "chatgpt_username": chatgpt_username,
        "username": request.user.username,
        "created_at": int(time.time()),
        "user_agent": request.headers.get('User-Agent', ''),
    })

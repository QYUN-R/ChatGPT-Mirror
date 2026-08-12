import os
from pathlib import Path

DEBUG = False

FREE_ACCOUNT_USERNAME = "free_account"

def required_env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise RuntimeError(f"{key} must be set in production")
    return value


ADMIN_USERNAME = required_env("ADMIN_USERNAME")
ADMIN_PASSWORD = required_env("ADMIN_PASSWORD")
GATEWAY_ADMIN_SECRET = required_env("GATEWAY_ADMIN_SECRET")
CHATGPT_GATEWAY_URL = required_env("CHATGPT_GATEWAY_URL")
ALLOW_REGISTER = os.environ.get("ALLOW_REGISTER", "true") == "true"
SHOW_GITHUB = os.environ.get("SHOW_GITHUB", "true") == "true"

TURNSTILE_MODE = os.environ.get("CLOUDFLARE_TURNSTILE", "local").strip().lower()
if TURNSTILE_MODE not in {"enable", "local", "disable"}:
    raise RuntimeError("CLOUDFLARE_TURNSTILE must be enable, local, or disable")
TURNSTILE_ENABLED = TURNSTILE_MODE == "enable"
LOCAL_CAPTCHA_ENABLED = TURNSTILE_MODE == "local"
TURNSTILE_SITE_KEY = os.environ.get("CLOUDFLARE_TURNSTILE_SITE_KEY", "").strip()
TURNSTILE_SECRET_KEY = os.environ.get("CLOUDFLARE_TURNSTILE_SECRET_KEY", "").strip()
if TURNSTILE_ENABLED and (not TURNSTILE_SITE_KEY or not TURNSTILE_SECRET_KEY):
    raise RuntimeError(
        "CLOUDFLARE_TURNSTILE_SITE_KEY and CLOUDFLARE_TURNSTILE_SECRET_KEY "
        "must be set when CLOUDFLARE_TURNSTILE=enable"
    )

BASE_DIR = Path(__file__).resolve().parent.parent
log_file_path = os.path.join(BASE_DIR, os.pardir, 'logs/cron.log > /dev/null 2>&1')

CRONJOBS = [
    ('*/5 * * * *', 'app.cron.check_access_token', f'>> {log_file_path}'),
    ('*/5 * * * *', 'app.cron.update_access_token', f'>> {log_file_path}'),
]

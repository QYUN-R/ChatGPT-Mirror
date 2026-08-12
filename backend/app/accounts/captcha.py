import base64
import hashlib
import hmac
import io
import secrets
import time
from functools import lru_cache

from django.core import signing
from django.core.cache import cache
from PIL import Image, ImageDraw, ImageFont
from rest_framework.exceptions import ValidationError


CAPTCHA_SALT = "accounts.local-captcha.v1"
CAPTCHA_ALPHABET = "2346789ACDEFGHJKMNPQRTUVWXY"
CAPTCHA_LENGTH = 6
CAPTCHA_TTL_SECONDS = 120
CAPTCHA_WIDTH = 192
CAPTCHA_HEIGHT = 64
CAPTCHA_GLYPH_WIDTH = 38
CAPTCHA_GLYPH_HEIGHT = 48


def _answer_digest(answer):
    normalized = str(answer or "").strip().upper()
    return hmac.new(
        signing.settings.SECRET_KEY.encode("utf-8"),
        normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


@lru_cache(maxsize=8)
def _load_font(size):
    for path in (
        "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _challenge_cache_key(nonce):
    fingerprint = hashlib.sha256(str(nonce).encode("utf-8")).hexdigest()
    return f"local-captcha:challenge:{fingerprint}"


def _used_cache_key(nonce):
    fingerprint = hashlib.sha256(str(nonce).encode("utf-8")).hexdigest()
    return f"local-captcha:used:{fingerprint}"


def _glyph_layout(length, rng):
    for _ in range(12):
        y_positions = [rng.randint(1, 15) for _ in range(length)]
        rotations = [rng.randint(-16, 16) for _ in range(length)]
        if max(y_positions) - min(y_positions) >= 10 and len(set(rotations)) >= 3:
            break

    max_x = CAPTCHA_WIDTH - CAPTCHA_GLYPH_WIDTH
    edge_margin = 2
    pitch = (max_x - edge_margin * 2) / max(length - 1, 1)
    return [
        {
            "x": max(
                0,
                min(
                    max_x,
                    round(edge_margin + index * pitch) + rng.randint(-5, 5),
                ),
            ),
            "y": y_positions[index],
            "rotation": rotations[index],
            "font_size": rng.randint(24, 28),
        }
        for index in range(length)
    ]


def _png_data_url(code):
    rng = secrets.SystemRandom()
    width, height = CAPTCHA_WIDTH, CAPTCHA_HEIGHT
    image = Image.new("RGB", (width, height), "#f2f2ef")
    draw = ImageDraw.Draw(image)

    for _ in range(22):
        x = rng.randint(3, width - 4)
        y = rng.randint(3, height - 4)
        draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill="#c3c3bd")

    for _ in range(3):
        draw.line(
            (
                rng.randint(0, width),
                rng.choice((rng.randint(3, 11), rng.randint(height - 12, height - 4))),
                rng.randint(0, width),
                rng.choice((rng.randint(3, 11), rng.randint(height - 12, height - 4))),
            ),
            fill="#b0b0aa",
            width=1,
        )

    layout = _glyph_layout(len(code), rng)
    for char, position in zip(code, layout):
        glyph = Image.new(
            "RGBA",
            (CAPTCHA_GLYPH_WIDTH, CAPTCHA_GLYPH_HEIGHT),
            (255, 255, 255, 0),
        )
        glyph_draw = ImageDraw.Draw(glyph)
        font = _load_font(position["font_size"])
        bounds = glyph_draw.textbbox((0, 0), char, font=font)
        text_width = bounds[2] - bounds[0]
        text_height = bounds[3] - bounds[1]
        glyph_draw.text(
            (
                (CAPTCHA_GLYPH_WIDTH - text_width) / 2 - bounds[0],
                (CAPTCHA_GLYPH_HEIGHT - text_height) / 2 - bounds[1],
            ),
            char,
            font=font,
            fill=(58, 58, 54, 218),
        )
        glyph = glyph.rotate(
            position["rotation"],
            resample=Image.Resampling.BICUBIC,
            expand=False,
        )
        image.paste(
            glyph,
            (position["x"], position["y"]),
            glyph,
        )

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def issue_local_captcha(action):
    action = str(action or "").strip()
    code = "".join(secrets.choice(CAPTCHA_ALPHABET) for _ in range(CAPTCHA_LENGTH))
    nonce = secrets.token_urlsafe(32)
    cache.set(
        _challenge_cache_key(nonce),
        {
            "action": action,
            "answer": _answer_digest(code),
            "issued_at": int(time.time()),
        },
        timeout=CAPTCHA_TTL_SECONDS,
    )
    payload = {
        "action": action,
        "nonce": nonce,
        "issued_at": int(time.time()),
    }
    return {
        "captcha_token": signing.dumps(payload, salt=CAPTCHA_SALT, compress=True),
        "image_data_url": _png_data_url(code),
        "expires_in": CAPTCHA_TTL_SECONDS,
    }


def inspect_local_captcha_token(token, *, max_age=CAPTCHA_TTL_SECONDS):
    return signing.loads(str(token or ""), salt=CAPTCHA_SALT, max_age=max_age)


def verify_local_captcha(token, answer, expected_action, *, max_age=CAPTCHA_TTL_SECONDS):
    token = str(token or "").strip()
    answer = str(answer or "").strip().upper()
    if not token or not answer:
        raise ValidationError({"message": "请输入图形验证码"})

    try:
        payload = inspect_local_captcha_token(token, max_age=max_age)
    except signing.SignatureExpired as exc:
        raise ValidationError({"message": "图形验证码已过期，请刷新后重试"}) from exc
    except signing.BadSignature as exc:
        raise ValidationError({"message": "图形验证码无效，请刷新后重试"}) from exc

    nonce = str(payload.get("nonce") or "")
    if not nonce or not cache.add(_used_cache_key(nonce), 1, timeout=max_age):
        raise ValidationError({"message": "图形验证码已使用，请刷新后重试"})

    challenge_key = _challenge_cache_key(nonce)
    challenge = cache.get(challenge_key)
    cache.delete(challenge_key)
    if not isinstance(challenge, dict):
        raise ValidationError({"message": "图形验证码已过期或已使用，请刷新后重试"})

    if (
        payload.get("action") != expected_action
        or challenge.get("action") != expected_action
        or not hmac.compare_digest(
            str(challenge.get("answer") or ""),
            _answer_digest(answer),
        )
    ):
        raise ValidationError({"message": "图形验证码错误，请刷新后重试"})

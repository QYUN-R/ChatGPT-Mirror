import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from app.billing.exceptions import BillingError
from app.billing.models import (
    CommercialSettings,
    OrderStatus,
    PlanOffer,
    RedemptionCode,
    RedemptionCodeBatch,
    RedemptionCodeStatus,
)
from app.billing.payment import PaymentEvent
from app.billing.services import audit, complete_order, create_order


CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_PAYLOAD_LENGTH = 28
CODE_PATTERN = re.compile(rf"^TWG[{CODE_ALPHABET}]{{{CODE_PAYLOAD_LENGTH}}}$")


def redemption_available():
    if not settings.REDEMPTION_CODES_ENABLED:
        return False
    commercial = CommercialSettings.objects.filter(pk=1).only("redemption_enabled").first()
    return bool(commercial and commercial.redemption_enabled)


def normalize_redemption_code(value):
    normalized = re.sub(r"[-\s]", "", str(value or "")).upper()
    if not CODE_PATTERN.fullmatch(normalized):
        raise BillingError("卡密无效或已使用", code="redemption_code_invalid")
    return normalized


def format_redemption_code(normalized):
    payload = normalized[3:]
    return "TWG-" + "-".join(payload[index:index + 4] for index in range(0, len(payload), 4))


def _decode_key(value):
    try:
        key = base64.b64decode(str(value), validate=True)
    except (binascii.Error, ValueError, TypeError):
        raise BillingError("卡密服务密钥配置无效", code="redemption_keyring_invalid")
    if len(key) < 32:
        raise BillingError("卡密服务密钥长度不足", code="redemption_keyring_invalid")
    return key


def load_redemption_keyring():
    payload = settings.REDEMPTION_CODE_KEYRING
    if payload is None:
        try:
            payload = json.loads(Path(settings.REDEMPTION_CODE_KEYRING_PATH).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raise BillingError("卡密服务密钥未配置", code="redemption_keyring_unavailable")
    if not isinstance(payload, dict) or not isinstance(payload.get("keys"), dict):
        raise BillingError("卡密服务密钥配置无效", code="redemption_keyring_invalid")
    active = str(payload.get("active") or "").strip()
    keys = {
        str(version).strip(): _decode_key(value)
        for version, value in payload["keys"].items()
        if str(version).strip()
    }
    if not active or active not in keys:
        raise BillingError("卡密服务缺少当前密钥", code="redemption_keyring_invalid")
    return active, keys


def redemption_digest(normalized, key):
    return hmac.new(key, normalized.encode("ascii"), hashlib.sha256).hexdigest()


def generate_plaintext_code():
    normalized = "TWG" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_PAYLOAD_LENGTH))
    return normalized, format_redemption_code(normalized)


def _next_batch_no():
    for _ in range(20):
        candidate = f"RC{timezone.now():%Y%m%d%H%M%S}{secrets.token_hex(3).upper()}"
        if not RedemptionCodeBatch.objects.filter(batch_no=candidate).exists():
            return candidate
    raise BillingError("无法生成唯一批次号，请重试", code="batch_number_unavailable")


@transaction.atomic
def create_redemption_batch(*, offer, quantity, expires_at, note, actor, ip_address=None):
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise BillingError("单批卡密数量必须在 1 到 1000 之间", code="invalid_batch_quantity")
    if quantity < 1 or quantity > 1000:
        raise BillingError("单批卡密数量必须在 1 到 1000 之间", code="invalid_batch_quantity")
    offer = PlanOffer.objects.select_related("plan", "plan__pool").get(pk=offer.pk)
    if offer.is_archived or offer.is_draft or offer.plan.is_archived or not offer.plan.is_active:
        raise BillingError("该价格方案不能生成卡密", code="offer_unavailable")
    if expires_at and expires_at <= timezone.now():
        raise BillingError("卡密失效时间必须晚于当前时间", code="invalid_batch_expiry")

    active_version, keys = load_redemption_keyring()
    batch = RedemptionCodeBatch.objects.create(
        batch_no=_next_batch_no(),
        plan=offer.plan,
        offer=offer,
        plan_snapshot={
            "id": offer.plan_id,
            "code": offer.plan.code,
            "name": offer.plan.name,
            "tagline": offer.plan.tagline,
            "pool_tier": offer.plan.pool_tier,
        },
        offer_snapshot={
            "id": offer.id,
            "code": offer.code,
            "name": offer.name,
            "months": offer.months,
        },
        entitlement_months=offer.months,
        price_cents=offer.price_cents,
        currency=offer.currency,
        quantity=quantity,
        expires_at=expires_at,
        note=str(note or "").strip()[:240],
        created_by=actor,
    )

    records = []
    plaintext_codes = []
    for index in range(1, quantity + 1):
        normalized, plaintext = generate_plaintext_code()
        serial_no = f"{batch.batch_no}-{index:04d}"
        records.append(
            RedemptionCode(
                batch=batch,
                serial_no=serial_no,
                key_version=active_version,
                code_digest=redemption_digest(normalized, keys[active_version]),
                code_mask=f"TWG-****-****-****-****-****-****-{normalized[-4:]}",
            )
        )
        plaintext_codes.append({"serial_no": serial_no, "code": plaintext})
    RedemptionCode.objects.bulk_create(records)
    audit(
        "redemption.batch_created",
        batch,
        actor=actor,
        ip_address=ip_address,
        detail={
            "batch_no": batch.batch_no,
            "offer_id": offer.id,
            "quantity": quantity,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "key_version": active_version,
        },
    )
    return batch, plaintext_codes


def _identifier(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _failure_subjects(user, ip_address):
    subjects = [("user", _identifier(user.pk))]
    if ip_address:
        subjects.append(("ip", _identifier(ip_address)))
    return subjects


def assert_redemption_not_locked(user, ip_address):
    for kind, digest in _failure_subjects(user, ip_address):
        if cache.get(f"redemption:lock:{kind}:{digest}"):
            raise BillingError(
                "卡密尝试次数过多，请稍后再试",
                code="redemption_temporarily_locked",
            )


def record_redemption_failure(user, ip_address):
    limit = max(int(settings.REDEMPTION_FAILURE_LIMIT), 1)
    window = max(int(settings.REDEMPTION_FAILURE_WINDOW_SECONDS), 60)
    lock_seconds = max(int(settings.REDEMPTION_LOCK_SECONDS), 60)
    for kind, digest in _failure_subjects(user, ip_address):
        count_key = f"redemption:fail:{kind}:{digest}"
        cache.add(count_key, 0, timeout=window)
        try:
            count = cache.incr(count_key)
        except ValueError:
            cache.set(count_key, 1, timeout=window)
            count = 1
        if count >= limit:
            cache.set(f"redemption:lock:{kind}:{digest}", True, timeout=lock_seconds)


def clear_redemption_failures(user, ip_address):
    keys = []
    for kind, digest in _failure_subjects(user, ip_address):
        keys.extend((f"redemption:fail:{kind}:{digest}", f"redemption:lock:{kind}:{digest}"))
    cache.delete_many(keys)


def _invalid_code(user, ip_address):
    record_redemption_failure(user, ip_address)
    raise BillingError("卡密无效或已使用", code="redemption_code_invalid")


def _find_code_for_update(normalized, keys):
    condition = Q(pk__isnull=True)
    for version, key in keys.items():
        condition |= Q(key_version=version, code_digest=redemption_digest(normalized, key))
    # Lock only the code row here. PostgreSQL rejects FOR UPDATE when the query
    # joins nullable order/subscription relations; the batch is locked next.
    return RedemptionCode.objects.select_for_update().filter(condition).first()


def lookup_redemption_code(plaintext_code):
    normalized = normalize_redemption_code(plaintext_code)
    _, keys = load_redemption_keyring()
    condition = Q(pk__isnull=True)
    for version, key in keys.items():
        condition |= Q(key_version=version, code_digest=redemption_digest(normalized, key))
    code = (
        RedemptionCode.objects.select_related(
            "batch",
            "batch__plan",
            "batch__offer",
            "redeemed_by",
            "order",
            "subscription",
        )
        .filter(condition)
        .first()
    )
    if code is None:
        raise BillingError("卡密不存在", code="redemption_code_not_found")
    return code


@transaction.atomic
def set_redemption_batch_state(*, batch, actor, is_active=None, archive=False, ip_address=None):
    batch = RedemptionCodeBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.is_archived and not archive:
        raise BillingError("已归档批次不能修改启用状态", code="redemption_batch_archived")
    action = "redemption.batch_updated"
    if archive:
        batch.is_archived = True
        batch.is_active = False
        action = "redemption.batch_archived"
    elif is_active is not None:
        batch.is_active = bool(is_active)
        action = "redemption.batch_enabled" if batch.is_active else "redemption.batch_disabled"
    batch.save(update_fields=["is_active", "is_archived", "updated_at"])
    audit(
        action,
        batch,
        actor=actor,
        ip_address=ip_address,
        detail={"batch_no": batch.batch_no, "is_active": batch.is_active, "is_archived": batch.is_archived},
    )
    return batch


def _validated_code_ids(code_ids):
    try:
        result = sorted({int(value) for value in code_ids})
    except (TypeError, ValueError):
        raise BillingError("卡密 ID 列表无效", code="invalid_redemption_code_ids")
    if not result or len(result) > 1000:
        raise BillingError("每次必须选择 1 到 1000 张卡密", code="invalid_redemption_code_ids")
    return result


@transaction.atomic
def revoke_redemption_codes(*, code_ids, actor, ip_address=None):
    code_ids = _validated_code_ids(code_ids)
    codes = list(RedemptionCode.objects.select_for_update().filter(pk__in=code_ids))
    if len(codes) != len(code_ids) or any(
        code.status != RedemptionCodeStatus.AVAILABLE or code.is_archived for code in codes
    ):
        raise BillingError("只能撤销未使用且未归档的卡密", code="redemption_revoke_not_allowed")
    now = timezone.now()
    for code in codes:
        code.status = RedemptionCodeStatus.REVOKED
        code.revoked_at = now
        code.revoked_by = actor
        code.updated_at = now
    RedemptionCode.objects.bulk_update(codes, ["status", "revoked_at", "revoked_by", "updated_at"])
    audit(
        "redemption.codes_revoked",
        actor=actor,
        ip_address=ip_address,
        target_type="RedemptionCode",
        target_id=",".join(str(code.id) for code in codes[:20]),
        detail={"count": len(codes), "serial_numbers": [code.serial_no for code in codes[:100]]},
    )
    return len(codes)


@transaction.atomic
def archive_redeemed_codes(*, code_ids, actor, ip_address=None):
    code_ids = _validated_code_ids(code_ids)
    codes = list(RedemptionCode.objects.select_for_update().filter(pk__in=code_ids))
    if len(codes) != len(code_ids) or any(
        code.status != RedemptionCodeStatus.REDEEMED or code.is_archived for code in codes
    ):
        raise BillingError("只能归档已使用且未归档的卡密", code="redemption_archive_not_allowed")
    now = timezone.now()
    for code in codes:
        code.is_archived = True
        code.archived_at = now
        code.archived_by = actor
        code.updated_at = now
    RedemptionCode.objects.bulk_update(codes, ["is_archived", "archived_at", "archived_by", "updated_at"])
    audit(
        "redemption.codes_archived",
        actor=actor,
        ip_address=ip_address,
        target_type="RedemptionCode",
        target_id=",".join(str(code.id) for code in codes[:20]),
        detail={"count": len(codes), "serial_numbers": [code.serial_no for code in codes[:100]]},
    )
    return len(codes)


@transaction.atomic
def restore_archived_codes(*, code_ids, actor, ip_address=None):
    code_ids = _validated_code_ids(code_ids)
    codes = list(RedemptionCode.objects.select_for_update().filter(pk__in=code_ids))
    if len(codes) != len(code_ids) or any(not code.is_archived for code in codes):
        raise BillingError("只能恢复已归档的卡密记录", code="redemption_restore_not_allowed")
    now = timezone.now()
    for code in codes:
        code.is_archived = False
        code.archived_at = None
        code.archived_by = None
        code.updated_at = now
    RedemptionCode.objects.bulk_update(codes, ["is_archived", "archived_at", "archived_by", "updated_at"])
    audit(
        "redemption.codes_restored",
        actor=actor,
        ip_address=ip_address,
        target_type="RedemptionCode",
        target_id=",".join(str(code.id) for code in codes[:20]),
        detail={"count": len(codes), "serial_numbers": [code.serial_no for code in codes[:100]]},
    )
    return len(codes)


@transaction.atomic
def redeem_code(*, user, plaintext_code, ip_address, user_agent):
    if not redemption_available():
        raise BillingError("卡密兑换暂未开放", code="redemption_disabled")
    assert_redemption_not_locked(user, ip_address)
    try:
        normalized = normalize_redemption_code(plaintext_code)
    except BillingError:
        _invalid_code(user, ip_address)

    _, keys = load_redemption_keyring()
    User = get_user_model()
    user = User.objects.select_for_update().get(pk=user.pk)
    code = _find_code_for_update(normalized, keys)
    if code is None:
        _invalid_code(user, ip_address)
    batch = (
        RedemptionCodeBatch.objects.select_for_update()
        .select_related("plan", "offer", "offer__plan")
        .get(pk=code.batch_id)
    )
    code.batch = batch

    if code.status == RedemptionCodeStatus.REDEEMED:
        if code.redeemed_by_id == user.id and code.order_id and code.order.status == OrderStatus.PAID:
            clear_redemption_failures(user, ip_address)
            return code.order, code.subscription, True
        _invalid_code(user, ip_address)

    now = timezone.now()
    if (
        code.status != RedemptionCodeStatus.AVAILABLE
        or code.is_archived
        or code.batch.is_archived
        or not code.batch.is_active
        or (code.batch.expires_at and code.batch.expires_at <= now)
    ):
        _invalid_code(user, ip_address)

    order = create_order(
        user,
        batch.offer,
        provider="redemption_code",
        actor=user,
        metadata={
            "source": "redemption_code",
            "batch_no": batch.batch_no,
            "serial_no": code.serial_no,
        },
        allow_unavailable=True,
        plan_snapshot=batch.plan_snapshot,
        offer_snapshot=batch.offer_snapshot,
        entitlement_months=batch.entitlement_months,
        price_cents=batch.price_cents,
        currency=batch.currency,
        target_plan=batch.plan,
    )
    event = PaymentEvent(
        event_id=f"redemption:{code.serial_no}",
        event_type="PAYMENT_SUCCEEDED",
        provider_transaction_id=f"redemption:{code.serial_no}",
        amount_cents=order.price_cents,
        currency=order.currency,
        signature_verified=True,
        occurred_at=now,
        payload={"mode": "redemption_code", "serial_no": code.serial_no, "batch_no": batch.batch_no},
    )
    order, subscription = complete_order(order, event, actor=user, ip_address=ip_address or None)
    code.status = RedemptionCodeStatus.REDEEMED
    code.redeemed_by = user
    code.redeemed_email = (user.email or "").strip().lower()
    code.redeemed_at = now
    code.redeemed_ip = ip_address or None
    code.redeemed_user_agent = str(user_agent or "")[:512]
    code.order = order
    code.subscription = subscription
    code.save(update_fields=[
        "status",
        "redeemed_by",
        "redeemed_email",
        "redeemed_at",
        "redeemed_ip",
        "redeemed_user_agent",
        "order",
        "subscription",
        "updated_at",
    ])
    audit(
        "redemption.code_redeemed",
        code,
        actor=user,
        ip_address=ip_address or None,
        detail={
            "serial_no": code.serial_no,
            "batch_no": batch.batch_no,
            "order_no": order.order_no,
            "plan_code": batch.plan_snapshot.get("code"),
            "entitlement_months": batch.entitlement_months,
        },
    )
    clear_redemption_failures(user, ip_address)
    return order, subscription, False

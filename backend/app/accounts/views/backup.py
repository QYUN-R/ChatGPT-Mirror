import json
import time

from django.core import serializers as django_serializers
from django.db import transaction
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from app.accounts.models import User, VisitLog
from app.chatgpt.models import ChatgptAccount, ChatgptCar
from app.fields import decrypt_value, encrypt_value
from app.utils import req_gateway
from app.billing.models import (
    AccountAssignment,
    AccountAssignmentEvent,
    Announcement,
    AuditLog,
    CommercialSettings,
    Order,
    PaymentTransaction,
    Plan,
    PlanOffer,
    PoolAccountPolicy,
    PoolReservation,
    RedemptionCode,
    RedemptionCodeBatch,
    SupportContact,
    Subscription,
    UsageEvent,
    UserNotification,
)


BILLING_BACKUP_MODELS = (
    Plan,
    PlanOffer,
    Subscription,
    Order,
    PaymentTransaction,
    PoolAccountPolicy,
    PoolReservation,
    AccountAssignment,
    AccountAssignmentEvent,
    UsageEvent,
    Announcement,
    UserNotification,
    SupportContact,
    CommercialSettings,
    RedemptionCodeBatch,
    RedemptionCode,
    AuditLog,
)


class UnifiedBackupView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request):
        payload = {
            "version": 2,
            "created_at": int(time.time()),
            "users": [
                {
                    "username": user.username,
                    "password": user.password,
                    "is_active": user.is_active,
                    "is_staff": user.is_staff,
                    "is_superuser": user.is_superuser,
                    "remark": user.remark,
                    "isolated_session": user.isolated_session,
                    "gptcar_list": user.gptcar_list,
                    "model_limit": user.model_limit,
                    "expired_date": user.expired_date.isoformat() if user.expired_date else None,
                    "daily_quota": user.daily_quota,
                    "monthly_quota": user.monthly_quota,
                    "force_chat_mode": user.force_chat_mode,
                }
                for user in User.objects.all()
            ],
            "chatgpt_accounts": [
                {
                    "id": account.id,
                    "archived_at": account.archived_at.isoformat() if account.archived_at else None,
                    **{
                        field: getattr(account, field)
                        for field in (
                        "chatgpt_username", "auth_status", "plan_type", "access_token",
                        "session_token", "extra_cookies", "refresh_token", "refresh_client_id",
                        "access_token_valid", "session_token_valid", "proxy_node_id",
                        "last_check_at", "last_error", "remark", "is_archived",
                        "created_time", "updated_time",
                        )
                    },
                }
                for account in ChatgptAccount.objects.all()
            ],
            "chatgpt_cars": list(
                ChatgptCar.objects.values(
                    "id", "car_name", "remark", "gpt_account_list", "created_time", "updated_time"
                )
            ),
            "visit_logs": list(
                VisitLog.objects.values(
                    "id", "username", "chatgpt_username", "log_type",
                    "created_at", "ip", "user_agent",
                )
            ),
            "gateway": req_gateway("get", "/api/backup/export"),
            "billing": {
                model._meta.label: django_serializers.serialize("json", model.objects.all())
                for model in BILLING_BACKUP_MODELS
            },
        }
        archive = encrypt_value(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        response = Response({"archive": archive})
        response["Content-Disposition"] = (
            f'attachment; filename="chatgpt-mirror-backup-{payload["created_at"]}.json"'
        )
        return response

    def post(self, request):
        if request.data.get("confirm") != "RESTORE":
            raise ValidationError({"confirm": "恢复操作必须明确提交 RESTORE"})
        archive = str(request.data.get("archive") or "")
        try:
            payload = json.loads(decrypt_value(archive))
        except Exception:
            raise ValidationError({"archive": "备份文件无效或加密密钥不匹配"})
        if payload.get("version") not in (1, 2):
            raise ValidationError({"archive": "不支持的备份版本"})

        with transaction.atomic():
            for data in payload.get("chatgpt_accounts", []):
                account_id = data.pop("id")
                username = data.pop("chatgpt_username")
                ChatgptAccount.objects.update_or_create(
                    id=account_id, defaults={"chatgpt_username": username, **data}
                )
            for data in payload.get("chatgpt_cars", []):
                car_id = data.pop("id")
                name = data.pop("car_name")
                ChatgptCar.objects.update_or_create(
                    id=car_id, defaults={"car_name": name, **data}
                )
            for data in payload.get("users", []):
                username = data.pop("username")
                User.objects.update_or_create(username=username, defaults=data)
            for data in payload.get("visit_logs", []):
                log_id = data.pop("id")
                VisitLog.objects.update_or_create(id=log_id, defaults=data)
            for model in BILLING_BACKUP_MODELS:
                serialized = (payload.get("billing") or {}).get(model._meta.label)
                if not serialized:
                    continue
                for item in django_serializers.deserialize("json", serialized):
                    item.save()
            req_gateway("post", "/api/backup/restore", json=payload.get("gateway") or {})
        return Response({"message": "统一备份恢复完成；现有会话未恢复，请重新登录"})

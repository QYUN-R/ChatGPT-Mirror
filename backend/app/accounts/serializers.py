from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

from app.accounts.email_auth import normalize_email
from app.accounts.model_limits import normalize_model_limits
from app.accounts.models import User, VisitLog
from app.accounts.device_policy import effective_device_policy
from app.chatgpt.models import ChatgptAccount
from app.settings import ADMIN_USERNAME


class ShowVisitLogModelSerializer(serializers.ModelSerializer):
    is_protected = serializers.SerializerMethodField()

    def get_is_protected(self, obj):
        return obj.username == ADMIN_USERNAME and obj.log_type == "login"

    class Meta:
        model = VisitLog
        fields = "__all__"


class ShowUserAccountModelSerializer(serializers.ModelSerializer):
    last_login = serializers.DateTimeField(format="%Y-%m-%d %H:%M")
    date_joined = serializers.DateTimeField(format="%Y-%m-%d %H:%M")
    use_count = serializers.SerializerMethodField()
    chatgpt_count = serializers.SerializerMethodField()
    subscription = serializers.SerializerMethodField()
    email_verified = serializers.SerializerMethodField()
    device_policy = serializers.SerializerMethodField()
    active_device_count = serializers.SerializerMethodField()

    def __init__(self, *args, use_count_dict=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.use_count_dict = use_count_dict or {}

    def get_chatgpt_count(self, obj):
        return ChatgptAccount.get_by_gptcar_list(obj.gptcar_list).count()

    def get_use_count(self, obj):
        return self.use_count_dict.get(obj.username, 0)

    def get_subscription(self, obj):
        try:
            subscription = obj.billing_subscription
        except Exception:
            return None
        return {
            "id": subscription.id,
            "plan_name": subscription.plan.name,
            "status": subscription.status,
            "ends_at": subscription.ends_at,
        }

    def get_email_verified(self, obj):
        return bool(obj.email and obj.email_verified_at)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["model_limit"] = normalize_model_limits(instance.model_limit)
        return data

    def get_device_policy(self, obj):
        return effective_device_policy(obj)

    def get_active_device_count(self, obj):
        annotated = getattr(obj, "active_device_count", None)
        if annotated is not None:
            return int(annotated)
        from django.utils import timezone

        return obj.device_sessions.filter(
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).count()

    class Meta:
        model = User
        exclude = (
            "password", "is_superuser", "first_name", "last_name", "is_staff", "groups", "user_permissions")
        # fields = "__all__"


class ModelLimitListField(serializers.ListField):
    child = serializers.CharField(allow_blank=False, trim_whitespace=True)

    def to_internal_value(self, data):
        values = super().to_internal_value(data)
        return normalize_model_limits(values)


class AddUserAccountSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False)
    is_active = serializers.BooleanField()
    username = serializers.CharField(min_length=4)
    email = serializers.EmailField(required=False, allow_blank=True)
    password = serializers.CharField(required=False)
    gptcar_list = serializers.JSONField(default=list)
    model_limit = ModelLimitListField(required=False, default=list)
    remark = serializers.CharField(default="", allow_blank=True)
    isolated_session = serializers.BooleanField()
    expired_date = serializers.DateField(required=False, allow_null=True)
    daily_quota = serializers.IntegerField(required=False, min_value=0, default=0)
    monthly_quota = serializers.IntegerField(required=False, min_value=0, default=0)
    force_chat_mode = serializers.BooleanField(required=False)
    multi_device_enabled = serializers.BooleanField(required=False)
    device_policy_managed_by_plan = serializers.BooleanField(required=False)
    device_limit = serializers.IntegerField(required=False, min_value=1, max_value=50)
    new_device_verification_enabled = serializers.BooleanField(required=False)

    def validate_password(self, value):
        if not value:
            return value
        try:
            validate_password(
                value,
                User(username=str(self.initial_data.get("username") or "")),
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value


class BatchModelLimitSerializer(serializers.Serializer):
    user_id_list = serializers.ListField(child=serializers.IntegerField())
    model_limit = ModelLimitListField()


class UserBindChatGPTSerializer(serializers.Serializer):
    user_id_list = serializers.ListField(child=serializers.IntegerField())
    gptcar_id_list = serializers.ListField(child=serializers.IntegerField())


class BatchUserActionSerializer(serializers.Serializer):
    user_id_list = serializers.ListField(
        child=serializers.IntegerField(), min_length=1, max_length=200
    )
    action = serializers.ChoiceField(
        choices=[
            "activate",
            "deactivate",
            "delete",
            "device_follow_plan",
            "device_override",
        ]
    )
    multi_device_enabled = serializers.BooleanField(required=False)
    device_limit = serializers.IntegerField(required=False, min_value=1, max_value=50)
    new_device_verification_enabled = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if attrs["action"] == "device_override":
            missing = [
                name for name in ("multi_device_enabled", "device_limit")
                if name not in attrs
            ]
            if missing:
                raise serializers.ValidationError({missing[0]: "批量覆盖设备策略时必须填写此项"})
        return attrs


class DeviceLoginVerificationRequestSerializer(serializers.Serializer):
    device_ticket = serializers.CharField()


class DeviceLoginVerificationConfirmSerializer(DeviceLoginVerificationRequestSerializer):
    verification_code = serializers.CharField(min_length=6, max_length=6)


class UserRegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()
    verification_code = serializers.CharField(min_length=6, max_length=6)
    chatgpt_token = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        from django.conf import settings

        if not settings.BILLING_ENABLED and not (attrs.get("chatgpt_token") or "").strip():
            raise serializers.ValidationError({"chatgpt_token": "上游账号令牌不能为空"})
        return attrs

    def validate_email(self, value):
        return normalize_email(value)

    def validate_password(self, value):
        try:
            validate_password(
                value,
                User(username=str(self.initial_data.get("email") or "")),
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def validate_email(self, value):
        if not value:
            return ""
        return normalize_email(value)


class EmailVerificationRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return normalize_email(value)


class PasswordResetConfirmSerializer(EmailVerificationRequestSerializer):
    verification_code = serializers.CharField(min_length=6, max_length=6)
    new_password = serializers.CharField()

    def validate_new_password(self, value):
        try:
            validate_password(value, User(username=str(self.initial_data.get("email") or "")))
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value


class EmailBindingRequestSerializer(EmailVerificationRequestSerializer):
    binding_ticket = serializers.CharField()


class EmailBindingConfirmSerializer(serializers.Serializer):
    binding_ticket = serializers.CharField()
    verification_code = serializers.CharField(min_length=6, max_length=6)


class EmailChangeRequestSerializer(EmailVerificationRequestSerializer):
    current_password = serializers.CharField()


class EmailChangeConfirmSerializer(serializers.Serializer):
    verification_code = serializers.CharField(min_length=6, max_length=6)


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField()
    new_password = serializers.CharField()

    def validate_new_password(self, value):
        try:
            validate_password(value, self.context.get("user"))
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

from rest_framework import serializers

from app.chatgpt.models import ChatgptAccount, ChatgptCar
import jwt
from app.utils import clean_int_list, redact_sensitive_data
import time

class ShowGptCarSerializer(serializers.ModelSerializer):
    gpt_account_name_list = serializers.SerializerMethodField()

    def get_gpt_account_name_list(self, obj):
        gpt_account_list = clean_int_list(obj.gpt_account_list)
        resutls = ChatgptAccount.objects.filter(
            id__in=gpt_account_list,
            is_archived=False,
        ).values_list("chatgpt_username")
        return [i[0] for i in resutls]

    class Meta:
        model = ChatgptCar
        fields = "__all__"

class AddChatgptCarModelSerializer(serializers.ModelSerializer):

    def validate_empty_values(self, data):
        if not self.instance:
            data["created_time"] = int(time.time())

        data["updated_time"] = int(time.time())
        return (False, data)

    class Meta:
        model = ChatgptCar
        fields = "__all__"

class DeleteChatgptCarSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField())


class ShowChatgptTokenSerializer(serializers.ModelSerializer):
    access_token_exp = serializers.SerializerMethodField()
    use_count = serializers.SerializerMethodField()
    supported_login_modes = serializers.SerializerMethodField()
    has_refresh_token = serializers.SerializerMethodField()
    last_error = serializers.SerializerMethodField()
    pool_name = serializers.SerializerMethodField()
    pool_health_status = serializers.SerializerMethodField()
    active_bindings = serializers.SerializerMethodField()
    binding_limit = serializers.SerializerMethodField()
    deletion_requires_migration = serializers.SerializerMethodField()

    def __init__(self, *args, use_count_dict=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.use_count_dict = use_count_dict or {}

    def get_use_count(self, obj):
        return self.use_count_dict.get(obj.chatgpt_username, 0)

    def get_access_token_exp(self, obj):
        try:
            access_token_exp = jwt.decode(obj.access_token, options={"verify_signature": False})["exp"]
        except Exception:
            access_token_exp = 0
        return access_token_exp

    def get_supported_login_modes(self, obj):
        modes = []
        if obj.access_token_valid:
            modes.append("api")
        if obj.session_token_valid:
            modes.append("web")
        return modes

    def get_has_refresh_token(self, obj):
        return bool(obj.refresh_token)

    def get_last_error(self, obj):
        return redact_sensitive_data(
            obj.last_error or "",
            secrets=(obj.access_token, obj.session_token, obj.refresh_token),
        )

    @staticmethod
    def _policy(obj):
        try:
            return obj.billing_policy
        except Exception:
            return None

    def get_pool_name(self, obj):
        policy = self._policy(obj)
        return policy.pool.car_name if policy else ""

    def get_pool_health_status(self, obj):
        policy = self._policy(obj)
        return policy.health_status if policy else ""

    def get_active_bindings(self, obj):
        annotated_count = getattr(obj, "active_bindings_count", None)
        if annotated_count is not None:
            return annotated_count
        return obj.billing_assignments.filter(active=True).count()

    def get_binding_limit(self, obj):
        policy = self._policy(obj)
        return policy.binding_limit if policy else None

    def get_deletion_requires_migration(self, obj):
        policy = self._policy(obj)
        return bool(
            self.get_active_bindings(obj)
            and policy
            and policy.enabled
            and policy.health_status == "HEALTHY"
            and obj.auth_status
            and (obj.access_token_valid or obj.session_token_valid)
        )

    class Meta:
        model = ChatgptAccount
        fields = (
            "id",
            "chatgpt_username",
            "auth_status",
            "plan_type",
            "access_token_valid",
            "session_token_valid",
            "proxy_node_id",
            "last_check_at",
            "last_error",
            "remark",
            "is_archived",
            "archived_at",
            "created_time",
            "updated_time",
            "access_token_exp",
            "use_count",
            "supported_login_modes",
            "has_refresh_token",
            "pool_name",
            "pool_health_status",
            "active_bindings",
            "binding_limit",
            "deletion_requires_migration",
        )


class AddChatgptTokenSerializer(serializers.Serializer):
    auth_type = serializers.ChoiceField(choices=["cookie", "refresh_token"], default="cookie", required=False)
    chatgpt_token_list = serializers.ListField(child=serializers.CharField(allow_blank=True), required=False)
    client_id = serializers.CharField(required=False, allow_blank=True)
    refresh_token = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        auth_type = attrs.get("auth_type") or "cookie"
        if auth_type == "refresh_token":
            if not (attrs.get("client_id") or "").strip():
                raise serializers.ValidationError({"client_id": "client_id 不能为空"})
            if not (attrs.get("refresh_token") or "").strip():
                raise serializers.ValidationError({"refresh_token": "refresh_token 不能为空"})
        elif not attrs.get("chatgpt_token_list"):
            raise serializers.ValidationError({"chatgpt_token_list": "Token 列表不能为空"})
        return attrs

class CheckChatgptTokenExpirySerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(), required=False)

class RefreshChatgptTokenSerializer(serializers.Serializer):
    id = serializers.IntegerField()

class DeleteChatgptAccountSerializer(serializers.Serializer):
    chatgpt_username = serializers.CharField()
    migrate_users = serializers.BooleanField(required=False, default=False)

class UpdateChatgptInfoSerializer(serializers.Serializer):
    chatgpt_username = serializers.CharField()
    remark = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    proxy_node_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)


class ChatGPTLoginSerializer(serializers.Serializer):
    chatgpt_id = serializers.IntegerField(required=False, allow_null=True)
    login_mode = serializers.ChoiceField(choices=["api", "web"], default="web", required=False)

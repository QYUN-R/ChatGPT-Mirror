import time
from datetime import datetime

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.middleware.csrf import get_token, rotate_token
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from app.accounts.models import EmailVerificationChallenge, User, UserDeviceSession, VisitLog
from app.accounts.serializers import ShowVisitLogModelSerializer, AddUserAccountSerializer, UserBindChatGPTSerializer, \
    ShowUserAccountModelSerializer, BatchModelLimitSerializer, BatchUserActionSerializer, ChangePasswordSerializer
from app.accounts.authentication import (
    active_device_sessions,
    clear_auth_cookie,
    get_device_session,
    issue_device_session,
    revoke_all_device_sessions,
    set_auth_cookie,
)
from rest_framework.authtoken.models import Token
from app.chatgpt.models import ChatgptAccount, ChatgptCar
from app.page import DefaultPageNumberPagination
from app.settings import ADMIN_USERNAME
from app.utils import get_client_ip, get_request_subject, req_gateway
from app.accounts.email_auth import has_verified_email, issue_binding_ticket
from app.accounts.model_limits import normalize_model_limits
from app.accounts.views.login import issue_user_token
from app.billing.exceptions import BillingError
from app.billing.services import audit, has_managed_subscription, managed_account_catalog, resolve_managed_account
from app.accounts.device_policy import (
    effective_device_policy,
    enforce_device_policy_with_gateway,
)


def revoke_user_sessions(user):
    Token.objects.filter(user=user).delete()
    subjects = revoke_all_device_sessions(user)
    for subject in subjects or [user.username]:
        try:
            req_gateway("post", "/api/logout", json={"user_name": subject})
        except ValidationError:
            pass


def quota_snapshot(user, request=None):
    now = timezone.now()
    day_start = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    month_start = int(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    daily_used = VisitLog.objects.filter(
        username=user.username, log_type="proxy", created_at__gte=day_start
    ).count()
    monthly_used = VisitLog.objects.filter(
        username=user.username, log_type="proxy", created_at__gte=month_start
    ).count()
    try:
        remote = req_gateway("post", "/api/get-user-quota-usage", json={
            "user_name": get_request_subject(request) if request else get_request_subject_from_user(user),
            "day_start": day_start,
            "month_start": month_start,
        })
        daily_used = int(remote.get("daily_used", daily_used))
        monthly_used = int(remote.get("monthly_used", monthly_used))
    except ValidationError:
        pass
    return {
        "daily": {"limit": user.daily_quota, "used": daily_used},
        "monthly": {"limit": user.monthly_quota, "used": monthly_used},
    }


def get_request_subject_from_user(user):
    return user.username


def normalized_model_limits(user):
    return normalize_model_limits(user.model_limit)


class GetMirrorToken(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        user = request.user
        try:
            managed_account = resolve_managed_account(user)
        except BillingError as exc:
            raise ValidationError({"message": exc.message, "code": exc.code})
        user_gpt_list = (
            [managed_account]
            if managed_account
            else ChatgptAccount.get_by_gptcar_list(user.gptcar_list)
        )
        chatgpt_username_list = [i.chatgpt_username for i in user_gpt_list]
        res = req_gateway("post", "/api/get-mirror-token", json={
            "isolated_session": user.isolated_session,
            "limits": normalized_model_limits(user),
            "chatgpt_list": chatgpt_username_list,
            "user_name": get_request_subject(request),
            "daily_quota": user.daily_quota,
            "monthly_quota": user.monthly_quota,
            "force_chat_mode": user.force_chat_mode,
        })
        for line in res:
            original_username = line.get("chatgpt_username")
            obj = ChatgptAccount.objects.filter(chatgpt_username=original_username).first()
            if obj:
                line["auth_status"] = obj.auth_status
                line["plan_type"] = obj.plan_type
            if managed_account:
                line["chatgpt_username"] = "套餐专属账号"
                line["managed_assignment"] = True
        return Response(res)


class UserChatGPTAccountList(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        results = []
        try:
            managed_assignment, managed_policies, managed = managed_account_catalog(request.user)
        except BillingError as exc:
            raise ValidationError({"message": exc.message, "code": exc.code})
        user_gpt_list = (
            [
                policy.account
                for policy in managed_policies
                if (
                    policy.enabled and policy.health_status == "HEALTHY"
                    or managed_assignment and policy.account_id == managed_assignment.account_id
                )
            ]
            if managed
            else list(ChatgptAccount.get_by_gptcar_list(request.user.gptcar_list))
        )
        for account in user_gpt_list:
            try:
                account.refresh_auth_diagnostics()
            except Exception:
                pass
            account.refresh_from_db()
            policy = next((item for item in managed_policies if item.account_id == account.id), None)
            if policy and policy.health_status != "HEALTHY" and account.auth_status:
                account.auth_status = False
        chatgpt_list = [i.chatgpt_username for i in user_gpt_list]

        try:
            use_count_dict = req_gateway("post", "/api/get-chatgpt-use-count", json={"chatgpt_list": chatgpt_list})
        except:
            use_count_dict = {}

        current_minute = datetime.now().minute

        visible_accounts = user_gpt_list if managed else (
            [i for i in user_gpt_list if i.auth_status] or user_gpt_list
        )
        policy_by_account_id = {policy.account_id: policy for policy in managed_policies}
        for index, line in enumerate(visible_accounts, start=1):
            gpt_use_count_dict = use_count_dict.get(line.chatgpt_username, {}).get("gpt-4o", {})
            last_3h_use_count = (gpt_use_count_dict.get("last_1h", 0) +
                          gpt_use_count_dict.get("last_2h", 0) + gpt_use_count_dict.get("last_3h", 0) +
                          gpt_use_count_dict.get("last_4h", 0) * (1 - current_minute / 60))
            supported_login_modes = []
            if line.access_token_valid:
                supported_login_modes.append("api")
            if line.session_token_valid:
                supported_login_modes.append("web")
            results.append({
                "id": policy_by_account_id[line.id].id if managed else line.id,
                "use_count": last_3h_use_count,
                "chatgpt_flag": f"套餐账号 {index:02d}" if managed else "{:03}{}".format(line.id, line.chatgpt_username[:3]),
                "plan_type": line.plan_type,
                "auth_status": line.auth_status,
                "access_token_valid": line.access_token_valid,
                "session_token_valid": line.session_token_valid,
                "supported_login_modes": supported_login_modes,
                "default_login_mode": "web",
                "managed_assignment": managed,
                "is_current": bool(managed_assignment and managed_assignment.account_id == line.id),
                "health_status": policy_by_account_id[line.id].health_status if managed else (
                    "HEALTHY" if line.auth_status and supported_login_modes else "DEGRADED"
                ),
                "last_error": str(line.last_error or ""),
            })

        return Response({"results": results, "managed_assignment": managed})


class BatchModelLimit(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def post(self, request):
        serializer = BatchModelLimitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        User.objects.filter(id__in=serializer.data["user_id_list"]).update(model_limit=serializer.data["model_limit"])
        return Response({"message": "更新成功"})


class MirrorProxyConfigView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request):
        return Response(req_gateway("get", "/api/mirror-proxy-config"))

    def post(self, request):
        enabled = request.data.get("enabled")
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() in ("1", "true", "yes", "on")
        return Response(req_gateway("post", "/api/mirror-proxy-config", json={
            "enabled": bool(enabled),
            "proxy_url": request.data.get("proxy_url"),
            "username": request.data.get("username"),
            "password": request.data.get("password"),
            "nodes": request.data.get("nodes") or [],
        }))


class MirrorProxyTestView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def post(self, request):
        enabled = request.data.get("enabled")
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() in ("1", "true", "yes", "on")
        return Response(req_gateway("post", "/api/test-mirror-proxy-config", json={
            "enabled": bool(enabled),
            "proxy_url": request.data.get("proxy_url"),
            "username": request.data.get("username"),
            "password": request.data.get("password"),
            "nodes": request.data.get("nodes") or [],
        }))


class CustomScriptConfigView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request):
        return Response(req_gateway("get", "/api/custom-scripts"))

    def post(self, request):
        return Response(req_gateway("post", "/api/custom-scripts", json={
            "scripts": request.data.get("scripts") or [],
        }))


class UserRelateGPTCarView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def post(self, request, *args, **kwargs):
        serializer = UserBindChatGPTSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_ids = serializer.validated_data["user_id_list"]
        pool_ids = list(dict.fromkeys(serializer.validated_data["gptcar_id_list"]))
        if ChatgptCar.objects.filter(id__in=pool_ids, is_commercial=True).exists():
            raise ValidationError({"message": "商业号池由套餐自动管理，不能通过旧批量绑定接口分配"})
        if ChatgptCar.objects.filter(id__in=pool_ids).count() != len(pool_ids):
            raise ValidationError({"message": "部分账号池不存在"})

        with transaction.atomic():
            users = list(User.objects.select_for_update().filter(id__in=user_ids))
            if len(users) != len(set(user_ids)):
                raise ValidationError({"message": "部分用户不存在"})
            managed_users = [user.username for user in users if has_managed_subscription(user)]
            if managed_users:
                raise ValidationError({"message": "套餐用户的商业号池由套餐自动管理，不能手动绑定"})
            for user in users:
                user.gptcar_list = pool_ids
                user.save(update_fields=["gptcar_list"])

        return Response({"message": "绑定成功"})


class UserAccountView(generics.ListCreateAPIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request, *args, **kwargs):
        queryset = (
            User.objects.select_related("billing_subscription__plan")
            .annotate(
                active_device_count=Count(
                    "device_sessions",
                    filter=Q(
                        device_sessions__revoked_at__isnull=True,
                        device_sessions__expires_at__gt=timezone.now(),
                    ),
                    distinct=True,
                )
            )
            .order_by("-id")
        )
        query = str(request.query_params.get("q") or "").strip()
        if query:
            queryset = queryset.filter(
                Q(username__icontains=query) | Q(email__icontains=query) | Q(remark__icontains=query)
            )
        status = request.query_params.get("status")
        if status in ("active", "inactive"):
            queryset = queryset.filter(is_active=status == "active")
        pg = DefaultPageNumberPagination()
        pg.page_size_query_param = "page_size"
        page_accounts = pg.paginate_queryset(queryset, request=request)
        username_list = [i.username for i in page_accounts]
        try:
            use_count_dict = req_gateway("post", "/api/get-user-use-count", json={"username_list": username_list})
        except:
            use_count_dict = {}
        serializer = ShowUserAccountModelSerializer(instance=page_accounts, use_count_dict=use_count_dict, many=True)
        return pg.get_paginated_response(serializer.data)

    def post(self, request, *args, **kwargs):
        # 添加或更新用户
        serializer = AddUserAccountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if serializer.data["username"] == ADMIN_USERNAME:
            raise ValidationError({"message": "管理员账号不能操作"})

        user = User.objects.filter(username=serializer.data["username"]).first()
        created = user is None
        if created:
            if not serializer.data.get("password"):
                raise ValidationError({"password": "新增用户必须设置密码"})
            user = User(username=serializer.data["username"])

        requested_email = serializer.validated_data.get("email")
        email_changed = requested_email is not None and requested_email != user.email
        if email_changed and requested_email:
            email_in_use = User.objects.filter(
                Q(email__iexact=requested_email) | Q(username__iexact=requested_email)
            ).exclude(pk=user.pk).exists()
            if email_in_use:
                raise ValidationError({"email": "该邮箱已被其他账户使用"})

        if serializer.data.get("password"):
            user.set_password(serializer.data["password"])

        if email_changed:
            user.email = requested_email
            user.email_verified_at = None

        if "expired_date" in serializer.validated_data:
            user.expired_date = serializer.validated_data["expired_date"]

        user.gptcar_list = [] if has_managed_subscription(user) else serializer.data["gptcar_list"]
        user.is_active = serializer.data["is_active"]
        user.model_limit = serializer.data["model_limit"]
        user.isolated_session = serializer.data["isolated_session"]
        user.remark = serializer.data["remark"]
        user.daily_quota = serializer.data.get("daily_quota", 0)
        user.monthly_quota = serializer.data.get("monthly_quota", 0)
        previous_policy = effective_device_policy(user) if not created else None
        user.multi_device_enabled = serializer.validated_data.get(
            "multi_device_enabled", user.multi_device_enabled
        )
        user.device_policy_managed_by_plan = serializer.validated_data.get(
            "device_policy_managed_by_plan", user.device_policy_managed_by_plan
        )
        user.device_limit = serializer.validated_data.get("device_limit", user.device_limit)
        user.new_device_verification_enabled = serializer.validated_data.get(
            "new_device_verification_enabled", user.new_device_verification_enabled
        )
        if "force_chat_mode" in serializer.validated_data:
            user.force_chat_mode = serializer.validated_data["force_chat_mode"]
        try:
            user.save()
        except IntegrityError as exc:
            if email_changed:
                raise ValidationError({"email": "该邮箱已被其他账户使用"}) from exc
            raise

        if email_changed:
            EmailVerificationChallenge.objects.filter(
                user=user,
                consumed_at__isnull=True,
                invalidated_at__isnull=True,
                locked_at__isnull=True,
            ).update(invalidated_at=timezone.now())
            audit(
                "user.email_changed",
                user,
                actor=request.user,
                detail={"verification_reset": True},
                ip_address=get_client_ip(request),
            )

        if "force_chat_mode" in serializer.validated_data:
            try:
                req_gateway("post", "/api/user-work-mode", json={
                    "user_name": user.username,
                    "force_chat_mode": user.force_chat_mode,
                })
            except ValidationError:
                pass

        credentials_changed = bool(serializer.data.get("password")) or email_changed
        access_revoked = not user.is_active or (
            user.expired_date and user.expired_date <= timezone.localdate()
        )
        if credentials_changed or access_revoked:
            revoke_user_sessions(user)
        elif previous_policy != effective_device_policy(user):
            enforce_device_policy_with_gateway(user)

        return Response({"message": "添加成功"})

    def delete(self, request, *args, **kwargs):
        username = request.data.get("username")
        if username == ADMIN_USERNAME:
            raise ValidationError({"message": "不能删除管理员账号"})
        User.objects.filter(username=username).delete()
        return Response({"message": "删除成功"})


class VisitLogView(generics.ListAPIView):
    permission_classes = (IsAuthenticated, IsAdminUser)
    queryset = VisitLog.objects.order_by("-id").all()
    serializer_class = ShowVisitLogModelSerializer
    pagination_class = DefaultPageNumberPagination

    def get_queryset(self):
        queryset = super().get_queryset()
        query = str(self.request.query_params.get("q") or "").strip()
        if query:
            queryset = queryset.filter(
                Q(username__icontains=query) | Q(chatgpt_username__icontains=query)
            )
        log_type = self.request.query_params.get("log_type")
        if log_type:
            queryset = queryset.filter(log_type=log_type)
        return queryset

    def delete(self, request, *args, **kwargs):
        protected_logs = VisitLog.objects.filter(
            username=ADMIN_USERNAME,
            log_type="login",
        )
        protected_count = protected_logs.count()
        deleted_count, _ = VisitLog.objects.exclude(
            username=ADMIN_USERNAME,
            log_type="login",
        ).delete()
        return Response({
            "message": "日志已清除",
            "deleted_count": deleted_count,
            "protected_count": protected_count,
        })


class BatchUserActionView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def post(self, request):
        serializer = BatchUserActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        queryset = User.objects.filter(id__in=serializer.validated_data["user_id_list"]).exclude(username=ADMIN_USERNAME)
        action = serializer.validated_data["action"]
        users = list(queryset)
        if action == "delete":
            for user in users:
                revoke_user_sessions(user)
            changed, _ = queryset.delete()
        elif action in ("activate", "deactivate"):
            active = action == "activate"
            changed = queryset.update(is_active=active)
            if not active:
                for user in users:
                    revoke_user_sessions(user)
        else:
            with transaction.atomic():
                users = list(queryset.select_for_update())
                if action == "device_follow_plan":
                    for user in users:
                        user.device_policy_managed_by_plan = True
                        if "new_device_verification_enabled" in serializer.validated_data:
                            user.new_device_verification_enabled = serializer.validated_data[
                                "new_device_verification_enabled"
                            ]
                        user.save(update_fields=[
                            "device_policy_managed_by_plan",
                            "new_device_verification_enabled",
                        ])
                else:
                    for user in users:
                        user.device_policy_managed_by_plan = False
                        user.multi_device_enabled = serializer.validated_data["multi_device_enabled"]
                        user.device_limit = serializer.validated_data["device_limit"]
                        if "new_device_verification_enabled" in serializer.validated_data:
                            user.new_device_verification_enabled = serializer.validated_data[
                                "new_device_verification_enabled"
                            ]
                        user.save(update_fields=[
                            "device_policy_managed_by_plan",
                            "multi_device_enabled",
                            "device_limit",
                            "new_device_verification_enabled",
                        ])
            for user in users:
                enforce_device_policy_with_gateway(user)
            changed = len(users)
        return Response({"message": "批量操作完成", "changed": changed})


class CurrentUserView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        result = {
            "authenticated": True,
            "username": request.user.username,
            "email": request.user.email,
            "email_verified": has_verified_email(request.user),
            "is_admin": bool(request.user.is_staff or request.user.is_superuser),
            "multi_device_enabled": effective_device_policy(request.user)["enabled"],
            "device_policy": effective_device_policy(request.user),
            "active_device_count": active_device_sessions(request.user).count(),
            "quota": quota_snapshot(request.user, request=request),
            "csrf_token": get_token(request),
        }
        if not result["email_verified"]:
            result["email_binding_ticket"] = issue_binding_ticket(request.user)
        return Response(result)


def serialize_device_session(session, *, current_session_id=None):
    return {
        "id": session.id,
        "is_current": session.id == current_session_id,
        "is_primary": session.is_primary,
        "device_type": session.device_type or "desktop",
        "browser_name": session.browser_name or "其他浏览器",
        "os_name": session.os_name or "其他系统",
        "ip_address": str(session.ip_address or ""),
        "verified_at": session.verified_at,
        "last_seen_at": session.last_seen_at,
        "created_at": session.created_at,
        "expires_at": session.expires_at,
    }


class DeviceSessionView(APIView):
    permission_classes = (IsAuthenticated,)

    @staticmethod
    def target_user(request):
        user_id = request.query_params.get("user_id") or request.data.get("user_id")
        if not user_id:
            return request.user
        if not (request.user.is_staff or request.user.is_superuser):
            raise ValidationError({"message": "没有权限查看其他用户的设备"})
        target = User.objects.filter(pk=user_id).first()
        if not target:
            raise ValidationError({"message": "用户不存在"})
        return target

    def get(self, request):
        target_user = self.target_user(request)
        current = get_device_session(request, request.user, touch=False)
        sessions = active_device_sessions(target_user).order_by("-is_primary", "-last_seen_at", "-id")
        return Response({
            "username": target_user.username,
            "policy": effective_device_policy(target_user),
            "sessions": [
                serialize_device_session(
                    item,
                    current_session_id=getattr(current, "id", None) if target_user == request.user else None,
                )
                for item in sessions
            ],
        })

    def delete(self, request):
        target_user = self.target_user(request)
        session = UserDeviceSession.objects.filter(
            pk=request.data.get("session_id"),
            user=target_user,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).first()
        if not session:
            raise ValidationError({"message": "设备会话不存在或已失效"})
        current = get_device_session(request, request.user, touch=False) if target_user == request.user else None
        session.revoked_at = timezone.now()
        session.save(update_fields=["revoked_at"])
        has_remaining_devices = active_device_sessions(target_user).exists()
        if session != current or not has_remaining_devices:
            try:
                req_gateway("post", "/api/logout", json={"user_name": target_user.username})
            except ValidationError:
                pass
        response = Response({"message": "设备已下线", "current_device_revoked": session == current})
        if session == current:
            if not active_device_sessions(request.user).exists() and request.auth:
                Token.objects.filter(key=str(request.auth)).delete()
            clear_auth_cookie(response)
            response.delete_cookie("mirror_token", path="/", samesite="Lax")
        return response


class ChangePasswordView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data, context={"user": request.user}
        )
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            if not user.check_password(serializer.validated_data["current_password"]):
                raise ValidationError({"current_password": "当前密码不正确"})
            user.set_password(serializer.validated_data["new_password"])
            user.save(update_fields=["password"])
            Token.objects.filter(user=user).delete()
            revoked_subjects = revoke_all_device_sessions(user)
            token = issue_user_token(user)
            device_token, device_id, _device_session, _ = issue_device_session(
                request, user, verified=True
            )

        for subject in revoked_subjects or [user.username]:
            try:
                req_gateway("post", "/api/logout", json={"user_name": subject})
            except ValidationError:
                pass
        request.user = user
        rotate_token(request)
        response = Response({
            "message": "密码修改成功，其他会话已退出",
            "csrf_token": get_token(request),
        })
        set_auth_cookie(response, token, device_token, device_id)
        return response


class QuotaView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        return Response(quota_snapshot(request.user, request=request))


class OperationsOverviewView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request):
        today_start = int(
            timezone.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        )
        accounts = ChatgptAccount.objects.all()
        try:
            gateway_metrics = req_gateway(
                "post", "/api/operations-overview", json={"day_start": today_start}
            )
        except ValidationError:
            gateway_metrics = {}
        result = {
            "users": {
                "total": User.objects.count(),
                "active": User.objects.filter(is_active=True).count(),
                "expired": User.objects.filter(expired_date__lte=timezone.localdate()).count(),
            },
            "upstream": {
                "total": accounts.count(),
                "healthy": accounts.filter(auth_status=True).count(),
                "unhealthy": accounts.filter(auth_status=False).count(),
            },
            "activity": {
                "today_logins": VisitLog.objects.filter(
                    log_type="login", created_at__gte=today_start
                ).count(),
                "today_requests": VisitLog.objects.filter(
                    log_type="proxy", created_at__gte=today_start
                ).count() or int(gateway_metrics.get("today_requests", 0)),
                "active_sessions": int(gateway_metrics.get("active_sessions", 0)),
            },
            "generated_at": int(time.time()),
        }
        from django.conf import settings
        if settings.BILLING_ENABLED:
            from app.billing.selectors import admin_overview

            result["billing"] = admin_overview()
        return Response(result)

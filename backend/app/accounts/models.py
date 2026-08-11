import uuid

from django.contrib.auth.models import AbstractUser, AbstractBaseUser
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from app.fields import EncryptedTextField
from app.chatgpt.models import ChatgptAccount


class User(AbstractUser):
    model_limit = models.JSONField(default=list, verbose_name="备注")
    remark = models.TextField(blank=True, verbose_name="备注")
    isolated_session = models.BooleanField(default=True, verbose_name="独立回话")
    gptcar_list = models.JSONField(default=list)
    expired_date = models.DateField(blank=True, null=True, verbose_name="过期日期")
    daily_quota = models.PositiveIntegerField(default=0, verbose_name="每日配额")
    monthly_quota = models.PositiveIntegerField(default=0, verbose_name="每月配额")
    force_chat_mode = models.BooleanField(default=True, verbose_name="自动退出 Work 模式")
    email_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                condition=~Q(email=""),
                name="accounts_user_email_ci_unique",
            )
        ]


class EmailVerificationPurpose(models.TextChoices):
    REGISTER = "REGISTER", "注册"
    PASSWORD_RESET = "PASSWORD_RESET", "找回密码"
    EMAIL_BINDING = "EMAIL_BINDING", "绑定邮箱"
    EMAIL_CHANGE = "EMAIL_CHANGE", "修改邮箱"


class EmailDeliveryStatus(models.TextChoices):
    PENDING = "PENDING", "等待发送"
    SENT = "SENT", "已发送"
    FAILED = "FAILED", "发送失败"


class EmailVerificationChallenge(models.Model):
    challenge_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="email_verification_challenges",
    )
    email = models.EmailField(max_length=254)
    purpose = models.CharField(max_length=24, choices=EmailVerificationPurpose.choices)
    code_hash = models.CharField(max_length=256)
    delivery_status = models.CharField(
        max_length=16,
        choices=EmailDeliveryStatus.choices,
        default=EmailDeliveryStatus.PENDING,
        db_index=True,
    )
    delivery_attempt_count = models.PositiveSmallIntegerField(default=0)
    delivery_error = models.CharField(max_length=64, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivery_token = EncryptedTextField(blank=True)
    delivery_queued_at = models.DateTimeField(null=True, blank=True)
    requested_ip_hash = models.CharField(max_length=64, blank=True)
    expires_at = models.DateTimeField()
    attempt_count = models.PositiveSmallIntegerField(default=0)
    locked_at = models.DateTimeField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("email", "purpose", "created_at")),
            models.Index(fields=("user", "purpose", "created_at")),
        ]


class VisitLog(models.Model):
    # user = models.ForeignKey(User, db_constraint=False, on_delete=models.SET_NULL, null=True)
    username = models.CharField(max_length=150, verbose_name="用户名")
    chatgpt_username = models.CharField(max_length=150, null=True, verbose_name="chatgpt")
    log_type = models.CharField(max_length=20, verbose_name="登录类型")
    created_at = models.IntegerField(verbose_name="登录时间")
    ip = models.GenericIPAddressField(verbose_name="登录IP")
    user_agent = models.TextField(verbose_name="User-Agent")

    @classmethod
    def save_data(cls, data):
        obj = cls.objects.create(**data)
        return obj

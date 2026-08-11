from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from app.chatgpt.models import ChatgptAccount, ChatgptCar


class PoolTier(models.TextChoices):
    STANDARD = "STANDARD", "普通 Plus 池"
    PREMIUM = "PREMIUM", "高级 Plus 池"


class SubscriptionStatus(models.TextChoices):
    PENDING = "PENDING", "待生效"
    ACTIVE = "ACTIVE", "生效中"
    SUSPENDED = "SUSPENDED", "已暂停"
    REFUNDED = "REFUNDED", "已退款"
    EXPIRED = "EXPIRED", "已到期"
    CANCELLED = "CANCELLED", "已取消"


class OrderStatus(models.TextChoices):
    PENDING = "PENDING", "待支付"
    PAID = "PAID", "已支付"
    CLOSED = "CLOSED", "已关闭"
    FAILED = "FAILED", "支付失败"
    REFUNDED = "REFUNDED", "已退款"


class OrderType(models.TextChoices):
    PURCHASE = "PURCHASE", "新购"
    RENEW = "RENEW", "续费"
    UPGRADE = "UPGRADE", "升级"
    DOWNGRADE = "DOWNGRADE", "降级"


class ReservationStatus(models.TextChoices):
    HELD = "HELD", "支付席位保留"
    ACTIVE = "ACTIVE", "已购席位"
    ASSIGNED = "ASSIGNED", "已绑定账号"
    RELEASED = "RELEASED", "已释放"
    EXPIRED = "EXPIRED", "已过期"


class AssignmentEventType(models.TextChoices):
    ASSIGNED = "ASSIGNED", "首次分配"
    MIGRATED = "MIGRATED", "故障迁移"
    RELEASED = "RELEASED", "释放"


class UsageResult(models.TextChoices):
    SUCCESS = "SUCCESS", "成功"
    ERROR = "ERROR", "失败"
    DENIED = "DENIED", "拒绝"


class AnnouncementAudience(models.TextChoices):
    ALL = "ALL", "全部用户"
    ACTIVE_SUBSCRIBERS = "ACTIVE_SUBSCRIBERS", "有效订阅用户"
    PLAN = "PLAN", "指定套餐"


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Plan(TimestampedModel):
    code = models.SlugField(max_length=48, unique=True)
    name = models.CharField(max_length=64)
    tagline = models.CharField(max_length=160, blank=True)
    pool = models.ForeignKey(
        ChatgptCar,
        on_delete=models.PROTECT,
        related_name="billing_plans",
    )
    pool_tier = models.CharField(max_length=16, choices=PoolTier.choices)
    is_active = models.BooleanField(default=True)
    is_public = models.BooleanField(default=True)
    is_archived = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "id")

    def __str__(self):
        return self.name


class PlanOffer(TimestampedModel):
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="offers")
    code = models.SlugField(max_length=48)
    name = models.CharField(max_length=64)
    months = models.PositiveSmallIntegerField()
    price_cents = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=8, default="CNY")
    is_draft = models.BooleanField(default=False)
    is_purchase_enabled = models.BooleanField(default=True)
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ("months", "price_cents", "id")
        constraints = [
            models.UniqueConstraint(fields=("plan", "code"), name="billing_offer_plan_code_uniq"),
            models.CheckConstraint(condition=Q(months__gte=1), name="billing_offer_months_positive"),
        ]

    def __str__(self):
        return f"{self.plan.name} - {self.name}"

    def clean(self):
        if not self.is_draft and self.is_purchase_enabled and self.price_cents <= 0:
            raise ValidationError({"price_cents": "可购买套餐的价格必须大于 0"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class Subscription(TimestampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="billing_subscription",
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    offer = models.ForeignKey(
        PlanOffer,
        on_delete=models.PROTECT,
        related_name="subscriptions",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=16,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.PENDING,
        db_index=True,
    )
    source = models.CharField(max_length=32, default="manual")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(db_index=True)
    scheduled_plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name="scheduled_subscriptions",
        null=True,
        blank=True,
    )
    scheduled_offer = models.ForeignKey(
        PlanOffer,
        on_delete=models.PROTECT,
        related_name="scheduled_subscriptions",
        null=True,
        blank=True,
    )
    scheduled_months = models.PositiveSmallIntegerField(default=0)
    auto_renew = models.BooleanField(default=False)

    class Meta:
        ordering = ("-ends_at", "-id")

    @property
    def is_service_active(self):
        return self.status == SubscriptionStatus.ACTIVE and self.ends_at > timezone.now()

    def __str__(self):
        return f"{self.user.username} - {self.plan.name}"


class Order(TimestampedModel):
    order_no = models.CharField(max_length=40, unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="billing_orders",
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="orders")
    offer = models.ForeignKey(PlanOffer, on_delete=models.PROTECT, related_name="orders")
    order_type = models.CharField(max_length=16, choices=OrderType.choices)
    status = models.CharField(
        max_length=16,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING,
        db_index=True,
    )
    provider = models.CharField(max_length=32, default="manual")
    provider_order_id = models.CharField(max_length=128, blank=True)
    idempotency_key = models.CharField(max_length=96, unique=True, null=True, blank=True)
    plan_snapshot = models.JSONField(default=dict)
    offer_snapshot = models.JSONField(default=dict)
    price_cents = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=8, default="CNY")
    paid_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-created_at", "-id")

    def __str__(self):
        return self.order_no


class PaymentTransaction(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="transactions")
    provider = models.CharField(max_length=32)
    provider_transaction_id = models.CharField(max_length=128, blank=True)
    event_id = models.CharField(max_length=128, unique=True)
    event_type = models.CharField(max_length=32)
    amount_cents = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=8, default="CNY")
    signature_verified = models.BooleanField(default=False)
    accepted = models.BooleanField(default=False)
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")


class PoolAccountPolicy(TimestampedModel):
    pool = models.ForeignKey(ChatgptCar, on_delete=models.PROTECT, related_name="billing_policies")
    account = models.OneToOneField(
        ChatgptAccount,
        on_delete=models.PROTECT,
        related_name="billing_policy",
    )
    tier = models.CharField(max_length=16, choices=PoolTier.choices)
    binding_limit = models.PositiveSmallIntegerField(default=5)
    enabled = models.BooleanField(default=True)
    health_status = models.CharField(max_length=16, default="HEALTHY")
    last_health_check_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("pool_id", "account_id")

    def clean(self):
        errors = {}
        if self.tier == PoolTier.STANDARD and not 3 <= self.binding_limit <= 8:
            errors["binding_limit"] = "普通池单账号绑定人数必须在 3 到 8 之间"
        if self.tier == PoolTier.PREMIUM and self.binding_limit != 3:
            errors["binding_limit"] = "高级池单账号绑定人数固定为 3"
        if self.account_id and "plus" not in (self.account.plan_type or "").lower():
            errors["account"] = "商业号池只允许加入 Plus 账号"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class PoolReservation(TimestampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pool_reservations",
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="reservations")
    pool = models.ForeignKey(ChatgptCar, on_delete=models.PROTECT, related_name="billing_reservations")
    order = models.OneToOneField(
        Order,
        on_delete=models.PROTECT,
        related_name="reservation",
        null=True,
        blank=True,
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.PROTECT,
        related_name="reservations",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=16, choices=ReservationStatus.choices, db_index=True)
    slot_count = models.PositiveSmallIntegerField(default=1)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(condition=Q(slot_count=1), name="billing_reservation_one_slot"),
        ]


class AccountAssignment(TimestampedModel):
    subscription = models.OneToOneField(
        Subscription,
        on_delete=models.PROTECT,
        related_name="assignment",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="account_assignments",
    )
    pool = models.ForeignKey(ChatgptCar, on_delete=models.PROTECT, related_name="account_assignments")
    account = models.ForeignKey(
        ChatgptAccount,
        on_delete=models.PROTECT,
        related_name="billing_assignments",
    )
    active = models.BooleanField(default=True, db_index=True)
    assigned_at = models.DateTimeField(default=timezone.now)
    released_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-assigned_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("user",),
                condition=Q(active=True),
                name="billing_one_active_assignment_per_user",
            ),
        ]


class AccountAssignmentEvent(models.Model):
    assignment = models.ForeignKey(
        AccountAssignment,
        on_delete=models.PROTECT,
        related_name="events",
        null=True,
        blank=True,
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.PROTECT,
        related_name="assignment_events",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="assignment_events",
    )
    old_account = models.ForeignKey(
        ChatgptAccount,
        on_delete=models.PROTECT,
        related_name="assignment_events_from",
        null=True,
        blank=True,
    )
    new_account = models.ForeignKey(
        ChatgptAccount,
        on_delete=models.PROTECT,
        related_name="assignment_events_to",
        null=True,
        blank=True,
    )
    event_type = models.CharField(max_length=16, choices=AssignmentEventType.choices)
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")


class UsageEvent(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="billing_usage_events",
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.PROTECT,
        related_name="usage_events",
        null=True,
        blank=True,
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="usage_events", null=True, blank=True)
    account = models.ForeignKey(
        ChatgptAccount,
        on_delete=models.PROTECT,
        related_name="usage_events",
        null=True,
        blank=True,
    )
    model_name = models.CharField(max_length=128, blank=True)
    result = models.CharField(max_length=16, choices=UsageResult.choices)
    duration_ms = models.PositiveIntegerField(default=0)
    request_at = models.DateTimeField(default=timezone.now, db_index=True)
    error_code = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ("-request_at", "-id")
        indexes = [models.Index(fields=("user", "request_at"))]


class Announcement(TimestampedModel):
    title = models.CharField(max_length=120)
    content = models.TextField()
    category = models.CharField(max_length=32, default="NOTICE")
    severity = models.CharField(max_length=16, default="INFO")
    audience = models.CharField(
        max_length=24,
        choices=AnnouncementAudience.choices,
        default=AnnouncementAudience.ALL,
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name="announcements",
        null=True,
        blank=True,
    )
    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-published_at", "-created_at")


class UserNotification(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="notifications",
    )
    announcement = models.ForeignKey(
        Announcement,
        on_delete=models.PROTECT,
        related_name="notifications",
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=32, default="SYSTEM")
    title = models.CharField(max_length=120)
    content = models.TextField()
    dedupe_key = models.CharField(max_length=128, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("user", "dedupe_key"),
                condition=~Q(dedupe_key=""),
                name="billing_notification_dedupe_uniq",
            ),
        ]


class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="billing_audit_logs",
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=64, db_index=True)
    target_type = models.CharField(max_length=64)
    target_id = models.CharField(max_length=64, blank=True)
    detail = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")

from rest_framework import serializers

from app.billing.models import (
    AccountAssignment,
    Announcement,
    AuditLog,
    CommercialSettings,
    Order,
    PaymentTransaction,
    Plan,
    PlanOffer,
    PoolAccountPolicy,
    RedemptionCode,
    RedemptionCodeBatch,
    SupportContact,
    Subscription,
    UserNotification,
)
from app.billing.selectors import capacity_snapshot


class PlanOfferSerializer(serializers.ModelSerializer):
    price_yuan = serializers.SerializerMethodField()

    class Meta:
        model = PlanOffer
        fields = (
            "id",
            "code",
            "name",
            "months",
            "price_cents",
            "price_yuan",
            "currency",
            "is_draft",
            "is_purchase_enabled",
            "is_archived",
        )

    def get_price_yuan(self, obj):
        return f"{obj.price_cents / 100:.2f}"


class PlanSerializer(serializers.ModelSerializer):
    offers = serializers.SerializerMethodField()
    capacity = serializers.SerializerMethodField()
    purchase_available = serializers.SerializerMethodField()
    pool_name = serializers.CharField(source="pool.car_name", read_only=True)
    pool_ids = serializers.SerializerMethodField()
    pool_names = serializers.SerializerMethodField()

    class Meta:
        model = Plan
        fields = (
            "id",
            "code",
            "name",
            "tagline",
            "pool_id",
            "pool_name",
            "pool_ids",
            "pool_names",
            "pool_tier",
            "user_limit",
            "daily_quota",
            "monthly_quota",
            "is_active",
            "is_public",
            "is_archived",
            "sort_order",
            "offers",
            "purchase_available",
            "capacity",
        )

    def get_fields(self):
        fields = super().get_fields()
        if not self.context.get("admin"):
            fields.pop("capacity", None)
        return fields

    def get_offers(self, obj):
        queryset = obj.offers.all()
        if not self.context.get("include_archived"):
            queryset = queryset.filter(is_archived=False)
        if not self.context.get("admin"):
            queryset = queryset.filter(is_draft=False, is_purchase_enabled=True)
        return PlanOfferSerializer(queryset, many=True).data

    def get_capacity(self, obj):
        return capacity_snapshot(obj)

    def get_pool_ids(self, obj):
        pool_ids = [link.pool_id for link in obj.pool_links.all() if link.is_active]
        return pool_ids or ([obj.pool_id] if obj.pool_id else [])

    def get_pool_names(self, obj):
        pool_names = [link.pool.car_name for link in obj.pool_links.all() if link.is_active]
        return pool_names or ([obj.pool.car_name] if obj.pool_id else [])

    def get_purchase_available(self, obj):
        return not capacity_snapshot(obj)["is_full"]


class SubscriptionSerializer(serializers.ModelSerializer):
    plan = PlanSerializer(read_only=True)
    offer = PlanOfferSerializer(read_only=True)
    scheduled_plan_name = serializers.CharField(source="scheduled_plan.name", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    assignment = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = (
            "id",
            "username",
            "status",
            "source",
            "starts_at",
            "ends_at",
            "auto_renew",
            "plan",
            "offer",
            "scheduled_plan_id",
            "scheduled_plan_name",
            "scheduled_months",
            "assignment",
        )

    def get_assignment(self, obj):
        try:
            assignment = obj.assignment
        except AccountAssignment.DoesNotExist:
            return None
        if not assignment.active:
            return None
        result = {
            "status": "BOUND",
            "pool_id": assignment.pool_id,
            "assigned_at": assignment.assigned_at,
            "last_used_at": assignment.last_used_at,
        }
        if self.context.get("admin"):
            result.update({
                "account_id": assignment.account_id,
                "account_name": assignment.account.chatgpt_username,
            })
        return result


class OrderSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    plan_name = serializers.CharField(source="plan.name", read_only=True)
    offer_name = serializers.CharField(source="offer.name", read_only=True)
    price_yuan = serializers.SerializerMethodField()
    transactions = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = (
            "id",
            "order_no",
            "username",
            "plan_id",
            "plan_name",
            "offer_id",
            "offer_name",
            "entitlement_months",
            "order_type",
            "status",
            "provider",
            "price_cents",
            "price_yuan",
            "currency",
            "payment_expires_at",
            "created_at",
            "paid_at",
            "entitlement_ends_at",
            "closed_at",
            "provider_order_id",
            "transactions",
        )

    def get_fields(self):
        fields = super().get_fields()
        if not self.context.get("admin"):
            fields.pop("provider_order_id", None)
            fields.pop("transactions", None)
        return fields

    def get_price_yuan(self, obj):
        return f"{obj.price_cents / 100:.2f}"

    def get_transactions(self, obj):
        return PaymentTransactionSerializer(obj.transactions.all(), many=True).data


class PaymentTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentTransaction
        fields = (
            "id",
            "provider",
            "provider_transaction_id",
            "event_id",
            "event_type",
            "amount_cents",
            "currency",
            "signature_verified",
            "accepted",
            "payload",
            "occurred_at",
            "created_at",
        )


class CommercialSettingsSerializer(serializers.ModelSerializer):
    updated_by_name = serializers.CharField(source="updated_by.username", read_only=True)

    class Meta:
        model = CommercialSettings
        fields = (
            "redemption_enabled",
            "purchase_url",
            "updated_by_id",
            "updated_by_name",
            "updated_at",
        )


class RedemptionCodeBatchSerializer(serializers.ModelSerializer):
    plan_name = serializers.SerializerMethodField()
    offer_name = serializers.SerializerMethodField()
    created_by_name = serializers.CharField(source="created_by.username", read_only=True)
    available_count = serializers.SerializerMethodField()
    redeemed_count = serializers.SerializerMethodField()
    revoked_count = serializers.SerializerMethodField()
    archived_count = serializers.SerializerMethodField()

    class Meta:
        model = RedemptionCodeBatch
        fields = (
            "id",
            "batch_no",
            "offer_id",
            "plan_name",
            "offer_name",
            "entitlement_months",
            "price_cents",
            "currency",
            "quantity",
            "expires_at",
            "is_active",
            "is_archived",
            "note",
            "created_by_id",
            "created_by_name",
            "available_count",
            "redeemed_count",
            "revoked_count",
            "archived_count",
            "created_at",
            "updated_at",
        )

    def get_plan_name(self, obj):
        return obj.plan_snapshot.get("name") or obj.plan.name

    def get_offer_name(self, obj):
        return obj.offer_snapshot.get("name") or obj.offer.name

    @staticmethod
    def _count(obj, annotation, **filters):
        annotated = getattr(obj, annotation, None)
        return annotated if annotated is not None else obj.codes.filter(**filters).count()

    def get_available_count(self, obj):
        return self._count(obj, "available_count", status="AVAILABLE", is_archived=False)

    def get_redeemed_count(self, obj):
        return self._count(obj, "redeemed_count", status="REDEEMED", is_archived=False)

    def get_revoked_count(self, obj):
        return self._count(obj, "revoked_count", status="REVOKED", is_archived=False)

    def get_archived_count(self, obj):
        return self._count(obj, "archived_count", is_archived=True)


class RedemptionCodeSerializer(serializers.ModelSerializer):
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True)
    plan_name = serializers.SerializerMethodField()
    offer_name = serializers.SerializerMethodField()
    username = serializers.CharField(source="redeemed_by.username", read_only=True)
    order_no = serializers.CharField(source="order.order_no", read_only=True)
    subscription_ends_at = serializers.DateTimeField(source="subscription.ends_at", read_only=True)

    class Meta:
        model = RedemptionCode
        fields = (
            "id",
            "serial_no",
            "code_mask",
            "batch_id",
            "batch_no",
            "plan_name",
            "offer_name",
            "status",
            "is_archived",
            "username",
            "redeemed_email",
            "redeemed_at",
            "redeemed_ip",
            "redeemed_user_agent",
            "order_id",
            "order_no",
            "subscription_id",
            "subscription_ends_at",
            "revoked_at",
            "archived_at",
            "created_at",
        )

    def get_plan_name(self, obj):
        return obj.batch.plan_snapshot.get("name") or obj.batch.plan.name

    def get_offer_name(self, obj):
        return obj.batch.offer_snapshot.get("name") or obj.batch.offer.name


class PoolPolicySerializer(serializers.ModelSerializer):
    pool_name = serializers.CharField(source="pool.car_name", read_only=True)
    account_name = serializers.CharField(source="account.chatgpt_username", read_only=True)
    account_plan_type = serializers.CharField(source="account.plan_type", read_only=True)
    account_auth_status = serializers.BooleanField(source="account.auth_status", read_only=True)
    active_bindings = serializers.SerializerMethodField()

    class Meta:
        model = PoolAccountPolicy
        fields = (
            "id",
            "pool_id",
            "pool_name",
            "account_id",
            "account_name",
            "account_plan_type",
            "account_auth_status",
            "tier",
            "binding_limit",
            "enabled",
            "health_status",
            "last_health_check_at",
            "active_bindings",
        )

    def get_active_bindings(self, obj):
        annotated_count = getattr(obj, "active_bindings_count", None)
        if annotated_count is not None:
            return annotated_count
        return obj.account.billing_assignments.filter(active=True).count()


class AccountAssignmentUsageSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    email_verified = serializers.SerializerMethodField()
    plan_name = serializers.CharField(source="subscription.plan.name", read_only=True)
    subscription_status = serializers.CharField(source="subscription.status", read_only=True)
    subscription_ends_at = serializers.DateTimeField(source="subscription.ends_at", read_only=True)

    class Meta:
        model = AccountAssignment
        fields = (
            "id",
            "username",
            "email",
            "email_verified",
            "plan_name",
            "subscription_status",
            "subscription_ends_at",
            "assigned_at",
            "last_used_at",
        )

    def get_email_verified(self, obj):
        return bool(obj.user.email and obj.user.email_verified_at)


class NotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.SerializerMethodField()
    requires_acknowledgement = serializers.SerializerMethodField()

    class Meta:
        model = UserNotification
        fields = (
            "id",
            "kind",
            "title",
            "content",
            "requires_acknowledgement",
            "is_read",
            "read_at",
            "created_at",
        )

    def get_is_read(self, obj):
        return obj.read_at is not None

    def get_requires_acknowledgement(self, obj):
        return bool(obj.announcement_id and obj.announcement.requires_acknowledgement)


class AnnouncementSerializer(serializers.ModelSerializer):
    plan_name = serializers.CharField(source="plan.name", read_only=True)

    class Meta:
        model = Announcement
        fields = "__all__"


class SupportContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportContact
        fields = (
            "id",
            "name",
            "channel",
            "contact",
            "description",
            "qr_image",
            "is_active",
            "sort_order",
        )


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.username", read_only=True)

    class Meta:
        model = AuditLog
        fields = (
            "id",
            "actor_id",
            "actor_name",
            "action",
            "target_type",
            "target_id",
            "detail",
            "ip_address",
            "created_at",
        )

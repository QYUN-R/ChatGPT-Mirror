from rest_framework import serializers

from app.billing.models import (
    AccountAssignment,
    Announcement,
    AuditLog,
    Order,
    Plan,
    PlanOffer,
    PoolAccountPolicy,
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
    pool_name = serializers.CharField(source="pool.car_name", read_only=True)

    class Meta:
        model = Plan
        fields = (
            "id",
            "code",
            "name",
            "tagline",
            "pool_id",
            "pool_name",
            "pool_tier",
            "is_active",
            "is_public",
            "is_archived",
            "sort_order",
            "offers",
            "capacity",
        )

    def get_offers(self, obj):
        queryset = obj.offers.all()
        if not self.context.get("include_archived"):
            queryset = queryset.filter(is_archived=False)
        if not self.context.get("admin"):
            queryset = queryset.filter(is_draft=False, is_purchase_enabled=True)
        return PlanOfferSerializer(queryset, many=True).data

    def get_capacity(self, obj):
        return capacity_snapshot(obj)


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
            "order_type",
            "status",
            "provider",
            "price_cents",
            "price_yuan",
            "currency",
            "created_at",
            "paid_at",
            "closed_at",
        )

    def get_price_yuan(self, obj):
        return f"{obj.price_cents / 100:.2f}"


class PoolPolicySerializer(serializers.ModelSerializer):
    pool_name = serializers.CharField(source="pool.car_name", read_only=True)
    account_name = serializers.CharField(source="account.chatgpt_username", read_only=True)
    active_bindings = serializers.SerializerMethodField()

    class Meta:
        model = PoolAccountPolicy
        fields = (
            "id",
            "pool_id",
            "pool_name",
            "account_id",
            "account_name",
            "tier",
            "binding_limit",
            "enabled",
            "health_status",
            "last_health_check_at",
            "active_bindings",
        )

    def get_active_bindings(self, obj):
        return obj.account.billing_assignments.filter(active=True).count()


class NotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.SerializerMethodField()

    class Meta:
        model = UserNotification
        fields = (
            "id",
            "kind",
            "title",
            "content",
            "is_read",
            "read_at",
            "created_at",
        )

    def get_is_read(self, obj):
        return obj.read_at is not None


class AnnouncementSerializer(serializers.ModelSerializer):
    plan_name = serializers.CharField(source="plan.name", read_only=True)

    class Meta:
        model = Announcement
        fields = "__all__"


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

from django.contrib import admin

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


for model in (
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
    AuditLog,
    SupportContact,
    CommercialSettings,
    RedemptionCodeBatch,
    RedemptionCode,
):
    admin.site.register(model)

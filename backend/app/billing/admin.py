from django.contrib import admin

from app.billing.models import (
    AccountAssignment,
    AccountAssignmentEvent,
    Announcement,
    AuditLog,
    Order,
    PaymentTransaction,
    Plan,
    PlanOffer,
    PoolAccountPolicy,
    PoolReservation,
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
):
    admin.site.register(model)

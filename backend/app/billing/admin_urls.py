from django.urls import path

from app.billing.admin_views import (
    AdminAnnouncementView,
    AdminAuditLogView,
    AdminOrderView,
    AdminPlanView,
    AdminPoolView,
    AdminSubscriptionView,
)


urlpatterns = [
    path("plans", AdminPlanView.as_view()),
    path("pools", AdminPoolView.as_view()),
    path("subscriptions", AdminSubscriptionView.as_view()),
    path("orders", AdminOrderView.as_view()),
    path("announcements", AdminAnnouncementView.as_view()),
    path("audit-logs", AdminAuditLogView.as_view()),
]

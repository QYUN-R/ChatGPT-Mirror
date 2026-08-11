from django.urls import path

from app.billing.views import (
    BillingMeView,
    NotificationListView,
    NotificationReadView,
    OrderListCreateView,
    OrderMockPayView,
    OrderRenewView,
    PlanListView,
    SupportContactListView,
)


urlpatterns = [
    path("plans", PlanListView.as_view()),
    path("me", BillingMeView.as_view()),
    path("orders", OrderListCreateView.as_view()),
    path("orders/<int:order_id>/renew", OrderRenewView.as_view()),
    path("orders/<int:order_id>/mock-pay", OrderMockPayView.as_view()),
    path("notifications", NotificationListView.as_view()),
    path("notifications/<int:notification_id>/read", NotificationReadView.as_view()),
    path("support-contacts", SupportContactListView.as_view()),
]

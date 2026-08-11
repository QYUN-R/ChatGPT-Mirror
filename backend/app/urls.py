"""
URL configuration for chatgpt_mirror project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include
from app.billing.views import NotificationListView, NotificationReadView
from app.settings import DEBUG

urlpatterns = [
    path("0x/user/", include("app.accounts.urls")),
    path("0x/chatgpt/", include("app.chatgpt.urls")),
    path("0x/billing/", include("app.billing.urls")),
    path("0x/notifications", NotificationListView.as_view()),
    path("0x/notifications/<int:notification_id>/read", NotificationReadView.as_view()),
    path("0x/admin/", include("app.billing.admin_urls")),
]

if DEBUG:
    urlpatterns.append(path("admin/", admin.site.urls))

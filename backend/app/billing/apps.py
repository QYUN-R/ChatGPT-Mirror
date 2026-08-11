from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "app.billing"
    verbose_name = "套餐与计费"

    def ready(self):
        from app.billing import signals  # noqa: F401

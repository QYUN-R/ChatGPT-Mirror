from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0008_plan_device_policy"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="plan",
            constraint=models.CheckConstraint(
                condition=Q(device_limit__gte=1, device_limit__lte=50),
                name="billing_plan_device_limit_range",
            ),
        ),
    ]

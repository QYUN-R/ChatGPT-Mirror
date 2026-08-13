from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0007_plan_multiple_pools_and_quotas"),
    ]

    operations = [
        migrations.AddField(
            model_name="plan",
            name="device_limit",
            field=models.PositiveSmallIntegerField(default=3),
        ),
        migrations.AddField(
            model_name="plan",
            name="multi_device_enabled",
            field=models.BooleanField(default=True),
        ),
    ]

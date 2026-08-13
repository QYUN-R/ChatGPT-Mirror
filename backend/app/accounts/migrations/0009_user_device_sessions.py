import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0008_email_delivery_outbox"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="multi_device_enabled",
            field=models.BooleanField(default=False, verbose_name="允许多设备"),
        ),
        migrations.CreateModel(
            name="UserDeviceSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token_hash", models.CharField(max_length=64, unique=True)),
                ("subject_id", models.UUIDField(default=uuid.uuid4, editable=False)),
                ("user_agent", models.CharField(blank=True, max_length=512)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("last_seen_at", models.DateTimeField(auto_now=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("revoked_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="device_sessions", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ("-last_seen_at",),
            },
        ),
        migrations.AddIndex(
            model_name="userdevicesession",
            index=models.Index(fields=["user", "revoked_at", "expires_at"], name="acct_dev_user_rev_exp_idx"),
        ),
    ]

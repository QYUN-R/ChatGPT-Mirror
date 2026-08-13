from django.db import migrations, models
from django.db.models import Q


def initialize_device_policy(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    UserDeviceSession = apps.get_model("accounts", "UserDeviceSession")

    User.objects.update(
        isolated_session=True,
        multi_device_enabled=True,
        device_policy_managed_by_plan=True,
        device_limit=3,
        new_device_verification_enabled=True,
    )

    primary_user_ids = set()
    sessions = UserDeviceSession.objects.select_related("user").order_by(
        "user_id",
        "-last_seen_at",
        "-id",
    )
    for session in sessions.iterator():
        is_primary = session.user_id not in primary_user_ids
        if is_primary:
            primary_user_ids.add(session.user_id)
        session.is_primary = is_primary
        session.gateway_subject = (
            session.user.username
            if is_primary
            else f"{session.user.username}:device:{session.subject_id.hex}"
        )
        session.verified_at = session.created_at
        session.save(update_fields=["is_primary", "gateway_subject", "verified_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0009_user_device_sessions"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="multi_device_enabled",
            field=models.BooleanField(default=True, verbose_name="允许多设备"),
        ),
        migrations.AddField(
            model_name="user",
            name="device_limit",
            field=models.PositiveSmallIntegerField(default=3, verbose_name="设备上限"),
        ),
        migrations.AddField(
            model_name="user",
            name="device_policy_managed_by_plan",
            field=models.BooleanField(default=True, verbose_name="设备策略跟随套餐"),
        ),
        migrations.AddField(
            model_name="user",
            name="new_device_verification_enabled",
            field=models.BooleanField(default=True, verbose_name="新设备邮箱验证"),
        ),
        migrations.AddField(
            model_name="userdevicesession",
            name="browser_name",
            field=models.CharField(blank=True, max_length=48),
        ),
        migrations.AddField(
            model_name="userdevicesession",
            name="device_key_hash",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="userdevicesession",
            name="device_type",
            field=models.CharField(blank=True, max_length=16),
        ),
        migrations.AddField(
            model_name="userdevicesession",
            name="gateway_subject",
            field=models.CharField(blank=True, max_length=220),
        ),
        migrations.AddField(
            model_name="userdevicesession",
            name="is_primary",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="userdevicesession",
            name="os_name",
            field=models.CharField(blank=True, max_length=48),
        ),
        migrations.AddField(
            model_name="userdevicesession",
            name="verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(initialize_device_policy, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="userdevicesession",
            constraint=models.UniqueConstraint(
                condition=Q(device_key_hash__isnull=False),
                fields=("user", "device_key_hash"),
                name="acct_dev_user_key_uniq",
            ),
        ),
    ]

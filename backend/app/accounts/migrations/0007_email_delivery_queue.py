from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0006_email_verification"),
    ]

    operations = [
        migrations.AlterField(
            model_name="emailverificationchallenge",
            name="purpose",
            field=models.CharField(
                choices=[
                    ("REGISTER", "注册"),
                    ("PASSWORD_RESET", "找回密码"),
                    ("EMAIL_BINDING", "绑定邮箱"),
                    ("EMAIL_CHANGE", "修改邮箱"),
                ],
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="emailverificationchallenge",
            name="delivery_status",
            field=models.CharField(
                choices=[("PENDING", "等待发送"), ("SENT", "已发送"), ("FAILED", "发送失败")],
                db_index=True,
                default="PENDING",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="emailverificationchallenge",
            name="delivery_attempt_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="emailverificationchallenge",
            name="delivery_error",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="emailverificationchallenge",
            name="delivered_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0010_device_policy_and_stable_identity"),
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
                    ("DEVICE_LOGIN", "新设备登录"),
                ],
                max_length=24,
            ),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=Q(device_limit__gte=1, device_limit__lte=50),
                name="accounts_user_device_limit_range",
            ),
        ),
    ]

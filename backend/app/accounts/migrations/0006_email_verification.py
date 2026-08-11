import uuid

from django.db import migrations, models
from django.db.models import Q
from django.db.models.functions import Lower


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_user_force_chat_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="email_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="EmailVerificationChallenge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("challenge_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("email", models.EmailField(max_length=254)),
                (
                    "purpose",
                    models.CharField(
                        choices=[
                            ("REGISTER", "注册"),
                            ("PASSWORD_RESET", "找回密码"),
                            ("EMAIL_BINDING", "绑定邮箱"),
                        ],
                        max_length=24,
                    ),
                ),
                ("code_hash", models.CharField(max_length=256)),
                ("requested_ip_hash", models.CharField(blank=True, max_length=64)),
                ("expires_at", models.DateTimeField()),
                ("attempt_count", models.PositiveSmallIntegerField(default=0)),
                ("locked_at", models.DateTimeField(blank=True, null=True)),
                ("invalidated_at", models.DateTimeField(blank=True, null=True)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.CASCADE,
                        related_name="email_verification_challenges",
                        to="accounts.user",
                    ),
                ),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.AddIndex(
            model_name="emailverificationchallenge",
                    index=models.Index(fields=["email", "purpose", "created_at"], name="accounts_em_email_a2c384_idx"),
        ),
        migrations.AddIndex(
            model_name="emailverificationchallenge",
                    index=models.Index(fields=["user", "purpose", "created_at"], name="accounts_em_user_id_3538c5_idx"),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                Lower("email"),
                condition=~Q(email=""),
                name="accounts_user_email_ci_unique",
            ),
        ),
    ]

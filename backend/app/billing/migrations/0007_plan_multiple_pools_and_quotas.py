from django.db import migrations, models
import django.db.models.deletion


def copy_primary_pools(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    PlanPool = apps.get_model("billing", "PlanPool")
    PlanPool.objects.bulk_create(
        [
            PlanPool(plan_id=plan.id, pool_id=plan.pool_id, priority=0, is_active=True)
            for plan in Plan.objects.exclude(pool_id=None).iterator()
        ],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0006_order_entitlement_ends_at_announcement_requires_acknowledgement"),
    ]

    operations = [
        migrations.AddField(
            model_name="plan",
            name="daily_quota",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="plan",
            name="monthly_quota",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="plan",
            name="user_limit",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.CreateModel(
            name="PlanPool",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("priority", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pool_links",
                        to="billing.plan",
                    ),
                ),
                (
                    "pool",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="billing_plan_links",
                        to="chatgpt.chatgptcar",
                    ),
                ),
            ],
            options={"ordering": ("priority", "id")},
        ),
        migrations.AddConstraint(
            model_name="planpool",
            constraint=models.UniqueConstraint(fields=("plan", "pool"), name="billing_plan_pool_uniq"),
        ),
        migrations.AddField(
            model_name="plan",
            name="pools",
            field=models.ManyToManyField(
                related_name="billing_pool_plans",
                through="billing.PlanPool",
                to="chatgpt.chatgptcar",
            ),
        ),
        migrations.RunPython(copy_primary_pools, migrations.RunPython.noop),
    ]

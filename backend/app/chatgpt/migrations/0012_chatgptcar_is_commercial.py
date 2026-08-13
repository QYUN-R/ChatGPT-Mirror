from django.db import migrations, models


def classify_existing_pools(apps, schema_editor):
    chatgpt_car = apps.get_model("chatgpt", "ChatgptCar")
    plan = apps.get_model("billing", "Plan")
    plan_pool = apps.get_model("billing", "PlanPool")
    pool_policy = apps.get_model("billing", "PoolAccountPolicy")

    commercial_ids = set(pool_policy.objects.values_list("pool_id", flat=True))
    commercial_ids.update(plan.objects.values_list("pool_id", flat=True))
    commercial_ids.update(plan_pool.objects.values_list("pool_id", flat=True))
    commercial_ids.discard(None)
    chatgpt_car.objects.filter(id__in=commercial_ids).update(is_commercial=True)


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0007_plan_multiple_pools_and_quotas"),
        ("chatgpt", "0011_clear_legacy_auth_diagnostics"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatgptcar",
            name="is_commercial",
            field=models.BooleanField(db_index=True, default=False, verbose_name="套餐号池"),
        ),
        migrations.RunPython(classify_existing_pools, migrations.RunPython.noop),
    ]

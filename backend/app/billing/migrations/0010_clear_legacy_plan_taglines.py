from django.db import migrations


LEGACY_DEFAULT_TAGLINES = (
    "稳定的 Plus 号池服务",
    "高优先级 Plus 号池服务",
)


def clear_legacy_default_taglines(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.filter(tagline__in=LEGACY_DEFAULT_TAGLINES).update(tagline="")


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0009_plan_device_limit_constraint"),
    ]

    operations = [
        migrations.RunPython(clear_legacy_default_taglines, migrations.RunPython.noop),
    ]

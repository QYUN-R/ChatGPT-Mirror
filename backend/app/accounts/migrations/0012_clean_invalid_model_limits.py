from django.db import migrations


INVALID_MODEL_LIMIT_VALUES = {"[object Object]", "undefined", "null"}


def clean_invalid_model_limits(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for user in User.objects.only("id", "model_limit").iterator():
        value = user.model_limit
        if not isinstance(value, list):
            cleaned = []
        else:
            cleaned = []
            seen = set()
            for item in value:
                if not isinstance(item, str):
                    continue
                model_id = item.strip()
                if not model_id or model_id in INVALID_MODEL_LIMIT_VALUES or model_id in seen:
                    continue
                seen.add(model_id)
                cleaned.append(model_id)
        if cleaned != value:
            User.objects.filter(pk=user.pk).update(model_limit=cleaned)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0011_device_policy_constraints"),
    ]

    operations = [
        migrations.RunPython(clean_invalid_model_limits, migrations.RunPython.noop),
    ]

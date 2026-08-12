from django.db import migrations


def clear_legacy_auth_diagnostics(apps, schema_editor):
    account_model = apps.get_model("chatgpt", "ChatgptAccount")
    account_model.objects.exclude(last_error__in=(None, "")).update(last_error="")


class Migration(migrations.Migration):
    dependencies = [
        ("chatgpt", "0010_chatgptaccount_archived_at_and_more"),
    ]

    operations = [
        migrations.RunPython(clear_legacy_auth_diagnostics, migrations.RunPython.noop),
    ]

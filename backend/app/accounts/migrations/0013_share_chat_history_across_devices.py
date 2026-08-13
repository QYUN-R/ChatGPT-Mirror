from django.db import migrations


def canonicalize_gateway_subjects(apps, schema_editor):
    UserDeviceSession = apps.get_model("accounts", "UserDeviceSession")
    sessions = UserDeviceSession.objects.select_related("user").all()
    for session in sessions.iterator():
        if session.gateway_subject != session.user.username:
            session.gateway_subject = session.user.username
            session.save(update_fields=["gateway_subject"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0012_clean_invalid_model_limits"),
    ]

    operations = [
        migrations.RunPython(canonicalize_gateway_subjects, migrations.RunPython.noop),
    ]

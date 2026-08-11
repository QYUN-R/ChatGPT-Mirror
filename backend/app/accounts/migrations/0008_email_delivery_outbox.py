from django.db import migrations, models

import app.fields


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0007_email_delivery_queue"),
    ]

    operations = [
        migrations.AddField(
            model_name="emailverificationchallenge",
            name="delivery_token",
            field=app.fields.EncryptedTextField(blank=True),
        ),
        migrations.AddField(
            model_name="emailverificationchallenge",
            name="delivery_queued_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

import calendar

from django.db import migrations, models


def add_months(value, months):
    month_index = value.month - 1 + max(int(months or 0), 0)
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def backfill_entitlement_ends_at(apps, schema_editor):
    Order = apps.get_model("billing", "Order")
    current_end_by_user = {}
    queryset = Order.objects.filter(status="PAID").order_by("user_id", "paid_at", "id")
    for order in queryset.iterator(chunk_size=500):
        effective_at = order.paid_at or order.created_at
        months = max(int(order.entitlement_months or 1), 1)
        current_end = current_end_by_user.get(order.user_id)
        if order.order_type == "PURCHASE" or current_end is None:
            entitlement_end = add_months(effective_at, months)
        else:
            entitlement_end = add_months(max(current_end, effective_at), months)
        current_end_by_user[order.user_id] = entitlement_end
        Order.objects.filter(pk=order.pk).update(entitlement_ends_at=entitlement_end)


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0005_order_entitlement_months_commercialsettings_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="announcement",
            name="requires_acknowledgement",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="order",
            name="entitlement_ends_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(backfill_entitlement_ends_at, migrations.RunPython.noop),
    ]

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connections, transaction
from django.db.utils import OperationalError, ProgrammingError


MODEL_LABELS = (
    "chatgpt.ChatgptAccount",
    "chatgpt.ChatgptCar",
    "accounts.User",
    "accounts.VisitLog",
    "authtoken.Token",
    "billing.Plan",
    "billing.PlanOffer",
    "billing.Subscription",
    "billing.Order",
    "billing.PaymentTransaction",
    "billing.PoolAccountPolicy",
    "billing.PoolReservation",
    "billing.AccountAssignment",
    "billing.AccountAssignmentEvent",
    "billing.UsageEvent",
    "billing.Announcement",
    "billing.UserNotification",
    "billing.AuditLog",
)


class Command(BaseCommand):
    help = "将 legacy_sqlite 数据安全复制到已完成迁移的 PostgreSQL default 数据库"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--batch-size", type=int, default=200)

    def handle(self, *args, **options):
        if "legacy_sqlite" not in connections:
            raise CommandError("请设置 MIGRATION_SOURCE_SQLITE_PATH")
        if connections["default"].vendor != "postgresql":
            raise CommandError("default 必须是 PostgreSQL，禁止覆盖当前 SQLite")
        if connections["legacy_sqlite"].vendor != "sqlite":
            raise CommandError("legacy_sqlite 必须指向 SQLite 数据库")

        dry_run = options["dry_run"]
        batch_size = max(options["batch_size"], 1)
        copied_models = []
        with transaction.atomic(using="default"):
            for label in MODEL_LABELS:
                model = apps.get_model(label)
                try:
                    source_objects = list(model.objects.using("legacy_sqlite").all())
                except (OperationalError, ProgrammingError):
                    self.stdout.write(f"跳过 {label}：源数据库没有对应表")
                    continue
                self.stdout.write(f"{label}: {len(source_objects)} 条")
                if dry_run or not source_objects:
                    continue
                concrete_fields = [field for field in model._meta.concrete_fields]
                update_fields = [field.name for field in concrete_fields if not field.primary_key]
                model.objects.using("default").bulk_create(
                    source_objects,
                    batch_size=batch_size,
                    update_conflicts=True,
                    update_fields=update_fields,
                    unique_fields=[model._meta.pk.name],
                )
                copied_models.append(model)

            if not dry_run:
                sql = connections["default"].ops.sequence_reset_sql(no_style(), copied_models)
                with connections["default"].cursor() as cursor:
                    for statement in sql:
                        cursor.execute(statement)

            if dry_run:
                transaction.set_rollback(True, using="default")

        mode = "只读检查" if dry_run else "迁移"
        self.stdout.write(self.style.SUCCESS(f"SQLite 到 PostgreSQL {mode}完成"))

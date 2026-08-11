import hashlib
import json

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.core.serializers.json import DjangoJSONEncoder
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


def model_digest(model, alias, batch_size=200):
    fields = [field.name for field in model._meta.concrete_fields]
    pk_name = model._meta.pk.name
    digest = hashlib.sha256()
    count = 0
    queryset = model.objects.using(alias).order_by(pk_name).values_list(*fields)
    for row in queryset.iterator(chunk_size=batch_size):
        digest.update(
            json.dumps(
                row,
                cls=DjangoJSONEncoder,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        digest.update(b"\n")
        count += 1
    return count, digest.hexdigest()


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
        source_signatures = []
        with transaction.atomic(using="default"):
            for label in MODEL_LABELS:
                model = apps.get_model(label)
                try:
                    source_objects = list(model.objects.using("legacy_sqlite").all())
                except (OperationalError, ProgrammingError):
                    self.stdout.write(f"跳过 {label}：源数据库没有对应表")
                    continue
                self.stdout.write(f"{label}: {len(source_objects)} 条")
                if not dry_run:
                    source_signatures.append((label, model, *model_digest(model, "legacy_sqlite", batch_size)))
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

                verification_errors = []
                for label, model, source_count, source_digest in source_signatures:
                    target_count, target_digest = model_digest(model, "default", batch_size)
                    if target_count != source_count or target_digest != source_digest:
                        verification_errors.append(
                            f"{label}: source={source_count}/{source_digest[:12]} "
                            f"target={target_count}/{target_digest[:12]}"
                        )
                    else:
                        self.stdout.write(f"校验通过 {label}: {target_count} 条")
                if verification_errors:
                    raise CommandError("迁移校验失败：" + "; ".join(verification_errors))

            if dry_run:
                transaction.set_rollback(True, using="default")

        mode = "只读检查" if dry_run else "迁移"
        self.stdout.write(self.style.SUCCESS(f"SQLite 到 PostgreSQL {mode}完成"))

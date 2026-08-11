import time

from django.core.management.base import BaseCommand
from django.db import transaction

from app.billing.models import Plan, PlanOffer, PoolTier
from app.chatgpt.models import ChatgptCar


class Command(BaseCommand):
    help = "创建普通/高级 Plus 号池以及 58/98 元默认月套餐"

    @transaction.atomic
    def handle(self, *args, **options):
        now = int(time.time())
        standard_pool, standard_pool_created = ChatgptCar.objects.get_or_create(
            car_name="Plus 普通号池",
            defaults={
                "remark": "普通套餐专用，单账号可配置 3-8 人",
                "gpt_account_list": [],
                "created_time": now,
                "updated_time": now,
            },
        )
        premium_pool, premium_pool_created = ChatgptCar.objects.get_or_create(
            car_name="Plus 高级号池",
            defaults={
                "remark": "高级套餐专用，单账号固定最多 3 人",
                "gpt_account_list": [],
                "created_time": now,
                "updated_time": now,
            },
        )
        standard_plan, standard_plan_created = Plan.objects.get_or_create(
            code="standard-plus",
            defaults={
                "name": "普通套餐",
                "tagline": "5-8 人共享 Plus 号池",
                "pool": standard_pool,
                "pool_tier": PoolTier.STANDARD,
                "sort_order": 10,
            },
        )
        premium_plan, premium_plan_created = Plan.objects.get_or_create(
            code="premium-plus",
            defaults={
                "name": "高级套餐",
                "tagline": "1-3 人 Plus 号池",
                "pool": premium_pool,
                "pool_tier": PoolTier.PREMIUM,
                "sort_order": 20,
            },
        )
        PlanOffer.objects.get_or_create(
            plan=standard_plan,
            code="monthly",
            defaults={
                "name": "月套餐",
                "months": 1,
                "price_cents": 5800,
                "currency": "CNY",
            },
        )
        PlanOffer.objects.get_or_create(
            plan=premium_plan,
            code="monthly",
            defaults={
                "name": "月套餐",
                "months": 1,
                "price_cents": 9800,
                "currency": "CNY",
            },
        )
        for plan in (standard_plan, premium_plan):
            PlanOffer.objects.get_or_create(
                plan=plan,
                code="quarterly",
                defaults={
                    "name": "季套餐",
                    "months": 3,
                    "price_cents": 0,
                    "currency": "CNY",
                    "is_draft": True,
                    "is_purchase_enabled": False,
                },
            )

        created_count = sum((
            standard_pool_created,
            premium_pool_created,
            standard_plan_created,
            premium_plan_created,
        ))
        self.stdout.write(self.style.SUCCESS(
            f"套餐初始化完成；新增核心对象 {created_count} 个。已有价格和后台配置未被覆盖。"
        ))

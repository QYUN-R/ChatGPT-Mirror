from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.test import APIClient

from app.accounts.models import User
from app.accounts.views import UserChatGPTAccountList, UserRelateGPTCarView
from app.billing.exceptions import BillingError, CapacityUnavailable, PaymentRejected, SubscriptionInactive
from app.billing.models import (
    AccountAssignment,
    AccountAssignmentEvent,
    Announcement,
    Order,
    OrderStatus,
    PaymentTransaction,
    Plan,
    PlanOffer,
    PlanPool,
    PoolAccountPolicy,
    PoolTier,
    SupportContact,
    Subscription,
    SubscriptionStatus,
    UserNotification,
)
from app.billing.payment import PaymentEvent
from app.billing.services import (
    add_months,
    complete_order,
    create_order,
    ensure_assignment,
    refresh_subscription_state,
    refund_order,
    resolve_managed_account,
    archive_upstream_account,
    sync_commercial_pool_accounts,
)
from app.chatgpt.models import ChatgptAccount, ChatgptCar
from app.chatgpt.views.chatgpt import ChatGPTLoginView
from app.chatgpt.views.gptcar import GptCarEnum, GptCarView
from app.billing.admin_views import AdminPlanView, AdminPoolView
from app.fields import decrypt_value
import json


@override_settings(
    BILLING_ENABLED=True,
    BILLING_ENFORCE_SUBSCRIPTION=False,
    BILLING_MOCK_PAYMENTS=True,
    BILLING_ORDER_HOLD_MINUTES=30,
)
class BillingServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="billing-user", password="Strong-password-123!")
        self.standard_pool = ChatgptCar.objects.create(
            car_name="Plus 普通号池",
            gpt_account_list=[],
            is_commercial=True,
            created_time=1,
            updated_time=1,
        )
        self.premium_pool = ChatgptCar.objects.create(
            car_name="Plus 高级号池",
            gpt_account_list=[],
            is_commercial=True,
            created_time=1,
            updated_time=1,
        )
        self.standard_plan = Plan.objects.create(
            code="standard-plus",
            name="普通套餐",
            tagline="稳定的 Plus 号池服务",
            pool=self.standard_pool,
            pool_tier=PoolTier.STANDARD,
        )
        self.premium_plan = Plan.objects.create(
            code="premium-plus",
            name="高级套餐",
            tagline="高优先级 Plus 号池服务",
            pool=self.premium_pool,
            pool_tier=PoolTier.PREMIUM,
        )
        self.standard_offer = PlanOffer.objects.create(
            plan=self.standard_plan,
            code="monthly",
            name="月套餐",
            months=1,
            price_cents=5800,
        )
        self.premium_offer = PlanOffer.objects.create(
            plan=self.premium_plan,
            code="monthly",
            name="月套餐",
            months=1,
            price_cents=9800,
        )
        self.standard_accounts = [
            self.create_account("standard-a@example.com"),
            self.create_account("standard-b@example.com"),
        ]
        self.premium_account = self.create_account("premium-a@example.com")
        for account in self.standard_accounts:
            PoolAccountPolicy.objects.create(
                pool=self.standard_pool,
                account=account,
                tier=PoolTier.STANDARD,
                binding_limit=5,
            )
        PoolAccountPolicy.objects.create(
            pool=self.premium_pool,
            account=self.premium_account,
            tier=PoolTier.PREMIUM,
            binding_limit=3,
        )

    @staticmethod
    def create_account(username):
        return ChatgptAccount.objects.create(
            chatgpt_username=username,
            plan_type="plus",
            access_token="test-access-token",
            auth_status=True,
            access_token_valid=True,
            created_time=1,
            updated_time=1,
        )

    @staticmethod
    def payment_event(
        order,
        *,
        event_id=None,
        amount_cents=None,
        verified=True,
        event_type="PAYMENT_SUCCEEDED",
        provider_transaction_id=None,
        occurred_at=None,
    ):
        return PaymentEvent(
            event_id=event_id or f"event-{uuid4().hex}",
            event_type=event_type,
            provider_transaction_id=provider_transaction_id or f"tx-{uuid4().hex}",
            amount_cents=order.price_cents if amount_cents is None else amount_cents,
            currency=order.currency,
            signature_verified=verified,
            occurred_at=occurred_at or timezone.now(),
            payload={"test": True},
        )

    def purchase(self, user=None, offer=None):
        user = user or self.user
        order = create_order(user, offer or self.standard_offer, provider="mock")
        order, subscription = complete_order(order, self.payment_event(order))
        return order, subscription

    def test_binding_limits_are_independently_configurable_for_each_tier(self):
        standard = PoolAccountPolicy(
            pool=self.standard_pool,
            account=self.create_account("standard-configurable@example.com"),
            tier=PoolTier.STANDARD,
            binding_limit=12,
        )
        standard.full_clean()

        premium = PoolAccountPolicy(
            pool=self.premium_pool,
            account=self.create_account("premium-configurable@example.com"),
            tier=PoolTier.PREMIUM,
            binding_limit=7,
        )
        premium.full_clean()

        invalid = PoolAccountPolicy(
            pool=self.standard_pool,
            account=self.create_account("invalid-limit@example.com"),
            tier=PoolTier.STANDARD,
            binding_limit=0,
        )
        with self.assertRaises(DjangoValidationError):
            invalid.full_clean()

    def test_standard_and_premium_tiers_cannot_share_one_pool(self):
        conflicting_plan = Plan(
            code="invalid-shared-pool",
            name="错误高级套餐",
            pool=self.standard_pool,
            pool_tier=PoolTier.PREMIUM,
        )
        with self.assertRaises(DjangoValidationError):
            conflicting_plan.save()

        conflicting_policy = PoolAccountPolicy(
            pool=self.standard_pool,
            account=self.create_account("invalid-shared-policy@example.com"),
            tier=PoolTier.PREMIUM,
            binding_limit=3,
        )
        with self.assertRaises(DjangoValidationError):
            conflicting_policy.save()

    def test_quarterly_draft_offer_cannot_be_purchased(self):
        draft = PlanOffer.objects.create(
            plan=self.standard_plan,
            code="quarterly",
            name="季套餐",
            months=3,
            price_cents=1,
            is_draft=True,
            is_purchase_enabled=False,
        )
        with self.assertRaises(BillingError):
            create_order(self.user, draft)

    def test_purchase_creates_subscription_and_fixed_assignment(self):
        order, subscription = self.purchase()
        self.assertEqual(order.status, OrderStatus.PAID)
        self.assertEqual(subscription.status, SubscriptionStatus.ACTIVE)
        first = ensure_assignment(subscription)
        second = ensure_assignment(subscription)
        self.assertEqual(first.account_id, second.account_id)
        self.assertTrue(AccountAssignment.objects.filter(user=self.user, active=True).exists())

    def test_assignment_migrates_only_inside_same_pool(self):
        _, subscription = self.purchase()
        first = ensure_assignment(subscription)
        first_policy = PoolAccountPolicy.objects.get(account=first.account)
        first_policy.health_status = "DISABLED"
        first_policy.enabled = False
        first_policy.save(update_fields=["health_status", "enabled", "updated_at"])

        migrated = ensure_assignment(subscription, reason="health_failure")
        self.assertNotEqual(first.account_id, migrated.account_id)
        self.assertIn(migrated.account_id, [item.id for item in self.standard_accounts])
        self.assertNotEqual(migrated.account_id, self.premium_account.id)
        self.assertTrue(AccountAssignmentEvent.objects.filter(event_type="MIGRATED").exists())

    def test_plan_can_allocate_across_multiple_linked_pools(self):
        second_pool = ChatgptCar.objects.create(
            car_name="Plus 普通备用号池",
            gpt_account_list=[],
            created_time=1,
            updated_time=1,
        )
        second_account = self.create_account("standard-pro@example.com")
        second_account.plan_type = "pro"
        second_account.save(update_fields=["plan_type"])
        self.standard_plan.pool_links.all().delete()
        PlanPool.objects.create(plan=self.standard_plan, pool=self.standard_pool, priority=0)
        PlanPool.objects.create(plan=self.standard_plan, pool=second_pool, priority=1)
        self.standard_pool.billing_policies.update(binding_limit=1)
        PoolAccountPolicy.objects.create(
            pool=second_pool,
            account=second_account,
            tier=PoolTier.STANDARD,
            binding_limit=2,
        )

        first_users = [
            User.objects.create_user(username=f"multi-pool-first-{index}", password="Strong-password-123!")
            for index in range(2)
        ]
        assignments = []
        for user in first_users:
            _, first_subscription = self.purchase(user=user)
            assignments.append(ensure_assignment(first_subscription))
        third_user = User.objects.create_user(username="multi-pool-third", password="Strong-password-123!")
        _, subscription = self.purchase(user=third_user)
        assignments.append(ensure_assignment(subscription))

        self.assertEqual({item.pool_id for item in assignments}, {self.standard_pool.id, second_pool.id})
        self.assertIn(second_account.id, {item.account_id for item in assignments})

    def test_plan_user_limit_stops_new_purchase_before_pool_capacity(self):
        self.standard_plan.user_limit = 1
        self.standard_plan.save(update_fields=["user_limit", "updated_at"])
        first_user = User.objects.create_user(username="plan-limit-first", password="Strong-password-123!")
        second_user = User.objects.create_user(username="plan-limit-second", password="Strong-password-123!")
        self.purchase(user=first_user)

        with self.assertRaises(CapacityUnavailable):
            create_order(second_user, self.standard_offer, provider="mock")

    def test_plan_quotas_are_applied_on_purchase_and_upgrade(self):
        self.standard_plan.daily_quota = 25
        self.standard_plan.monthly_quota = 500
        self.standard_plan.save(update_fields=["daily_quota", "monthly_quota", "updated_at"])
        self.premium_plan.daily_quota = 60
        self.premium_plan.monthly_quota = 1200
        self.premium_plan.save(update_fields=["daily_quota", "monthly_quota", "updated_at"])

        self.purchase()
        self.user.refresh_from_db()
        self.assertEqual(self.user.daily_quota, 25)
        self.assertEqual(self.user.monthly_quota, 500)

        upgrade = create_order(self.user, self.premium_offer, provider="mock")
        complete_order(upgrade, self.payment_event(upgrade))
        self.user.refresh_from_db()
        self.assertEqual(self.user.daily_quota, 60)
        self.assertEqual(self.user.monthly_quota, 1200)

    def test_premium_pool_stops_at_its_configured_capacity(self):
        users = [
            User.objects.create_user(username=f"premium-{index}", password="Strong-password-123!")
            for index in range(4)
        ]
        for user in users[:3]:
            self.purchase(user=user, offer=self.premium_offer)
        with self.assertRaises(CapacityUnavailable):
            create_order(users[3], self.premium_offer)

    def test_standard_pool_stops_at_configured_capacity(self):
        users = [User.objects.create(username=f"standard-capacity-{index}") for index in range(11)]
        for user in users[:10]:
            self.purchase(user=user, offer=self.standard_offer)
        with self.assertRaises(CapacityUnavailable):
            create_order(users[10], self.standard_offer)

    def test_renewal_extends_from_existing_expiry(self):
        _, subscription = self.purchase()
        original_end = subscription.ends_at
        renewal = create_order(self.user, self.standard_offer, provider="mock")
        _, renewed = complete_order(renewal, self.payment_event(renewal))
        self.assertEqual(renewed.ends_at, add_months(original_end, 1))

    def test_subscription_refresh_lock_query_avoids_nullable_outer_joins(self):
        self.purchase()

        with CaptureQueriesContext(connection) as captured:
            refresh_subscription_state(self.user)

        subscription_queries = [
            query["sql"]
            for query in captured.captured_queries
            if "billing_subscription" in query["sql"]
        ]
        self.assertTrue(subscription_queries)
        self.assertNotIn("LEFT OUTER JOIN", subscription_queries[0].upper())

    def test_upgrade_is_immediate_and_preserves_remaining_time(self):
        _, subscription = self.purchase()
        original_end = subscription.ends_at
        upgrade = create_order(self.user, self.premium_offer, provider="mock")
        _, upgraded = complete_order(upgrade, self.payment_event(upgrade))
        self.assertEqual(upgraded.plan_id, self.premium_plan.id)
        self.assertEqual(upgraded.ends_at, add_months(original_end, 1))
        assignment = ensure_assignment(upgraded)
        self.assertEqual(assignment.account_id, self.premium_account.id)

    def test_downgrade_stays_premium_until_current_period_ends(self):
        _, subscription = self.purchase(offer=self.premium_offer)
        premium_assignment = ensure_assignment(subscription)
        downgrade = create_order(self.user, self.standard_offer, provider="mock")
        _, scheduled = complete_order(downgrade, self.payment_event(downgrade))
        self.assertEqual(scheduled.plan_id, self.premium_plan.id)
        self.assertEqual(scheduled.scheduled_plan_id, self.standard_plan.id)
        self.assertEqual(ensure_assignment(scheduled).account_id, premium_assignment.account_id)

        scheduled.ends_at = timezone.now() - timedelta(minutes=1)
        scheduled.save(update_fields=["ends_at", "updated_at"])
        applied = refresh_subscription_state(self.user)
        self.assertEqual(applied.plan_id, self.standard_plan.id)
        self.assertIsNone(applied.scheduled_plan_id)
        self.assertEqual(ensure_assignment(applied).pool_id, self.standard_pool.id)

    def test_duplicate_callback_does_not_add_time_twice(self):
        order = create_order(self.user, self.standard_offer, provider="mock")
        event = self.payment_event(order, event_id="same-event")
        _, subscription = complete_order(order, event)
        first_end = subscription.ends_at
        _, duplicate = complete_order(order, event)
        self.assertEqual(duplicate.ends_at, first_end)
        self.assertEqual(PaymentTransaction.objects.filter(event_id="same-event").count(), 1)

    def test_amount_mismatch_is_recorded_and_does_not_activate(self):
        order = create_order(self.user, self.standard_offer, provider="mock")
        with self.assertRaises(PaymentRejected):
            complete_order(order, self.payment_event(order, amount_cents=5700))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PENDING)
        transaction = PaymentTransaction.objects.get(order=order)
        self.assertFalse(transaction.accepted)

    def test_forged_signature_does_not_activate_subscription(self):
        order = create_order(self.user, self.standard_offer, provider="mock")
        with self.assertRaises(PaymentRejected):
            complete_order(order, self.payment_event(order, verified=False))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertFalse(PaymentTransaction.objects.get(order=order).accepted)

    def test_expired_callback_does_not_activate_subscription(self):
        order = create_order(self.user, self.standard_offer, provider="mock")
        with self.assertRaises(PaymentRejected):
            complete_order(
                order,
                self.payment_event(order, occurred_at=timezone.now() - timedelta(minutes=16)),
            )
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PENDING)

    def test_non_success_event_does_not_activate_subscription(self):
        order = create_order(self.user, self.standard_offer, provider="mock")
        with self.assertRaises(PaymentRejected):
            complete_order(order, self.payment_event(order, event_type="PAYMENT_CLOSED"))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PENDING)

    def test_provider_transaction_id_cannot_pay_two_orders(self):
        first_order = create_order(self.user, self.standard_offer, provider="mock")
        transaction_id = "provider-tx-shared"
        complete_order(
            first_order,
            self.payment_event(first_order, provider_transaction_id=transaction_id),
        )

        second_user = User.objects.create(username="duplicate-provider-transaction")
        second_order = create_order(second_user, self.standard_offer, provider="mock")
        with self.assertRaises(PaymentRejected):
            complete_order(
                second_order,
                self.payment_event(second_order, provider_transaction_id=transaction_id),
            )
        second_order.refresh_from_db()
        self.assertEqual(second_order.status, OrderStatus.PENDING)
        self.assertEqual(
            PaymentTransaction.objects.filter(
                provider="mock",
                provider_transaction_id=transaction_id,
                accepted=True,
            ).count(),
            1,
        )

    def test_payment_payload_secrets_are_redacted(self):
        order = create_order(self.user, self.standard_offer, provider="mock")
        event = self.payment_event(order)
        event = PaymentEvent(
            **{**event.__dict__, "payload": {"access_token": "secret", "nested": {"cookie": "secret"}}}
        )
        complete_order(order, event)
        payload = PaymentTransaction.objects.get(event_id=event.event_id).payload
        self.assertEqual(payload["access_token"], "[redacted]")
        self.assertEqual(payload["nested"]["cookie"], "[redacted]")

    def test_subscription_user_receives_all_healthy_accounts_from_its_plan_only(self):
        _, subscription = self.purchase()
        assignment = ensure_assignment(subscription)
        factory = APIRequestFactory()
        request = factory.get("/0x/user/chatgpt-list")
        force_authenticate(request, user=self.user)
        with patch("app.accounts.views.req_gateway", return_value={}):
            response = UserChatGPTAccountList.as_view()(request)
        self.assertTrue(response.data["managed_assignment"])
        self.assertEqual(len(response.data["results"]), 2)
        visible_policy_ids = {item["id"] for item in response.data["results"]}
        self.assertEqual(
            visible_policy_ids,
            set(PoolAccountPolicy.objects.filter(account__in=self.standard_accounts).values_list("id", flat=True)),
        )
        self.assertNotIn(
            PoolAccountPolicy.objects.get(account=self.premium_account).id,
            visible_policy_ids,
        )
        current = next(item for item in response.data["results"] if item["is_current"])
        self.assertEqual(
            PoolAccountPolicy.objects.get(pk=current["id"]).account_id,
            assignment.account_id,
        )
        self.assertEqual(response.data["results"][0]["chatgpt_flag"], "套餐账号 01")
        self.assertEqual(response.data["results"][0]["default_login_mode"], "web")

    def test_managed_user_can_select_another_account_in_same_plan_pool(self):
        _, subscription = self.purchase()
        assignment = ensure_assignment(subscription)
        target_policy = PoolAccountPolicy.objects.filter(
            pool=self.standard_pool,
        ).exclude(account=assignment.account).get()
        request = APIRequestFactory().post(
            "/0x/chatgpt/login",
            {"chatgpt_id": target_policy.id, "login_mode": "api"},
            format="json",
            HTTP_USER_AGENT="managed-account-switch-test",
        )
        force_authenticate(request, user=self.user)
        with patch("app.chatgpt.views.chatgpt.req_gateway", side_effect=[{}, {"message": "ok"}]) as gateway:
            response = ChatGPTLoginView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        assignment.refresh_from_db()
        self.assertEqual(assignment.account_id, target_policy.account_id)
        self.assertEqual(gateway.call_args_list[1].kwargs["json"]["access_token"], target_policy.account.access_token)

    def test_managed_user_cannot_select_account_from_another_tier(self):
        self.purchase()
        premium_policy = PoolAccountPolicy.objects.get(account=self.premium_account)
        request = APIRequestFactory().post(
            "/0x/chatgpt/login",
            {"chatgpt_id": premium_policy.id, "login_mode": "api"},
            format="json",
        )
        force_authenticate(request, user=self.user)
        response = ChatGPTLoginView.as_view()(request)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "invalid_account_choice")

    def test_used_upstream_account_cannot_be_deleted(self):
        _, subscription = self.purchase()
        assignment = ensure_assignment(subscription)
        with self.assertRaises(BillingError) as captured:
            archive_upstream_account(assignment.account)
        self.assertEqual(captured.exception.code, "account_in_use")
        assignment.account.refresh_from_db()
        self.assertFalse(assignment.account.is_archived)

    def test_unused_upstream_account_delete_clears_credentials_and_pool_membership(self):
        account = self.standard_accounts[1]
        self.standard_pool.gpt_account_list = [account.id]
        self.standard_pool.save(update_fields=["gpt_account_list"])

        archive_upstream_account(account)

        account.refresh_from_db()
        self.standard_pool.refresh_from_db()
        self.assertTrue(account.is_archived)
        self.assertEqual(account.access_token, "")
        self.assertIsNone(account.session_token)
        self.assertFalse(PoolAccountPolicy.objects.filter(account=account).exists())
        self.assertNotIn(account.id, self.standard_pool.gpt_account_list)

    def test_legacy_pool_api_hides_commercial_pools(self):
        free_pool = ChatgptCar.objects.create(
            car_name="free-only-pool",
            gpt_account_list=[],
            created_time=1,
            updated_time=1,
        )
        admin = User.objects.create_superuser(username="pool-admin", password="Strong-password-123!")
        request = APIRequestFactory().get("/0x/chatgpt/car-enum")
        force_authenticate(request, user=admin)

        response = GptCarEnum.as_view()(request)

        self.assertEqual(response.status_code, 200)
        visible_ids = {item["id"] for item in response.data["data"]}
        self.assertIn(free_pool.id, visible_ids)
        self.assertNotIn(self.standard_pool.id, visible_ids)
        self.assertNotIn(self.premium_pool.id, visible_ids)

    def test_legacy_pool_api_rejects_commercial_accounts(self):
        free_pool = ChatgptCar.objects.create(
            car_name="free-edit-pool",
            gpt_account_list=[],
            created_time=1,
            updated_time=1,
        )
        admin = User.objects.create_superuser(username="pool-edit-admin", password="Strong-password-123!")
        request = APIRequestFactory().post(
            "/0x/chatgpt/car",
            {
                "id": free_pool.id,
                "car_name": free_pool.car_name,
                "gpt_account_list": [self.standard_accounts[0].id],
                "remark": "",
            },
            format="json",
        )
        force_authenticate(request, user=admin)

        response = GptCarView.as_view()(request)

        self.assertEqual(response.status_code, 400)
        self.assertIn("商业号池", str(response.data["message"]))

    def test_pool_centric_sync_adds_multiple_accounts_and_preserves_existing_limits(self):
        existing_policy = PoolAccountPolicy.objects.get(account=self.standard_accounts[0])
        existing_policy.binding_limit = 7
        existing_policy.save(update_fields=["binding_limit", "updated_at"])
        new_account = self.create_account("standard-c@example.com")

        sync_commercial_pool_accounts(
            self.standard_pool,
            tier=PoolTier.STANDARD,
            account_ids=[self.standard_accounts[0].id, new_account.id],
            default_binding_limit=4,
        )

        existing_policy.refresh_from_db()
        new_policy = PoolAccountPolicy.objects.get(account=new_account)
        self.standard_pool.refresh_from_db()
        self.assertEqual(existing_policy.binding_limit, 7)
        self.assertEqual(new_policy.binding_limit, 4)
        self.assertEqual(new_policy.pool_id, self.standard_pool.id)
        self.assertEqual(
            set(self.standard_pool.gpt_account_list),
            {self.standard_accounts[0].id, new_account.id},
        )
        self.assertFalse(PoolAccountPolicy.objects.filter(account=self.standard_accounts[1]).exists())

    def test_pool_centric_sync_rejects_account_that_belongs_to_another_pool(self):
        with self.assertRaises(BillingError) as captured:
            sync_commercial_pool_accounts(
                self.standard_pool,
                tier=PoolTier.STANDARD,
                account_ids=[self.premium_account.id],
            )
        self.assertEqual(captured.exception.code, "account_already_in_pool")

    def test_pool_centric_sync_cannot_remove_an_account_with_active_users(self):
        _, subscription = self.purchase()
        assignment = ensure_assignment(subscription)
        remaining_ids = [
            account.id for account in self.standard_accounts if account.id != assignment.account_id
        ]

        with self.assertRaises(BillingError) as captured:
            sync_commercial_pool_accounts(
                self.standard_pool,
                tier=PoolTier.STANDARD,
                account_ids=remaining_ids,
            )

        self.assertEqual(captured.exception.code, "account_in_use")
        self.assertTrue(PoolAccountPolicy.objects.filter(account_id=assignment.account_id).exists())

    def test_pool_centric_sync_cannot_reduce_limit_below_active_bindings(self):
        _, subscription = self.purchase()
        assignment = ensure_assignment(subscription)

        with self.assertRaises(BillingError) as captured:
            sync_commercial_pool_accounts(
                self.standard_pool,
                tier=PoolTier.STANDARD,
                account_ids=[account.id for account in self.standard_accounts],
                default_binding_limit=0,
                apply_binding_limit=True,
            )

        self.assertEqual(captured.exception.code, "invalid_binding_limit")

    def test_legacy_bulk_binding_rejects_commercial_pool_and_managed_user(self):
        admin = User.objects.create_superuser(username="legacy-bind-admin", password="Strong-password-123!")
        plain_user = User.objects.create_user(username="legacy-bind-user", password="Strong-password-123!")
        request = APIRequestFactory().post(
            "/0x/user/relat-gptcar",
            {"user_id_list": [plain_user.id], "gptcar_id_list": [self.standard_pool.id]},
            format="json",
        )
        force_authenticate(request, user=admin)
        response = UserRelateGPTCarView.as_view()(request)
        self.assertEqual(response.status_code, 400)

        free_pool = ChatgptCar.objects.create(
            car_name="legacy-free-pool",
            gpt_account_list=[],
            created_time=1,
            updated_time=1,
        )
        self.purchase()
        request = APIRequestFactory().post(
            "/0x/user/relat-gptcar",
            {"user_id_list": [self.user.id], "gptcar_id_list": [free_pool.id]},
            format="json",
        )
        force_authenticate(request, user=admin)
        response = UserRelateGPTCarView.as_view()(request)
        self.assertEqual(response.status_code, 400)

    def test_admin_pool_api_returns_one_row_per_commercial_pool(self):
        admin = User.objects.create_superuser(username="pool-view-admin", password="Strong-password-123!")
        request = APIRequestFactory().get("/0x/admin/pools")
        force_authenticate(request, user=admin)

        response = AdminPoolView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        rows = {row["id"]: row for row in response.data["commercial_pools"]}
        self.assertEqual(rows[self.standard_pool.id]["account_count"], 2)
        self.assertEqual(rows[self.standard_pool.id]["total_capacity"], 10)
        self.assertEqual(rows[self.premium_pool.id]["account_count"], 1)

    def test_admin_can_create_commercial_pool_with_multiple_accounts(self):
        admin = User.objects.create_superuser(username="pool-create-admin", password="Strong-password-123!")
        first = self.create_account("new-pool-first@example.com")
        second = self.create_account("new-pool-second@example.com")
        request = APIRequestFactory().post(
            "/0x/admin/pools",
            {
                "action": "create_pool",
                "pool_name": "Plus 普通备用池",
                "tier": PoolTier.STANDARD,
                "account_ids": [first.id, second.id],
                "default_binding_limit": 6,
            },
            format="json",
        )
        force_authenticate(request, user=admin)

        response = AdminPoolView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        pool = ChatgptCar.objects.get(pk=response.data["pool_id"])
        self.assertEqual(set(pool.gpt_account_list), {first.id, second.id})
        self.assertEqual(
            set(PoolAccountPolicy.objects.filter(pool=pool).values_list("binding_limit", flat=True)),
            {6},
        )

    def test_plan_api_does_not_offer_free_only_pool(self):
        free_pool = ChatgptCar.objects.create(
            car_name="plan-free-only-pool",
            gpt_account_list=[],
            created_time=1,
            updated_time=1,
        )
        admin = User.objects.create_superuser(username="plan-pool-admin", password="Strong-password-123!")
        request = APIRequestFactory().get("/0x/admin/plans")
        force_authenticate(request, user=admin)

        response = AdminPlanView.as_view()(request)

        pool_ids = {row["id"] for row in response.data["pools"]}
        self.assertNotIn(free_pool.id, pool_ids)
        self.assertIn(self.standard_pool.id, pool_ids)

    @override_settings(BILLING_ENABLED=False)
    def test_disabled_billing_keeps_legacy_account_selection(self):
        self.assertIsNone(resolve_managed_account(self.user))

    def test_expired_subscription_blocks_service_but_keeps_history(self):
        order, subscription = self.purchase()
        subscription.ends_at = timezone.now() - timedelta(minutes=1)
        subscription.save(update_fields=["ends_at", "updated_at"])
        with self.assertRaises(SubscriptionInactive):
            resolve_managed_account(self.user)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PAID)

    def test_refund_releases_assignment_but_keeps_business_history(self):
        order, subscription = self.purchase()
        assignment = ensure_assignment(subscription)
        refund_order(order)
        subscription.refresh_from_db()
        assignment.refresh_from_db()
        self.assertEqual(subscription.status, SubscriptionStatus.REFUNDED)
        self.assertFalse(assignment.active)
        self.assertTrue(AccountAssignmentEvent.objects.filter(assignment=assignment).exists())
        self.assertTrue(PaymentTransaction.objects.filter(order=order).exists())

    def test_archived_plan_keeps_existing_subscription_usable(self):
        _, subscription = self.purchase()
        assignment = ensure_assignment(subscription)
        self.standard_plan.is_archived = True
        self.standard_plan.is_active = False
        self.standard_plan.save(update_fields=["is_archived", "is_active", "updated_at"])
        self.assertEqual(ensure_assignment(subscription).account_id, assignment.account_id)
        another_user = User.objects.create(username="archived-plan-new-user")
        with self.assertRaises(BillingError):
            create_order(another_user, self.standard_offer)

    def test_managed_login_keeps_existing_model_and_quota_controls(self):
        self.user.model_limit = ["gpt-5"]
        self.user.daily_quota = 12
        self.user.monthly_quota = 120
        self.user.force_chat_mode = True
        self.user.save(update_fields=["model_limit", "daily_quota", "monthly_quota", "force_chat_mode"])
        self.purchase()
        request = APIRequestFactory().post(
            "/0x/chatgpt/login",
            {"login_mode": "api"},
            format="json",
            HTTP_USER_AGENT="billing-managed-login-test",
        )
        force_authenticate(request, user=self.user)
        with patch("app.chatgpt.views.chatgpt.req_gateway", side_effect=[{}, {"message": "ok"}]) as gateway:
            response = ChatGPTLoginView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        login_payload = gateway.call_args_list[1].kwargs["json"]
        self.assertEqual(login_payload["limits"], ["gpt-5"])
        self.assertEqual(login_payload["daily_quota"], 12)
        self.assertEqual(login_payload["monthly_quota"], 120)
        self.assertNotIn("chatgpt_id", login_payload)

    def test_managed_login_defaults_to_mixed_mode(self):
        for account in self.standard_accounts:
            account.session_token = "test-session-token"
            account.session_token_valid = True
            account.save(update_fields=["session_token", "session_token_valid", "updated_time"])

        self.purchase()
        request = APIRequestFactory().post(
            "/0x/chatgpt/login",
            {},
            format="json",
            HTTP_USER_AGENT="billing-default-mixed-mode-test",
        )
        force_authenticate(request, user=self.user)
        with patch("app.chatgpt.views.chatgpt.req_gateway", side_effect=[{}, {"message": "ok"}]) as gateway:
            response = ChatGPTLoginView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        login_payload = gateway.call_args_list[1].kwargs["json"]
        self.assertEqual(login_payload["login_mode"], "web")


@override_settings(
    BILLING_ENABLED=True,
    BILLING_ENFORCE_SUBSCRIPTION=True,
    BILLING_MOCK_PAYMENTS=True,
    PAYMENT_PROVIDER="mock",
    BILLING_ORDER_HOLD_MINUTES=30,
)
class BillingApiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="billing-admin", password="Strong-password-123!")
        self.user = User.objects.create_user(username="api-user@example.com", password="Strong-password-123!")
        self.pool = ChatgptCar.objects.create(
            car_name="API Plus Pool",
            gpt_account_list=[],
            is_commercial=True,
            created_time=1,
            updated_time=1,
        )
        self.plan = Plan.objects.create(
            code="api-standard",
            name="普通套餐",
            tagline="稳定的 Plus 号池服务",
            pool=self.pool,
            pool_tier=PoolTier.STANDARD,
        )
        self.offer = PlanOffer.objects.create(
            plan=self.plan,
            code="monthly",
            name="月套餐",
            months=1,
            price_cents=5800,
        )
        self.account = ChatgptAccount.objects.create(
            chatgpt_username="api-upstream@example.com",
            plan_type="plus",
            access_token="secret-upstream-token",
            auth_status=True,
            access_token_valid=True,
            created_time=1,
            updated_time=1,
        )
        PoolAccountPolicy.objects.create(
            pool=self.pool,
            account=self.account,
            tier=PoolTier.STANDARD,
            binding_limit=5,
        )
        self.client = APIClient()

    def test_user_can_mock_purchase_and_read_subscription(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/0x/billing/orders",
            {"offer_id": self.offer.id, "idempotency_key": "api-purchase-1", "pay_now": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["order"]["status"], "PAID")
        me = self.client.get("/0x/billing/me")
        self.assertTrue(me.data["service_available"])
        self.assertEqual(me.data["subscription"]["plan"]["name"], "普通套餐")

    @override_settings(BILLING_MOCK_PAYMENTS=False, PAYMENT_PROVIDER="manual")
    def test_user_checkout_is_rejected_without_a_configured_payment_provider(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(
            "/0x/billing/orders",
            {"offer_id": self.offer.id, "idempotency_key": "unconfigured-checkout"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "checkout_unavailable")
        self.assertFalse(Order.objects.filter(user=self.user).exists())
        self.assertFalse(self.user.pool_reservations.exists())
        plans = self.client.get("/0x/billing/plans")
        self.assertFalse(plans.data["checkout_available"])

    def test_user_plan_payload_hides_capacity_but_keeps_purchase_availability(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/0x/billing/plans")
        self.assertEqual(response.status_code, 200)
        plan = response.data["plans"][0]
        self.assertNotIn("capacity", plan)
        self.assertTrue(plan["purchase_available"])

        self.client.force_authenticate(self.admin)
        admin_response = self.client.get("/0x/admin/plans")
        self.assertEqual(admin_response.status_code, 200)
        self.assertIn("capacity", admin_response.data["plans"][0])

    def test_admin_can_link_multiple_pools_and_set_plan_limits(self):
        second_pool = ChatgptCar.objects.create(
            car_name="API Pro Pool",
            gpt_account_list=[],
            created_time=1,
            updated_time=1,
        )
        self.client.force_authenticate(self.user)
        purchased = self.client.post(
            "/0x/billing/orders",
            {"offer_id": self.offer.id, "idempotency_key": "plan-limit-sync", "pay_now": True},
            format="json",
        )
        self.assertEqual(purchased.status_code, 200)
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/0x/admin/plans",
            {
                "action": "save_plan",
                "id": self.plan.id,
                "code": self.plan.code,
                "name": self.plan.name,
                "tagline": self.plan.tagline,
                "pool_ids": [self.pool.id, second_pool.id],
                "pool_tier": PoolTier.STANDARD,
                "user_limit": 80,
                "daily_quota": 30,
                "monthly_quota": 600,
                "is_active": True,
                "is_public": True,
                "sort_order": 0,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["plan"]["pool_ids"], [self.pool.id, second_pool.id])
        self.assertEqual(response.data["plan"]["user_limit"], 80)
        self.assertEqual(response.data["plan"]["daily_quota"], 30)
        self.assertEqual(response.data["plan"]["monthly_quota"], 600)
        self.user.refresh_from_db()
        self.assertEqual(self.user.daily_quota, 30)
        self.assertEqual(self.user.monthly_quota, 600)

    def test_admin_can_view_active_account_users_without_upstream_credentials(self):
        self.user.email = "bound-user@example.com"
        self.user.email_verified_at = timezone.now()
        self.user.save(update_fields=["email", "email_verified_at"])
        order = create_order(self.user, self.offer, provider="mock")
        _, subscription = complete_order(order, BillingServiceTests.payment_event(order))
        assignment = ensure_assignment(subscription)
        assignment.last_used_at = timezone.now()
        assignment.save(update_fields=["last_used_at", "updated_at"])

        inactive_user = User.objects.create_user(
            username="inactive-binding@example.com",
            password="Strong-password-123!",
        )
        inactive_subscription = Subscription.objects.create(
            user=inactive_user,
            plan=self.plan,
            offer=self.offer,
            status=SubscriptionStatus.ACTIVE,
            source="manual",
            starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(days=30),
        )
        AccountAssignment.objects.create(
            subscription=inactive_subscription,
            user=inactive_user,
            pool=self.pool,
            account=self.account,
            active=False,
        )
        self.account.session_token = "secret-session-token"
        self.account.refresh_token = "secret-refresh-token"
        self.account.extra_cookies = [{"name": "session", "value": "secret-cookie-value"}]
        self.account.save(update_fields=[
            "session_token",
            "refresh_token",
            "extra_cookies",
            "updated_time",
        ])

        policy = PoolAccountPolicy.objects.get(account=self.account)
        self.client.force_authenticate(self.admin)
        response = self.client.get(f"/0x/admin/pools/{policy.id}/usage")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["policy"]["active_bindings"], 1)
        self.assertEqual(len(response.data["users"]), 1)
        self.assertEqual(response.data["users"][0]["username"], self.user.username)
        self.assertEqual(response.data["users"][0]["email"], self.user.email)
        self.assertTrue(response.data["users"][0]["email_verified"])
        self.assertEqual(response.data["users"][0]["plan_name"], self.plan.name)
        rendered = json.dumps(response.data, ensure_ascii=False)
        for secret in (
            "secret-upstream-token",
            "secret-session-token",
            "secret-refresh-token",
            "secret-cookie-value",
        ):
            self.assertNotIn(secret, rendered)
        for sensitive_field in (
            "access_token",
            "session_token",
            "refresh_token",
            "extra_cookies",
        ):
            self.assertNotIn(sensitive_field, rendered)

        self.client.force_authenticate(self.user)
        forbidden = self.client.get(f"/0x/admin/pools/{policy.id}/usage")
        self.assertEqual(forbidden.status_code, 403)

    def test_support_contacts_are_admin_managed_and_user_visible(self):
        png_data_uri = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9gqS8AAAAASUVORK5CYII="
        )
        self.client.force_authenticate(self.admin)
        created = self.client.post(
            "/0x/admin/support-contacts",
            {
                "action": "save",
                "name": "售后客服",
                "channel": "微信",
                "contact": "service-wechat",
                "description": "工作日回复",
                "qr_image": png_data_uri,
                "is_active": True,
                "sort_order": 10,
            },
            format="json",
        )
        self.assertEqual(created.status_code, 200)
        contact_id = created.data["contact"]["id"]

        self.client.force_authenticate(self.user)
        visible = self.client.get("/0x/billing/support-contacts")
        self.assertEqual(visible.status_code, 200)
        self.assertEqual(visible.data["contacts"][0]["id"], contact_id)
        self.assertEqual(visible.data["contacts"][0]["qr_image"], png_data_uri)

        self.client.force_authenticate(self.admin)
        hidden = self.client.post(
            "/0x/admin/support-contacts",
            {
                "action": "save",
                "id": contact_id,
                "name": "售后客服",
                "channel": "微信",
                "contact": "service-wechat",
                "description": "工作日回复",
                "qr_image": png_data_uri,
                "is_active": False,
                "sort_order": 10,
            },
            format="json",
        )
        self.assertEqual(hidden.status_code, 200)

        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get("/0x/billing/support-contacts").data["contacts"], [])

    def test_support_contact_rejects_svg_qr_upload(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/0x/admin/support-contacts",
            {
                "action": "save",
                "name": "售后客服",
                "channel": "微信",
                "contact": "service-wechat",
                "qr_image": "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SupportContact.objects.exists())

    def test_admin_manual_grant_and_user_list_show_plan(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/0x/admin/subscriptions",
            {"action": "grant", "user_id": self.user.id, "offer_id": self.offer.id},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        users = self.client.get("/0x/user/?page_size=100")
        row = next(item for item in users.data["results"] if item["id"] == self.user.id)
        self.assertEqual(row["subscription"]["plan_name"], "普通套餐")

    def test_published_announcement_creates_user_notification(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/0x/admin/announcements",
            {
                "action": "publish",
                "title": "服务器迁移",
                "content": "维护完成后将恢复服务。",
                "audience": "ALL",
                "category": "MIGRATION",
                "severity": "INFO",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.user.notifications.filter(title="服务器迁移").exists())

    def test_required_announcement_is_listed_until_user_acknowledges_it(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/0x/admin/announcements",
            {
                "action": "publish",
                "title": "必须阅读",
                "content": "请确认本次维护通知。",
                "audience": "ALL",
                "requires_acknowledgement": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        announcement = Announcement.objects.get(title="必须阅读")
        self.assertTrue(announcement.requires_acknowledgement)

        self.client.force_authenticate(self.user)
        listed = self.client.get(
            "/0x/billing/notifications?unread=1&requires_acknowledgement=1&page_size=10"
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.data["results"]), 1)
        notification = listed.data["results"][0]
        self.assertTrue(notification["requires_acknowledgement"])

        marked = self.client.post(
            f"/0x/billing/notifications/{notification['id']}/read",
            {},
            format="json",
        )
        self.assertEqual(marked.status_code, 200)
        listed_again = self.client.get(
            "/0x/billing/notifications?unread=1&requires_acknowledgement=1&page_size=10"
        )
        self.assertEqual(listed_again.data["results"], [])

    def test_notification_compatibility_routes_list_and_mark_read(self):
        notification = UserNotification.objects.create(
            user=self.user,
            title="维护通知",
            content="服务维护已经完成。",
        )
        self.client.force_authenticate(self.user)
        listed = self.client.get("/0x/notifications")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data["unread_count"], 1)
        marked = self.client.post(f"/0x/notifications/{notification.id}/read", {}, format="json")
        self.assertEqual(marked.status_code, 200)
        notification.refresh_from_db()
        self.assertIsNotNone(notification.read_at)

    @patch("app.accounts.views.backup.req_gateway", return_value={"version": 1})
    def test_encrypted_backup_contains_billing_models(self, _gateway):
        order = create_order(self.user, self.offer, provider="mock")
        complete_order(order, BillingServiceTests.payment_event(order))
        self.client.force_authenticate(self.admin)
        response = self.client.get("/0x/user/backup")
        self.assertEqual(response.status_code, 200)
        payload = json.loads(decrypt_value(response.data["archive"]))
        self.assertEqual(payload["version"], 2)
        self.assertIn("billing.Order", payload["billing"])
        self.assertIn("billing.SupportContact", payload["billing"])
        self.assertNotIn("secret-upstream-token", response.data["archive"])

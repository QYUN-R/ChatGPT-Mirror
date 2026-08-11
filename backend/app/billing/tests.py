from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.test import APIClient

from app.accounts.models import User
from app.accounts.views import UserChatGPTAccountList
from app.billing.exceptions import BillingError, CapacityUnavailable, PaymentRejected, SubscriptionInactive
from app.billing.models import (
    AccountAssignment,
    AccountAssignmentEvent,
    OrderStatus,
    PaymentTransaction,
    Plan,
    PlanOffer,
    PoolAccountPolicy,
    PoolTier,
    SubscriptionStatus,
)
from app.billing.payment import PaymentEvent
from app.billing.services import (
    add_months,
    complete_order,
    create_order,
    ensure_assignment,
    resolve_managed_account,
)
from app.chatgpt.models import ChatgptAccount, ChatgptCar
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
            created_time=1,
            updated_time=1,
        )
        self.premium_pool = ChatgptCar.objects.create(
            car_name="Plus 高级号池",
            gpt_account_list=[],
            created_time=1,
            updated_time=1,
        )
        self.standard_plan = Plan.objects.create(
            code="standard-plus",
            name="普通套餐",
            tagline="5-8 人共享号池",
            pool=self.standard_pool,
            pool_tier=PoolTier.STANDARD,
        )
        self.premium_plan = Plan.objects.create(
            code="premium-plus",
            name="高级套餐",
            tagline="1-3 人号池",
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
    def payment_event(order, *, event_id=None, amount_cents=None, verified=True):
        return PaymentEvent(
            event_id=event_id or f"event-{uuid4().hex}",
            event_type="PAYMENT_SUCCEEDED",
            provider_transaction_id=f"tx-{uuid4().hex}",
            amount_cents=order.price_cents if amount_cents is None else amount_cents,
            currency=order.currency,
            signature_verified=verified,
            payload={"test": True},
        )

    def purchase(self, user=None, offer=None):
        user = user or self.user
        order = create_order(user, offer or self.standard_offer, provider="mock")
        order, subscription = complete_order(order, self.payment_event(order))
        return order, subscription

    def test_standard_and_premium_binding_limits_are_enforced(self):
        policy = PoolAccountPolicy(
            pool=self.standard_pool,
            account=self.create_account("invalid-standard@example.com"),
            tier=PoolTier.STANDARD,
            binding_limit=9,
        )
        with self.assertRaises(DjangoValidationError):
            policy.full_clean()

        premium = PoolAccountPolicy(
            pool=self.premium_pool,
            account=self.create_account("invalid-premium@example.com"),
            tier=PoolTier.PREMIUM,
            binding_limit=4,
        )
        with self.assertRaises(DjangoValidationError):
            premium.full_clean()

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

    def test_premium_pool_stops_at_three_reserved_users(self):
        users = [
            User.objects.create_user(username=f"premium-{index}", password="Strong-password-123!")
            for index in range(4)
        ]
        for user in users[:3]:
            self.purchase(user=user, offer=self.premium_offer)
        with self.assertRaises(CapacityUnavailable):
            create_order(users[3], self.premium_offer)

    def test_renewal_extends_from_existing_expiry(self):
        _, subscription = self.purchase()
        original_end = subscription.ends_at
        renewal = create_order(self.user, self.standard_offer, provider="mock")
        _, renewed = complete_order(renewal, self.payment_event(renewal))
        self.assertEqual(renewed.ends_at, add_months(original_end, 1))

    def test_upgrade_is_immediate_and_preserves_remaining_time(self):
        _, subscription = self.purchase()
        original_end = subscription.ends_at
        upgrade = create_order(self.user, self.premium_offer, provider="mock")
        _, upgraded = complete_order(upgrade, self.payment_event(upgrade))
        self.assertEqual(upgraded.plan_id, self.premium_plan.id)
        self.assertEqual(upgraded.ends_at, add_months(original_end, 1))
        assignment = ensure_assignment(upgraded)
        self.assertEqual(assignment.account_id, self.premium_account.id)

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

    def test_subscription_user_receives_managed_account_without_real_id(self):
        _, subscription = self.purchase()
        ensure_assignment(subscription)
        factory = APIRequestFactory()
        request = factory.get("/0x/user/chatgpt-list")
        force_authenticate(request, user=self.user)
        with patch("app.accounts.views.req_gateway", return_value={}):
            response = UserChatGPTAccountList.as_view()(request)
        self.assertTrue(response.data["managed_assignment"])
        self.assertEqual(response.data["results"][0]["id"], 0)
        self.assertEqual(response.data["results"][0]["chatgpt_flag"], "套餐专属账号")

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


@override_settings(
    BILLING_ENABLED=True,
    BILLING_ENFORCE_SUBSCRIPTION=True,
    BILLING_MOCK_PAYMENTS=True,
    BILLING_ORDER_HOLD_MINUTES=30,
)
class BillingApiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="billing-admin", password="Strong-password-123!")
        self.user = User.objects.create_user(username="api-user@example.com", password="Strong-password-123!")
        self.pool = ChatgptCar.objects.create(
            car_name="API Plus Pool",
            gpt_account_list=[],
            created_time=1,
            updated_time=1,
        )
        self.plan = Plan.objects.create(
            code="api-standard",
            name="普通套餐",
            tagline="5-8 人共享 Plus 号池",
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
        self.assertNotIn("secret-upstream-token", response.data["archive"])

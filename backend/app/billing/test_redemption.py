import base64
import json
import threading
from datetime import timedelta

from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory

from app.accounts.models import User
from app.billing.exceptions import BillingError, CapacityUnavailable
from app.billing.models import (
    CommercialSettings,
    Order,
    OrderStatus,
    PaymentTransaction,
    Plan,
    PlanOffer,
    PoolAccountPolicy,
    PoolTier,
    RedemptionCode,
    RedemptionCodeStatus,
)
from app.billing.redemption import (
    archive_redeemed_codes,
    create_redemption_batch,
    redeem_code,
    revoke_redemption_codes,
)
from app.billing.services import add_months
from app.chatgpt.models import ChatgptAccount, ChatgptCar
from app.utils import get_redemption_client_ip


TEST_KEYRING = {
    "active": "v1",
    "keys": {"v1": base64.b64encode(b"test-redemption-key-material-32-bytes-minimum").decode()},
}


@override_settings(
    BILLING_ENABLED=True,
    BILLING_ENFORCE_SUBSCRIPTION=True,
    REDEMPTION_CODES_ENABLED=True,
    REDEMPTION_CODE_KEYRING=TEST_KEYRING,
    REDEMPTION_FAILURE_LIMIT=10,
    REDEMPTION_TRUSTED_PROXY_CIDRS=["127.0.0.0/8", "172.16.0.0/12"],
    REDEMPTION_CLOUDFLARE_CIDRS=["203.0.113.0/24"],
)
class RedemptionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="redemption-admin", password="Strong-password-123!")
        self.user = User.objects.create_user(
            username="redemption-user",
            email="member@example.com",
            password="Strong-password-123!",
        )
        self.other_user = User.objects.create_user(username="other-user", password="Strong-password-123!")
        self.standard_pool = ChatgptCar.objects.create(
            car_name="Redemption standard pool", gpt_account_list=[], created_time=1, updated_time=1
        )
        self.premium_pool = ChatgptCar.objects.create(
            car_name="Redemption premium pool", gpt_account_list=[], created_time=1, updated_time=1
        )
        self.standard_plan = Plan.objects.create(
            code="redeem-standard", name="普通套餐", pool=self.standard_pool, pool_tier=PoolTier.STANDARD
        )
        self.premium_plan = Plan.objects.create(
            code="redeem-premium", name="高级套餐", pool=self.premium_pool, pool_tier=PoolTier.PREMIUM
        )
        self.standard_offer = PlanOffer.objects.create(
            plan=self.standard_plan, code="monthly", name="月套餐", months=1, price_cents=5800
        )
        self.standard_quarterly = PlanOffer.objects.create(
            plan=self.standard_plan, code="quarterly", name="季套餐", months=3, price_cents=15800
        )
        self.premium_offer = PlanOffer.objects.create(
            plan=self.premium_plan, code="monthly", name="月套餐", months=1, price_cents=9800
        )
        self.standard_account = self.create_account("redemption-standard@example.com")
        self.premium_account = self.create_account("redemption-premium@example.com")
        PoolAccountPolicy.objects.create(
            pool=self.standard_pool, account=self.standard_account, tier=PoolTier.STANDARD, binding_limit=5
        )
        PoolAccountPolicy.objects.create(
            pool=self.premium_pool, account=self.premium_account, tier=PoolTier.PREMIUM, binding_limit=3
        )
        CommercialSettings.objects.create(redemption_enabled=True, purchase_url="https://shop.example.com/buy")
        self.client = APIClient()

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

    def generate_code(self, offer=None, **kwargs):
        batch, plaintext = create_redemption_batch(
            offer=offer or self.standard_offer,
            quantity=1,
            expires_at=kwargs.get("expires_at"),
            note="test batch",
            actor=self.admin,
            ip_address="198.51.100.10",
        )
        return batch, plaintext[0]["code"], batch.codes.get()

    def redeem(self, code, user=None):
        return redeem_code(
            user=user or self.user,
            plaintext_code=code,
            ip_address="198.51.100.20",
            user_agent="Redemption test browser",
        )

    def test_generated_plaintext_is_not_stored_and_redeems_once(self):
        _, plaintext, code = self.generate_code()
        self.assertNotIn(plaintext, json.dumps(list(RedemptionCode.objects.values()), default=str))

        order, subscription, duplicate = self.redeem(plaintext.lower().replace("-", " "))
        self.assertFalse(duplicate)
        self.assertEqual(order.status, OrderStatus.PAID)
        self.assertEqual(order.provider, "redemption_code")
        self.assertEqual(order.entitlement_ends_at, subscription.ends_at)
        self.assertEqual(subscription.plan_id, self.standard_plan.id)
        code.refresh_from_db()
        self.assertEqual(code.status, RedemptionCodeStatus.REDEEMED)
        self.assertEqual(code.redeemed_by_id, self.user.id)
        self.assertEqual(str(code.redeemed_ip), "198.51.100.20")
        self.assertEqual(code.redeemed_user_agent, "Redemption test browser")
        self.assertEqual(PaymentTransaction.objects.filter(order=order, accepted=True).count(), 1)

        repeated_order, repeated_subscription, duplicate = self.redeem(plaintext)
        self.assertTrue(duplicate)
        self.assertEqual(repeated_order.id, order.id)
        self.assertEqual(repeated_subscription.id, subscription.id)
        self.assertEqual(PaymentTransaction.objects.filter(order=order).count(), 1)

        with self.assertRaises(BillingError):
            self.redeem(plaintext, user=self.other_user)

    def test_batch_snapshot_keeps_original_term_and_price(self):
        batch, plaintext, _ = self.generate_code(self.standard_quarterly)
        self.standard_quarterly.months = 12
        self.standard_quarterly.price_cents = 1
        self.standard_quarterly.save(update_fields=["months", "price_cents", "updated_at"])

        order, subscription, _ = self.redeem(plaintext)
        self.assertEqual(batch.entitlement_months, 3)
        self.assertEqual(order.entitlement_months, 3)
        self.assertEqual(order.price_cents, 15800)
        expected = add_months(subscription.starts_at, 3)
        self.assertEqual(subscription.ends_at, expected)
        self.assertEqual(order.entitlement_ends_at, expected)

    def test_redemption_order_list_filters_and_keeps_expiry_snapshot(self):
        _, plaintext, _ = self.generate_code()
        order, subscription, _ = self.redeem(plaintext)
        original_expiry = order.entitlement_ends_at

        _, second_plaintext, _ = self.generate_code()
        self.redeem(second_plaintext)
        order.refresh_from_db()
        subscription.refresh_from_db()
        self.assertEqual(order.entitlement_ends_at, original_expiry)
        self.assertGreater(subscription.ends_at, original_expiry)

        self.client.force_authenticate(self.user)
        response = self.client.get("/0x/billing/orders?provider=redemption_code&page_size=50")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 2)
        self.assertTrue(all(item["provider"] == "redemption_code" for item in response.data["results"]))
        self.assertTrue(all(item["entitlement_ends_at"] for item in response.data["results"]))

    def test_renew_upgrade_and_downgrade_reuse_existing_rules(self):
        _, first_code, _ = self.generate_code()
        _, subscription, _ = self.redeem(first_code)
        first_end = subscription.ends_at

        _, renewal_code, _ = self.generate_code()
        _, renewed, _ = self.redeem(renewal_code)
        self.assertEqual(renewed.ends_at, add_months(first_end, 1))

        _, upgrade_code, _ = self.generate_code(self.premium_offer)
        _, upgraded, _ = self.redeem(upgrade_code)
        self.assertEqual(upgraded.plan_id, self.premium_plan.id)
        self.assertEqual(upgraded.ends_at, add_months(renewed.ends_at, 1))

        _, downgrade_code, _ = self.generate_code()
        _, scheduled, _ = self.redeem(downgrade_code)
        self.assertEqual(scheduled.plan_id, self.premium_plan.id)
        self.assertEqual(scheduled.scheduled_plan_id, self.standard_plan.id)
        self.assertEqual(scheduled.scheduled_months, 1)

    def test_capacity_failure_rolls_back_order_and_code(self):
        self.standard_pool.billing_policies.update(binding_limit=1)
        first_user = User.objects.create_user(username="capacity-first")
        _, first_code, _ = self.generate_code()
        self.redeem(first_code, user=first_user)
        _, second_code, second_record = self.generate_code()

        with self.assertRaises(CapacityUnavailable):
            self.redeem(second_code)
        second_record.refresh_from_db()
        self.assertEqual(second_record.status, RedemptionCodeStatus.AVAILABLE)
        self.assertIsNone(second_record.order_id)
        self.assertFalse(Order.objects.filter(user=self.user, provider="redemption_code").exists())

    def test_disabled_expired_revoked_and_archived_codes_are_rejected(self):
        batch, plaintext, record = self.generate_code()
        batch.is_active = False
        batch.save(update_fields=["is_active", "updated_at"])
        with self.assertRaises(BillingError):
            self.redeem(plaintext)

        batch.is_active = True
        batch.expires_at = timezone.now() - timedelta(seconds=1)
        batch.save(update_fields=["is_active", "expires_at", "updated_at"])
        with self.assertRaises(BillingError):
            self.redeem(plaintext)

        batch.expires_at = None
        batch.save(update_fields=["expires_at", "updated_at"])
        revoke_redemption_codes(code_ids=[record.id], actor=self.admin)
        with self.assertRaises(BillingError):
            self.redeem(plaintext)

    def test_archive_redeemed_keeps_audit_relation(self):
        _, plaintext, record = self.generate_code()
        order, _, _ = self.redeem(plaintext)
        archive_redeemed_codes(code_ids=[record.id], actor=self.admin)
        record.refresh_from_db()
        self.assertTrue(record.is_archived)
        self.assertEqual(record.order_id, order.id)
        self.assertEqual(record.redeemed_by_id, self.user.id)

    def test_user_api_exposes_purchase_url_and_redeems_without_ip_fields(self):
        _, plaintext, _ = self.generate_code()
        self.client.force_authenticate(self.user)
        plans = self.client.get("/0x/billing/plans")
        self.assertTrue(plans.data["redemption_enabled"])
        self.assertEqual(plans.data["purchase_url"], "https://shop.example.com/buy")

        response = self.client.post(
            "/0x/billing/redemption-codes/redeem",
            {"code": plaintext},
            format="json",
            REMOTE_ADDR="172.18.0.10",
            HTTP_X_REAL_IP="198.51.100.40",
            HTTP_X_FORWARDED_FOR="6.6.6.6",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("redeemed_ip", response.data)
        self.assertNotIn("plaintext_code", response.data)

    def test_admin_api_lists_redeemer_time_and_ip_and_archives_used(self):
        _, plaintext, record = self.generate_code()
        self.redeem(plaintext)
        self.client.force_authenticate(self.admin)
        listed = self.client.get("/0x/admin/redemption-codes?q=redemption-user")
        self.assertEqual(listed.status_code, 200)
        row = listed.data["results"][0]
        self.assertEqual(row["username"], self.user.username)
        self.assertEqual(row["redeemed_email"], self.user.email)
        self.assertEqual(row["redeemed_ip"], "198.51.100.20")
        self.assertIsNotNone(row["redeemed_at"])

        archived = self.client.post(
            "/0x/admin/redemption-codes",
            {"action": "archive_redeemed", "code_ids": [record.id]},
            format="json",
        )
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(archived.data["count"], 1)

    def test_admin_batch_list_hides_archived_batches_without_invalid_join(self):
        active_batch, _, _ = self.generate_code()
        archived_batch, _, _ = self.generate_code()
        archived_batch.is_archived = True
        archived_batch.is_active = False
        archived_batch.save(update_fields=["is_archived", "is_active", "updated_at"])

        self.client.force_authenticate(self.admin)
        response = self.client.get("/0x/admin/redemption-batches")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.data["batches"]], [active_batch.id])

    def test_admin_can_lookup_full_code_without_returning_plaintext(self):
        _, plaintext, _ = self.generate_code()
        self.redeem(plaintext)
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/0x/admin/redemption-codes",
            {"action": "lookup", "code": plaintext},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"]["username"], self.user.username)
        self.assertEqual(response.data["code"]["redeemed_ip"], "198.51.100.20")
        self.assertNotIn(plaintext, json.dumps(response.data))

    def test_existing_offer_cannot_be_moved_to_another_plan(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/0x/admin/plans",
            {
                "action": "save_offer",
                "id": self.standard_offer.id,
                "plan_id": self.premium_plan.id,
                "code": self.standard_offer.code,
                "name": self.standard_offer.name,
                "months": self.standard_offer.months,
                "price_cents": self.standard_offer.price_cents,
                "currency": self.standard_offer.currency,
                "is_draft": False,
                "is_purchase_enabled": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.standard_offer.refresh_from_db()
        self.assertEqual(self.standard_offer.plan_id, self.standard_plan.id)

    def test_plan_with_issued_codes_cannot_change_pool_or_tier(self):
        self.generate_code()
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/0x/admin/plans",
            {
                "action": "save_plan",
                "id": self.standard_plan.id,
                "code": self.standard_plan.code,
                "name": self.standard_plan.name,
                "tagline": self.standard_plan.tagline,
                "pool_id": self.premium_pool.id,
                "pool_tier": PoolTier.PREMIUM,
                "is_active": True,
                "is_public": True,
                "sort_order": 0,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.standard_plan.refresh_from_db()
        self.assertEqual(self.standard_plan.pool_id, self.standard_pool.id)
        self.assertEqual(self.standard_plan.pool_tier, PoolTier.STANDARD)

    def test_trusted_proxy_ip_extraction_rejects_spoofed_headers(self):
        factory = APIRequestFactory()
        direct = factory.post(
            "/redeem",
            {},
            REMOTE_ADDR="198.51.100.88",
            HTTP_X_CHATGPT_MIRROR_CLIENT_IP="203.0.113.10",
            HTTP_X_REAL_IP="1.1.1.1",
            HTTP_CF_CONNECTING_IP="8.8.8.8",
        )
        self.assertEqual(get_redemption_client_ip(direct), "198.51.100.88")

        proxied = factory.post(
            "/redeem",
            {},
            REMOTE_ADDR="172.18.0.10",
            HTTP_X_CHATGPT_MIRROR_CLIENT_IP="198.51.100.77",
            HTTP_X_REAL_IP="172.18.0.7",
            HTTP_X_FORWARDED_FOR="6.6.6.6",
        )
        self.assertEqual(get_redemption_client_ip(proxied), "198.51.100.77")

        cloudflare = factory.post(
            "/redeem",
            {},
            REMOTE_ADDR="172.18.0.10",
            HTTP_X_REAL_IP="203.0.113.25",
            HTTP_CF_CONNECTING_IP="192.0.2.55",
        )
        self.assertEqual(get_redemption_client_ip(cloudflare), "192.0.2.55")


@override_settings(
    BILLING_ENABLED=True,
    BILLING_ENFORCE_SUBSCRIPTION=True,
    REDEMPTION_CODES_ENABLED=True,
    REDEMPTION_CODE_KEYRING=TEST_KEYRING,
    PAYMENT_CALLBACK_LOCK_ENABLED=False,
)
class RedemptionPostgresConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != "postgresql":
            cls.__unittest_skip__ = True
            cls.__unittest_skip_why__ = "PostgreSQL row-lock test"

    def setUp(self):
        self.admin = User.objects.create_superuser(username="concurrency-admin", password="Strong-password-123!")
        self.first_user = User.objects.create_user(username="concurrency-first")
        self.second_user = User.objects.create_user(username="concurrency-second")
        pool = ChatgptCar.objects.create(
            car_name="Concurrency pool", gpt_account_list=[], created_time=1, updated_time=1
        )
        self.plan = Plan.objects.create(
            code="concurrency-plan", name="并发套餐", pool=pool, pool_tier=PoolTier.STANDARD
        )
        self.offer = PlanOffer.objects.create(
            plan=self.plan, code="monthly", name="月套餐", months=1, price_cents=100
        )
        account = RedemptionTests.create_account("concurrency@example.com")
        PoolAccountPolicy.objects.create(
            pool=pool, account=account, tier=PoolTier.STANDARD, binding_limit=10
        )
        CommercialSettings.objects.create(redemption_enabled=True)

    def generate_codes(self, quantity):
        batch, plaintext = create_redemption_batch(
            offer=self.offer,
            quantity=quantity,
            expires_at=None,
            note="postgres concurrency",
            actor=self.admin,
        )
        return batch, [item["code"] for item in plaintext]

    @staticmethod
    def run_parallel(workers):
        barrier = threading.Barrier(len(workers))
        results = [None] * len(workers)

        def runner(index, worker):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                results[index] = ("ok", worker())
            except Exception as exc:  # Assertions inspect exact outcome below.
                results[index] = ("error", exc)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=runner, args=(index, worker)) for index, worker in enumerate(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        return results

    def test_two_users_redeeming_one_code_only_one_succeeds(self):
        _, codes = self.generate_codes(1)

        def redeem_for(user_id):
            return lambda: redeem_code(
                user=User.objects.get(pk=user_id),
                plaintext_code=codes[0],
                ip_address=f"198.51.100.{user_id}",
                user_agent="postgres concurrency",
            )

        results = self.run_parallel([redeem_for(self.first_user.id), redeem_for(self.second_user.id)])
        self.assertEqual(sum(status == "ok" for status, _ in results), 1)
        self.assertEqual(sum(isinstance(value, BillingError) for status, value in results if status == "error"), 1)
        code = RedemptionCode.objects.get()
        self.assertEqual(code.status, RedemptionCodeStatus.REDEEMED)
        self.assertEqual(Order.objects.filter(provider="redemption_code", status=OrderStatus.PAID).count(), 1)

    def test_one_user_redeeming_two_codes_serializes_subscription_updates(self):
        _, codes = self.generate_codes(2)
        user_id = self.first_user.id

        def redeem_plaintext(plaintext):
            return lambda: redeem_code(
                user=User.objects.get(pk=user_id),
                plaintext_code=plaintext,
                ip_address="198.51.100.90",
                user_agent="postgres concurrency",
            )

        before = timezone.now()
        results = self.run_parallel([redeem_plaintext(codes[0]), redeem_plaintext(codes[1])])
        self.assertTrue(all(status == "ok" for status, _ in results), results)
        subscription = self.first_user.billing_subscription
        self.assertGreaterEqual(subscription.ends_at, add_months(before, 2) - timedelta(seconds=5))
        self.assertEqual(RedemptionCode.objects.filter(status=RedemptionCodeStatus.REDEEMED).count(), 2)
        self.assertEqual(Order.objects.filter(user_id=user_id, provider="redemption_code", status=OrderStatus.PAID).count(), 2)

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from app.accounts.models import User
from app.billing.models import OrderStatus, PaymentTransaction, Plan, PlanOffer, PoolAccountPolicy, PoolTier
from app.billing.payment import AlipayPagePaymentProvider, cny_to_cents
from app.billing.services import close_provider_order, create_order, reconcile_provider_order
from app.chatgpt.models import ChatgptAccount, ChatgptCar


class FakeAliPayClient:
    def __init__(self):
        self.verify_result = True
        self.page_requests = []
        self.query_response = {}
        self.close_response = {"code": "10000"}

    def api_alipay_trade_page_pay(self, **kwargs):
        self.page_requests.append(kwargs)
        return "signed-query"

    def verify(self, data, signature):
        return self.verify_result and signature == "valid-signature"

    def api_alipay_trade_query(self, **kwargs):
        return self.query_response

    def api_alipay_trade_close(self, **kwargs):
        return self.close_response


class AlipayPaymentTests(TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        private_path = Path(self.temp_dir.name) / "app-private.pem"
        public_path = Path(self.temp_dir.name) / "alipay-public.pem"
        private_path.write_text("not-used-in-mocked-tests", encoding="utf-8")
        public_path.write_text("not-used-in-mocked-tests", encoding="utf-8")
        self.settings_override = override_settings(
            BILLING_ENABLED=True,
            BILLING_ORDER_HOLD_MINUTES=30,
            ALIPAY_ENV="production",
            ALIPAY_APP_ID="test-app-id",
            ALIPAY_APP_PRIVATE_KEY_PATH=str(private_path),
            ALIPAY_PUBLIC_KEY_PATH=str(public_path),
            ALIPAY_NOTIFY_URL="https://chat2.devven.online/0x/billing/alipay/notify",
            ALIPAY_RETURN_URL="https://chat2.devven.online/admin#/account/billing",
            ALIPAY_SIGN_TYPE="RSA2",
        )
        self.settings_override.enable()
        self.client = APIClient()
        self.provider = AlipayPagePaymentProvider()
        self.fake_client = FakeAliPayClient()
        self.user = User.objects.create_user(username="alipay-user", password="Strong-password-123!")
        self.pool = ChatgptCar.objects.create(
            car_name="支付宝测试号池",
            gpt_account_list=[],
            created_time=1,
            updated_time=1,
        )
        self.plan = Plan.objects.create(
            code="alipay-test-plan",
            name="支付宝测试套餐",
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
        account = ChatgptAccount.objects.create(
            chatgpt_username="alipay-test@example.com",
            plan_type="plus",
            access_token="test-access-token",
            auth_status=True,
            access_token_valid=True,
            created_time=1,
            updated_time=1,
        )
        PoolAccountPolicy.objects.create(
            pool=self.pool,
            account=account,
            tier=PoolTier.STANDARD,
            binding_limit=5,
        )

    def tearDown(self):
        self.settings_override.disable()
        self.temp_dir.cleanup()

    def make_order(self):
        return create_order(self.user, self.offer, provider="alipay")

    def callback_payload(self, order):
        return {
            "app_id": "test-app-id",
            "sign": "valid-signature",
            "sign_type": "RSA2",
            "out_trade_no": order.order_no,
            "trade_no": "2026081100000001",
            "trade_status": "TRADE_SUCCESS",
            "total_amount": "58.00",
        }

    def test_cny_conversion_rejects_fractional_cent(self):
        self.assertEqual(cny_to_cents("58.00"), 5800)
        with self.assertRaises(Exception):
            cny_to_cents("58.001")

    def test_create_checkout_uses_server_order_amount(self):
        order = self.make_order()
        with patch.object(self.provider, "_client", return_value=self.fake_client):
            checkout = self.provider.create_order(order)
        self.assertEqual(checkout["provider"], "alipay")
        self.assertEqual(checkout["order_no"], order.order_no)
        self.assertTrue(checkout["pay_url"].startswith("https://openapi.alipay.com/gateway.do?"))
        self.assertEqual(self.fake_client.page_requests[0]["total_amount"], "58.00")
        self.assertEqual(self.fake_client.page_requests[0]["out_trade_no"], order.order_no)
        self.assertIsNotNone(order.payment_expires_at)

    def test_valid_callback_activates_only_once(self):
        order = self.make_order()
        payload = self.callback_payload(order)
        with patch.object(self.provider, "_client", return_value=self.fake_client), patch(
            "app.billing.views.get_payment_provider", return_value=self.provider
        ):
            first = self.client.post("/0x/billing/alipay/notify", payload)
            second = self.client.post("/0x/billing/alipay/notify", payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.content, b"success")
        self.assertEqual(second.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PAID)
        self.assertEqual(PaymentTransaction.objects.filter(order=order, accepted=True).count(), 1)

    def test_invalid_signature_never_creates_transaction(self):
        order = self.make_order()
        payload = self.callback_payload(order)
        payload["sign"] = "forged"
        with patch.object(self.provider, "_client", return_value=self.fake_client), patch(
            "app.billing.views.get_payment_provider", return_value=self.provider
        ):
            response = self.client.post("/0x/billing/alipay/notify", payload)
        self.assertEqual(response.status_code, 400)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertFalse(PaymentTransaction.objects.filter(order=order).exists())

    def test_callback_amount_mismatch_is_rejected_and_audited(self):
        order = self.make_order()
        payload = self.callback_payload(order)
        payload["total_amount"] = "0.01"
        with patch.object(self.provider, "_client", return_value=self.fake_client), patch(
            "app.billing.views.get_payment_provider", return_value=self.provider
        ):
            response = self.client.post("/0x/billing/alipay/notify", payload)
        self.assertEqual(response.status_code, 400)
        order.refresh_from_db()
        transaction = PaymentTransaction.objects.get(order=order)
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertFalse(transaction.accepted)

    def test_query_reconciles_paid_order_without_callback(self):
        order = self.make_order()
        self.fake_client.query_response = {
            "code": "10000",
            "out_trade_no": order.order_no,
            "trade_no": "2026081100000002",
            "trade_status": "TRADE_SUCCESS",
            "total_amount": "58.00",
        }
        with patch("app.billing.services.get_payment_provider", return_value=self.provider), patch.object(
            self.provider, "_client", return_value=self.fake_client
        ):
            reconciled, subscription = reconcile_provider_order(order)
        self.assertEqual(reconciled.status, OrderStatus.PAID)
        self.assertIsNotNone(subscription)

    def test_close_queries_before_closing_pending_order(self):
        order = self.make_order()
        self.fake_client.query_response = {
            "code": "10000",
            "out_trade_no": order.order_no,
            "trade_status": "WAIT_BUYER_PAY",
            "total_amount": "58.00",
        }
        with patch("app.billing.services.get_payment_provider", return_value=self.provider), patch.object(
            self.provider, "_client", return_value=self.fake_client
        ):
            closed = close_provider_order(order)
        self.assertEqual(closed.status, OrderStatus.CLOSED)

    def test_close_releases_order_when_alipay_reports_no_trade(self):
        order = self.make_order()
        self.fake_client.query_response = {"code": "40004", "sub_code": "ACQ.TRADE_NOT_EXIST"}
        self.fake_client.close_response = {"code": "40004", "sub_code": "ACQ.TRADE_NOT_EXIST"}
        with patch("app.billing.services.get_payment_provider", return_value=self.provider), patch.object(
            self.provider, "_client", return_value=self.fake_client
        ):
            closed = close_provider_order(order)
        self.assertEqual(closed.status, OrderStatus.CLOSED)

# test_payment_verification.py
# Regression tests for the payment amount-mismatch and replay-protection logic in
# payments.verify_razorpay_payment.
#
# These mock Razorpay's API responses — specifically, what a genuinely PAID order looks
# like — because reaching that state for real requires a browser-based Checkout.js
# session with a test card, which isn't scriptable from here. See verify_payment_flow.py
# for the complementary real-API checks (forged signature / wrong order / unpaid order)
# that don't need this. The replay-protection assertions below DO write to (and read
# from) the real `used_razorpay_payments` collection — on the isolated test database.

import uuid
from unittest.mock import MagicMock, patch

import razorpay.errors

from ..Handlers import payments
from ..Handlers.db_connection import get_db
from .base import SecurityTestCase


def _mock_razorpay_client(order_amount, order_status="paid", signature_valid=True):
    mock_client = MagicMock()
    if signature_valid:
        mock_client.utility.verify_payment_signature.return_value = True
    else:
        mock_client.utility.verify_payment_signature.side_effect = razorpay.errors.SignatureVerificationError("bad sig")
    mock_client.order.fetch.return_value = {"amount": order_amount, "status": order_status}
    return mock_client


class PaymentAmountAndReplayTests(SecurityTestCase):
    def setUp(self):
        super().setUp()
        self.order_id = f"order_test_{uuid.uuid4().hex[:12]}"
        self.payment_id = f"pay_test_{uuid.uuid4().hex[:12]}"
        self.signature = "irrelevant-because-signature-check-is-mocked"

    def tearDown(self):
        super().tearDown()
        get_db().used_razorpay_payments.delete_many({"order_id": {"$regex": "^order_test_"}})

    @patch("razorpay.Client")
    def test_amount_mismatch_is_rejected(self, mock_client_cls):
        """A real, genuinely PAID order for ₹1 must not unlock a ₹200 booking."""
        mock_client_cls.return_value = _mock_razorpay_client(order_amount=100, order_status="paid")
        result = payments.verify_razorpay_payment(self.order_id, self.payment_id, self.signature, 20000)
        self.assertFalse(result)

    @patch("razorpay.Client")
    def test_matching_paid_amount_is_accepted(self, mock_client_cls):
        mock_client_cls.return_value = _mock_razorpay_client(order_amount=20000, order_status="paid")
        result = payments.verify_razorpay_payment(self.order_id, self.payment_id, self.signature, 20000)
        self.assertTrue(result)

    @patch("razorpay.Client")
    def test_unpaid_order_is_rejected_even_with_matching_amount(self, mock_client_cls):
        mock_client_cls.return_value = _mock_razorpay_client(order_amount=20000, order_status="created")
        result = payments.verify_razorpay_payment(self.order_id, self.payment_id, self.signature, 20000)
        self.assertFalse(result)

    @patch("razorpay.Client")
    def test_invalid_signature_is_rejected_before_any_amount_check(self, mock_client_cls):
        mock_client_cls.return_value = _mock_razorpay_client(order_amount=20000, order_status="paid", signature_valid=False)
        result = payments.verify_razorpay_payment(self.order_id, self.payment_id, self.signature, 20000)
        self.assertFalse(result)

    @patch("razorpay.Client")
    def test_payment_cannot_be_replayed(self, mock_client_cls):
        mock_client_cls.return_value = _mock_razorpay_client(order_amount=20000, order_status="paid")

        first = payments.verify_razorpay_payment(self.order_id, self.payment_id, self.signature, 20000)
        self.assertTrue(first, "First use of a genuinely valid, correctly-amounted payment should succeed.")

        second = payments.verify_razorpay_payment(self.order_id, self.payment_id, self.signature, 20000)
        self.assertFalse(second, "The same payment_id was accepted twice — replay protection is broken!")

    @patch("razorpay.Client")
    def test_different_payments_are_independent(self, mock_client_cls):
        mock_client_cls.return_value = _mock_razorpay_client(order_amount=20000, order_status="paid")
        other_payment_id = f"pay_test_{uuid.uuid4().hex[:12]}"

        first = payments.verify_razorpay_payment(self.order_id, self.payment_id, self.signature, 20000)
        second = payments.verify_razorpay_payment(self.order_id, other_payment_id, self.signature, 20000)
        self.assertTrue(first)
        self.assertTrue(second)

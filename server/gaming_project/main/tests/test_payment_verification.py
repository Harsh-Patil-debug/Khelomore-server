# test_payment_verification.py
# Regression tests for the payment amount-mismatch and replay-protection logic in
# payments.verify_cashfree_payment.
#
# These mock Cashfree's order-status API response — specifically, what a genuinely PAID
# order looks like — because reaching that state for real requires a browser-based
# hosted-checkout session with a test card, which isn't scriptable from here. See
# verify_payment_flow.py for the complementary real-API checks (wrong/unknown order,
# unpaid order) that don't need this. The replay-protection assertions below DO write to
# (and read from) the real `used_cashfree_payments` collection — on the isolated test
# database.
#
# NOTE: unlike Razorpay, Cashfree's checkout never gives the client a signature to
# verify — the server independently asks Cashfree's own API what happened to the order,
# so there's no "invalid signature" case to test here at all; a forged/wrong order_id is
# instead covered by verify_payment_flow.py's real-API checks.

import uuid
from unittest.mock import MagicMock, patch

from django.test import override_settings

from ..Handlers import payments
from ..Handlers.db_connection import get_db
from .base import SecurityTestCase


def _mock_cashfree_order_response(order_amount, order_status="PAID"):
    mock_response = MagicMock()
    mock_response.json.return_value = {"order_amount": order_amount, "order_status": order_status}
    mock_response.raise_for_status.return_value = None
    return mock_response


# Self-contained fake credentials so these tests never depend on the real
# CASHFREE_CLIENT_ID/SECRET env vars being set locally — verify_cashfree_payment only
# needs them to be non-empty to reach the (mocked) HTTP call at all.
@override_settings(CASHFREE_CLIENT_ID="test_client_id", CASHFREE_CLIENT_SECRET="test_client_secret")
class PaymentAmountAndReplayTests(SecurityTestCase):
    def setUp(self):
        super().setUp()
        self.order_id = f"order_test_{uuid.uuid4().hex[:12]}"

    def tearDown(self):
        super().tearDown()
        get_db().used_cashfree_payments.delete_many({"order_id": {"$regex": "^order_test_"}})

    @patch("requests.get")
    def test_amount_mismatch_is_rejected(self, mock_get):
        """A real, genuinely PAID order for ₹1 must not unlock a ₹200 booking."""
        mock_get.return_value = _mock_cashfree_order_response(order_amount=1, order_status="PAID")
        result = payments.verify_cashfree_payment(self.order_id, 20000)
        self.assertFalse(result)

    @patch("requests.get")
    def test_matching_paid_amount_is_accepted(self, mock_get):
        mock_get.return_value = _mock_cashfree_order_response(order_amount=200, order_status="PAID")
        result = payments.verify_cashfree_payment(self.order_id, 20000)
        self.assertTrue(result)

    @patch("requests.get")
    def test_unpaid_order_is_rejected_even_with_matching_amount(self, mock_get):
        mock_get.return_value = _mock_cashfree_order_response(order_amount=200, order_status="ACTIVE")
        result = payments.verify_cashfree_payment(self.order_id, 20000)
        self.assertFalse(result)

    @patch("requests.get")
    def test_payment_cannot_be_replayed(self, mock_get):
        mock_get.return_value = _mock_cashfree_order_response(order_amount=200, order_status="PAID")

        first = payments.verify_cashfree_payment(self.order_id, 20000)
        self.assertTrue(first, "First use of a genuinely valid, correctly-amounted payment should succeed.")

        second = payments.verify_cashfree_payment(self.order_id, 20000)
        self.assertFalse(second, "The same order_id was accepted twice — replay protection is broken!")

    @patch("requests.get")
    def test_different_orders_are_independent(self, mock_get):
        mock_get.return_value = _mock_cashfree_order_response(order_amount=200, order_status="PAID")
        other_order_id = f"order_test_{uuid.uuid4().hex[:12]}"

        first = payments.verify_cashfree_payment(self.order_id, 20000)
        second = payments.verify_cashfree_payment(other_order_id, 20000)
        self.assertTrue(first)
        self.assertTrue(second)

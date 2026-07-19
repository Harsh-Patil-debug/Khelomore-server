# test_cafe_razorpay.py
# Regression tests for per-cafe Razorpay routing: a cafe owner can connect their own
# Razorpay account so booking payments go straight to them instead of the platform's
# account; cafes that haven't configured one yet fall back to the platform account
# (flagged for manual settlement later, per product decision); and an order created for
# one cafe can never be replayed to pay for a booking at a different cafe.

import uuid
from unittest.mock import MagicMock, patch

from django.conf import settings

from ..Handlers import payments
from ..Handlers.db_connection import get_db
from .base import SecurityTestCase


RZP_TEST_PASSWORD = "MyRzpPassword123"


def _connect_owner_credentials(test_case, cafe_id, token, key_id="rzp_test_ownerkey123", key_secret="supersecretvalue"):
    """Sets the owner's Razorpay password (first-time setup) and connects their own
    Razorpay account in one step — every test that needs a cafe to already have its own
    credentials configured goes through this, since the save endpoint requires the
    password gate to be satisfied (see CafeRazorpayCredentialsView.put)."""
    test_case.client.post(
        f"/api/v1/main/cafes/{cafe_id}/razorpay-credentials/set-password/",
        {"password": RZP_TEST_PASSWORD},
        format="json",
        **test_case.auth_header(token),
    )
    return test_case.client.put(
        f"/api/v1/main/cafes/{cafe_id}/razorpay-credentials/",
        {"key_id": key_id, "key_secret": key_secret, "razorpay_password": RZP_TEST_PASSWORD},
        format="json",
        **test_case.auth_header(token),
    )


def _mock_razorpay_client(order_amount, order_status="paid", signature_valid=True):
    mock_client = MagicMock()
    if signature_valid:
        mock_client.utility.verify_payment_signature.return_value = True
    else:
        import razorpay.errors
        mock_client.utility.verify_payment_signature.side_effect = razorpay.errors.SignatureVerificationError("bad sig")
    mock_client.order.fetch.return_value = {"amount": order_amount, "status": order_status}
    mock_client.order.create.return_value = {
        "id": f"order_test_{uuid.uuid4().hex[:12]}",
        "amount": order_amount,
        "status": "created",
    }
    return mock_client


class CafeRazorpayCredentialsTests(SecurityTestCase):
    def setUp(self):
        super().setUp()
        self.owner_email, self.owner_token = self.make_active_user(role="admin", collection="admins")
        self.cafe_id = self.make_cafe(owner_email=self.owner_email)

    def test_starts_unconfigured(self):
        resp = self.client.get(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/", **self.auth_header(self.owner_token)
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["configured"])

    def test_owner_can_save_credentials_and_status_reflects_it(self):
        resp = _connect_owner_credentials(self, self.cafe_id, self.owner_token)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["configured"])
        self.assertEqual(resp.json()["key_id"], "rzp_test_ownerkey123")

        status_resp = self.client.get(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/", **self.auth_header(self.owner_token)
        )
        self.assertTrue(status_resp.json()["configured"])
        self.assertEqual(status_resp.json()["key_id"], "rzp_test_ownerkey123")

    def test_key_secret_is_never_echoed_back(self):
        connect_resp = _connect_owner_credentials(self, self.cafe_id, self.owner_token)
        self.assertEqual(connect_resp.status_code, 200)
        status_resp = self.client.get(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/", **self.auth_header(self.owner_token)
        )
        self.assertNotIn("key_secret", status_resp.json())
        self.assertNotIn("supersecretvalue", str(status_resp.json()))

    def test_secret_is_encrypted_at_rest_not_stored_plaintext(self):
        connect_resp = _connect_owner_credentials(self, self.cafe_id, self.owner_token)
        self.assertEqual(connect_resp.status_code, 200)
        from bson import ObjectId
        doc = self.db.cafes.find_one({"_id": ObjectId(self.cafe_id)})
        self.assertNotEqual(doc["razorpay_key_secret_enc"], "supersecretvalue")
        self.assertNotIn("supersecretvalue", doc["razorpay_key_secret_enc"])

    def test_invalid_key_id_format_is_rejected(self):
        self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/set-password/",
            {"password": RZP_TEST_PASSWORD},
            format="json",
            **self.auth_header(self.owner_token),
        )
        resp = self.client.put(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/",
            {"key_id": "not-a-real-key", "key_secret": "supersecretvalue", "razorpay_password": RZP_TEST_PASSWORD},
            format="json",
            **self.auth_header(self.owner_token),
        )
        self.assertEqual(resp.status_code, 400)

    def test_unrelated_admin_cannot_save_for_someone_elses_cafe(self):
        attacker_email, attacker_token = self.make_active_user(role="admin", collection="admins")
        self.make_cafe(owner_email=attacker_email)

        resp = self.client.put(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/",
            {"key_id": "rzp_test_attacker", "key_secret": "hijack"},
            format="json",
            **self.auth_header(attacker_token),
        )
        self.assertEqual(resp.status_code, 403)

    def test_owner_can_disconnect(self):
        connect_resp = _connect_owner_credentials(self, self.cafe_id, self.owner_token)
        self.assertEqual(connect_resp.status_code, 200)
        resp = self.client.delete(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/",
            {"razorpay_password": RZP_TEST_PASSWORD},
            format="json",
            **self.auth_header(self.owner_token),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["configured"])

    def test_disconnect_without_password_is_rejected(self):
        connect_resp = _connect_owner_credentials(self, self.cafe_id, self.owner_token)
        self.assertEqual(connect_resp.status_code, 200)
        resp = self.client.delete(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/", **self.auth_header(self.owner_token)
        )
        self.assertEqual(resp.status_code, 403)


class CafeBookingOrderCreationTests(SecurityTestCase):
    def setUp(self):
        super().setUp()
        self.owner_email, self.owner_token = self.make_active_user(role="admin", collection="admins")
        self.cafe_id = self.make_cafe(owner_email=self.owner_email)
        self.customer_email, self.customer_token = self.make_active_user(role="user")

    def tearDown(self):
        super().tearDown()
        self.db.cafe_payment_orders.delete_many({"cafe_id": self.cafe_id})

    @patch("razorpay.Client")
    def test_unconfigured_cafe_falls_back_to_platform_account(self, mock_client_cls):
        mock_client_cls.return_value = _mock_razorpay_client(order_amount=20000)
        resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/payments/create-order/",
            {"amount": 200},
            format="json",
            **self.auth_header(self.customer_token),
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["used_platform_fallback"])
        self.assertEqual(body["key_id"], settings.RAZORPAY_KEY_ID)

    @patch("razorpay.Client")
    def test_configured_cafe_uses_its_own_key_id(self, mock_client_cls):
        mock_client_cls.return_value = _mock_razorpay_client(order_amount=20000)
        connect_resp = _connect_owner_credentials(self, self.cafe_id, self.owner_token)
        self.assertEqual(connect_resp.status_code, 200)
        resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/payments/create-order/",
            {"amount": 200},
            format="json",
            **self.auth_header(self.customer_token),
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["used_platform_fallback"])
        self.assertEqual(body["key_id"], "rzp_test_ownerkey123")

    def test_requires_login(self):
        resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/payments/create-order/", {"amount": 200}, format="json"
        )
        self.assertEqual(resp.status_code, 401)


class CafeBookingPaymentVerificationTests(SecurityTestCase):
    """Exercises payments.verify_razorpay_payment(cafe_id=...) directly — the HTTP-level
    booking-creation flow is already covered end-to-end by test_booking_security.py; this
    focuses on the new cafe-routing logic itself."""

    def setUp(self):
        super().setUp()
        self.owner_email, self.owner_token = self.make_active_user(role="admin", collection="admins")
        self.cafe_id = self.make_cafe(owner_email=self.owner_email)
        self.other_owner_email, self.other_owner_token = self.make_active_user(role="admin", collection="admins")
        self.other_cafe_id = self.make_cafe(owner_email=self.other_owner_email)

    def tearDown(self):
        super().tearDown()
        self.db.cafe_payment_orders.delete_many({"cafe_id": {"$in": [self.cafe_id, self.other_cafe_id]}})
        get_db().used_razorpay_payments.delete_many({"order_id": {"$regex": "^order_test_"}})

    @patch("razorpay.Client")
    def test_platform_fallback_order_verifies_successfully(self, mock_client_cls):
        mock_client_cls.return_value = _mock_razorpay_client(order_amount=20000)
        order = payments.create_cafe_booking_order_handler(self.cafe_id, 200)
        self.assertTrue(order["used_platform_fallback"])

        payment_id = f"pay_test_{uuid.uuid4().hex[:10]}"
        result = payments.verify_razorpay_payment(
            order["id"], payment_id, "irrelevant-mocked", 20000, cafe_id=self.cafe_id
        )
        self.assertTrue(result)

    @patch("razorpay.Client")
    def test_cafes_own_account_order_verifies_successfully(self, mock_client_cls):
        mock_client_cls.return_value = _mock_razorpay_client(order_amount=20000)
        connect_resp = _connect_owner_credentials(self, self.cafe_id, self.owner_token)
        self.assertEqual(connect_resp.status_code, 200)
        order = payments.create_cafe_booking_order_handler(self.cafe_id, 200)
        self.assertFalse(order["used_platform_fallback"])

        payment_id = f"pay_test_{uuid.uuid4().hex[:10]}"
        result = payments.verify_razorpay_payment(
            order["id"], payment_id, "irrelevant-mocked", 20000, cafe_id=self.cafe_id
        )
        self.assertTrue(result)

    @patch("razorpay.Client")
    def test_order_from_one_cafe_cannot_pay_for_a_booking_at_another_cafe(self, mock_client_cls):
        mock_client_cls.return_value = _mock_razorpay_client(order_amount=20000)
        order = payments.create_cafe_booking_order_handler(self.cafe_id, 200)

        payment_id = f"pay_test_{uuid.uuid4().hex[:10]}"
        result = payments.verify_razorpay_payment(
            order["id"], payment_id, "irrelevant-mocked", 20000, cafe_id=self.other_cafe_id
        )
        self.assertFalse(result, "An order created for one cafe must not verify a booking at a different cafe.")

    @patch("razorpay.Client")
    def test_unknown_order_id_is_rejected(self, mock_client_cls):
        mock_client_cls.return_value = _mock_razorpay_client(order_amount=20000)
        result = payments.verify_razorpay_payment(
            "order_never_created", "pay_x", "sig_x", 20000, cafe_id=self.cafe_id
        )
        self.assertFalse(result)


class TournamentRazorpayRoutingTests(SecurityTestCase):
    """Paid tournament entry fees route through the same per-cafe Razorpay logic as
    bookings: the cafe's own account if connected, the platform account otherwise, and an
    order created for one cafe/tournament can't be replayed elsewhere."""

    def setUp(self):
        super().setUp()
        self.owner_email, self.owner_token = self.make_active_user(role="admin", collection="admins")
        self.cafe_id = self.make_cafe(owner_email=self.owner_email)
        self.player_email, self.player_token = self.make_active_user(role="user")
        self.tournament_id = self._make_tournament(entry_fee=400)

    def tearDown(self):
        super().tearDown()
        self.db.cafe_payment_orders.delete_many({"cafe_id": self.cafe_id})
        self.db.tournaments.delete_many({"title": "Sectest Tournament"})
        self.db.registrations.delete_many({"tournament_title": "Sectest Tournament"})

    def _make_tournament(self, entry_fee=400, cafe_id=None):
        doc = {
            "title": "Sectest Tournament",
            "game": "VALORANT",
            "cafe_id": cafe_id if cafe_id is not None else self.cafe_id,
            "entry": "Paid Entry" if entry_fee else "Free Entry",
            "entry_fee": entry_fee,
            "capacity": 32,
            "registered": 0,
            "registration_open": True,
            "status": "upcoming",
            "mode": "Solo",
        }
        result = self.db.tournaments.insert_one(doc)
        self.track("tournaments", result.inserted_id)
        return str(result.inserted_id)

    @patch("razorpay.Client")
    def test_unconfigured_cafe_tournament_order_falls_back_to_platform(self, mock_client_cls):
        mock_client_cls.return_value = _mock_razorpay_client(order_amount=40000)
        resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/payments/create-order/",
            {"amount": 400},
            format="json",
            **self.auth_header(self.player_token),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["used_platform_fallback"])

    @patch("razorpay.Client")
    def test_registration_succeeds_with_verified_platform_fallback_payment(self, mock_client_cls):
        mock_client_cls.return_value = _mock_razorpay_client(order_amount=40000)
        order = payments.create_cafe_booking_order_handler(self.cafe_id, 400)

        resp = self.client.post(
            f"/api/v1/main/tournaments/{self.tournament_id}/register/",
            {
                "gamer_ids": ["Player1"],
                "razorpay_order_id": order["id"],
                "razorpay_payment_id": f"pay_test_{uuid.uuid4().hex[:10]}",
                "razorpay_signature": "irrelevant-mocked",
            },
            format="json",
            **self.auth_header(self.player_token),
        )
        self.assertEqual(resp.status_code, 200)

        registration = self.db.registrations.find_one({"tournament_title": "Sectest Tournament"})
        self.assertEqual(registration["payment_settlement"], "platform_pending_payout")

    @patch("razorpay.Client")
    def test_registration_stamps_direct_to_cafe_when_owner_connected(self, mock_client_cls):
        mock_client_cls.return_value = _mock_razorpay_client(order_amount=40000)
        connect_resp = _connect_owner_credentials(self, self.cafe_id, self.owner_token)
        self.assertEqual(connect_resp.status_code, 200)
        order = payments.create_cafe_booking_order_handler(self.cafe_id, 400)

        resp = self.client.post(
            f"/api/v1/main/tournaments/{self.tournament_id}/register/",
            {
                "gamer_ids": ["Player1"],
                "razorpay_order_id": order["id"],
                "razorpay_payment_id": f"pay_test_{uuid.uuid4().hex[:10]}",
                "razorpay_signature": "irrelevant-mocked",
            },
            format="json",
            **self.auth_header(self.player_token),
        )
        self.assertEqual(resp.status_code, 200)
        registration = self.db.registrations.find_one({"tournament_title": "Sectest Tournament"})
        self.assertEqual(registration["payment_settlement"], "direct_to_cafe")

    @patch("razorpay.Client")
    def test_registration_rejected_without_any_payment_fields(self, mock_client_cls):
        mock_client_cls.return_value = _mock_razorpay_client(order_amount=40000)
        resp = self.client.post(
            f"/api/v1/main/tournaments/{self.tournament_id}/register/",
            {"gamer_ids": ["Player1"]},
            format="json",
            **self.auth_header(self.player_token),
        )
        self.assertEqual(resp.status_code, 400)

    @patch("razorpay.Client")
    def test_order_from_a_different_cafe_cannot_pay_for_this_tournament(self, mock_client_cls):
        mock_client_cls.return_value = _mock_razorpay_client(order_amount=40000)
        other_owner_email, _ = self.make_active_user(role="admin", collection="admins")
        other_cafe_id = self.make_cafe(owner_email=other_owner_email)
        try:
            order = payments.create_cafe_booking_order_handler(other_cafe_id, 400)
            resp = self.client.post(
                f"/api/v1/main/tournaments/{self.tournament_id}/register/",
                {
                    "gamer_ids": ["Player1"],
                    "razorpay_order_id": order["id"],
                    "razorpay_payment_id": f"pay_test_{uuid.uuid4().hex[:10]}",
                    "razorpay_signature": "irrelevant-mocked",
                },
                format="json",
                **self.auth_header(self.player_token),
            )
            self.assertEqual(resp.status_code, 400)
        finally:
            self.db.cafe_payment_orders.delete_many({"cafe_id": other_cafe_id})

    def test_free_tournament_registration_needs_no_payment(self):
        free_tournament_id = self._make_tournament(entry_fee=0)
        resp = self.client.post(
            f"/api/v1/main/tournaments/{free_tournament_id}/register/",
            {"gamer_ids": ["Player1"]},
            format="json",
            **self.auth_header(self.player_token),
        )
        self.assertEqual(resp.status_code, 200)

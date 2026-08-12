# test_cafe_razorpay.py
# Regression tests for per-cafe Cashfree routing: a cafe owner can connect their own
# Cashfree account so booking payments go straight to them instead of the platform's
# account; cafes that haven't configured one yet fall back to the platform account
# (flagged for manual settlement later, per product decision); and an order created for
# one cafe can never be replayed to pay for a booking at a different cafe.

import uuid
from unittest.mock import MagicMock, patch

from django.test import override_settings

from ..Handlers import payments
from ..Handlers.db_connection import get_db
from .base import SecurityTestCase


CF_TEST_PASSWORD = "MyCfPassword123"


def _connect_owner_credentials(test_case, cafe_id, token, client_id="cf_test_ownerkey123", client_secret="supersecretvalue"):
    """Sets the owner's payment password (first-time setup) and connects their own
    Cashfree account in one step — every test that needs a cafe to already have its own
    credentials configured goes through this, since the save endpoint requires the
    password gate to be satisfied (see CafeCashfreeCredentialsView.put)."""
    test_case.client.post(
        f"/api/v1/main/cafes/{cafe_id}/payment-credentials/set-password/",
        {"password": CF_TEST_PASSWORD},
        format="json",
        **test_case.auth_header(token),
    )
    return test_case.client.put(
        f"/api/v1/main/cafes/{cafe_id}/payment-credentials/",
        {"client_id": client_id, "client_secret": client_secret, "payment_password": CF_TEST_PASSWORD},
        format="json",
        **test_case.auth_header(token),
    )


def _mock_cashfree_responses(order_amount, order_status="PAID"):
    """Returns (mock_post, mock_get) suitable for @patch("requests.post")/@patch("requests.get")
    — post covers order creation, get covers the order-status verification lookup."""
    order_id = f"order_test_{uuid.uuid4().hex[:12]}"

    mock_post_response = MagicMock()
    mock_post_response.raise_for_status.return_value = None
    mock_post_response.json.return_value = {
        "order_id": order_id,
        "order_amount": order_amount,
        "order_status": "ACTIVE",
        "payment_session_id": f"session_test_{uuid.uuid4().hex[:12]}",
    }

    mock_get_response = MagicMock()
    mock_get_response.raise_for_status.return_value = None
    mock_get_response.json.return_value = {"order_amount": order_amount, "order_status": order_status}

    return mock_post_response, mock_get_response


@override_settings(CASHFREE_CLIENT_ID="platform_test_client_id", CASHFREE_CLIENT_SECRET="platform_test_secret")
class CafeCashfreeCredentialsTests(SecurityTestCase):
    def setUp(self):
        super().setUp()
        self.owner_email, self.owner_token = self.make_active_user(role="admin", collection="admins")
        self.cafe_id = self.make_cafe(owner_email=self.owner_email)

    def test_starts_unconfigured(self):
        resp = self.client.get(
            f"/api/v1/main/cafes/{self.cafe_id}/payment-credentials/", **self.auth_header(self.owner_token)
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["configured"])

    def test_owner_can_save_credentials_and_status_reflects_it(self):
        resp = _connect_owner_credentials(self, self.cafe_id, self.owner_token)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["configured"])
        self.assertEqual(resp.json()["client_id"], "cf_test_ownerkey123")

        status_resp = self.client.get(
            f"/api/v1/main/cafes/{self.cafe_id}/payment-credentials/", **self.auth_header(self.owner_token)
        )
        self.assertTrue(status_resp.json()["configured"])
        self.assertEqual(status_resp.json()["client_id"], "cf_test_ownerkey123")

    def test_client_secret_is_never_echoed_back(self):
        connect_resp = _connect_owner_credentials(self, self.cafe_id, self.owner_token)
        self.assertEqual(connect_resp.status_code, 200)
        status_resp = self.client.get(
            f"/api/v1/main/cafes/{self.cafe_id}/payment-credentials/", **self.auth_header(self.owner_token)
        )
        self.assertNotIn("client_secret", status_resp.json())
        self.assertNotIn("supersecretvalue", str(status_resp.json()))

    def test_secret_is_encrypted_at_rest_not_stored_plaintext(self):
        connect_resp = _connect_owner_credentials(self, self.cafe_id, self.owner_token)
        self.assertEqual(connect_resp.status_code, 200)
        from bson import ObjectId
        doc = self.db.cafes.find_one({"_id": ObjectId(self.cafe_id)})
        self.assertNotEqual(doc["cashfree_client_secret_enc"], "supersecretvalue")
        self.assertNotIn("supersecretvalue", doc["cashfree_client_secret_enc"])

    def test_missing_client_secret_is_rejected(self):
        self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/payment-credentials/set-password/",
            {"password": CF_TEST_PASSWORD},
            format="json",
            **self.auth_header(self.owner_token),
        )
        resp = self.client.put(
            f"/api/v1/main/cafes/{self.cafe_id}/payment-credentials/",
            {"client_id": "cf_test_incomplete", "client_secret": "", "payment_password": CF_TEST_PASSWORD},
            format="json",
            **self.auth_header(self.owner_token),
        )
        self.assertEqual(resp.status_code, 400)

    def test_unrelated_admin_cannot_save_for_someone_elses_cafe(self):
        attacker_email, attacker_token = self.make_active_user(role="admin", collection="admins")
        self.make_cafe(owner_email=attacker_email)

        resp = self.client.put(
            f"/api/v1/main/cafes/{self.cafe_id}/payment-credentials/",
            {"client_id": "cf_test_attacker", "client_secret": "hijack"},
            format="json",
            **self.auth_header(attacker_token),
        )
        self.assertEqual(resp.status_code, 403)

    def test_owner_can_disconnect(self):
        connect_resp = _connect_owner_credentials(self, self.cafe_id, self.owner_token)
        self.assertEqual(connect_resp.status_code, 200)
        resp = self.client.delete(
            f"/api/v1/main/cafes/{self.cafe_id}/payment-credentials/",
            {"payment_password": CF_TEST_PASSWORD},
            format="json",
            **self.auth_header(self.owner_token),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["configured"])

    def test_disconnect_without_password_is_rejected(self):
        connect_resp = _connect_owner_credentials(self, self.cafe_id, self.owner_token)
        self.assertEqual(connect_resp.status_code, 200)
        resp = self.client.delete(
            f"/api/v1/main/cafes/{self.cafe_id}/payment-credentials/", **self.auth_header(self.owner_token)
        )
        self.assertEqual(resp.status_code, 403)


@override_settings(CASHFREE_CLIENT_ID="platform_test_client_id", CASHFREE_CLIENT_SECRET="platform_test_secret")
class CafeBookingOrderCreationTests(SecurityTestCase):
    def setUp(self):
        super().setUp()
        self.owner_email, self.owner_token = self.make_active_user(role="admin", collection="admins")
        self.cafe_id = self.make_cafe(owner_email=self.owner_email)
        self.customer_email, self.customer_token = self.make_active_user(role="user")

    def tearDown(self):
        super().tearDown()
        self.db.cafe_payment_orders.delete_many({"cafe_id": self.cafe_id})

    @patch("requests.post")
    def test_unconfigured_cafe_falls_back_to_platform_account(self, mock_post):
        mock_post.return_value, _ = _mock_cashfree_responses(order_amount=200)
        resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/payments/create-order/",
            {"amount": 200},
            format="json",
            **self.auth_header(self.customer_token),
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["used_platform_fallback"])

    @patch("requests.post")
    def test_configured_cafe_routes_to_its_own_account(self, mock_post):
        mock_post.return_value, _ = _mock_cashfree_responses(order_amount=200)
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
        # The actual x-client-id used for the (mocked) Cashfree call is whatever
        # get_cafe_cashfree_credentials resolved — asserted indirectly via
        # used_platform_fallback above; the call itself is mocked so there's no live
        # credential value to assert on here.

    def test_requires_login(self):
        resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/payments/create-order/", {"amount": 200}, format="json"
        )
        self.assertEqual(resp.status_code, 401)


@override_settings(CASHFREE_CLIENT_ID="platform_test_client_id", CASHFREE_CLIENT_SECRET="platform_test_secret")
class CafeBookingPaymentVerificationTests(SecurityTestCase):
    """Exercises payments.verify_cashfree_payment(cafe_id=...) directly — the HTTP-level
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
        get_db().used_cashfree_payments.delete_many({"order_id": {"$regex": "^order_test_"}})

    @patch("requests.get")
    @patch("requests.post")
    def test_platform_fallback_order_verifies_successfully(self, mock_post, mock_get):
        mock_post.return_value, mock_get.return_value = _mock_cashfree_responses(order_amount=200)
        order = payments.create_cafe_booking_order_handler(self.cafe_id, 200)
        self.assertTrue(order["used_platform_fallback"])

        result = payments.verify_cashfree_payment(order["order_id"], 20000, cafe_id=self.cafe_id)
        self.assertTrue(result)

    @patch("requests.get")
    @patch("requests.post")
    def test_cafes_own_account_order_verifies_successfully(self, mock_post, mock_get):
        mock_post.return_value, mock_get.return_value = _mock_cashfree_responses(order_amount=200)
        connect_resp = _connect_owner_credentials(self, self.cafe_id, self.owner_token)
        self.assertEqual(connect_resp.status_code, 200)
        order = payments.create_cafe_booking_order_handler(self.cafe_id, 200)
        self.assertFalse(order["used_platform_fallback"])

        result = payments.verify_cashfree_payment(order["order_id"], 20000, cafe_id=self.cafe_id)
        self.assertTrue(result)

    @patch("requests.get")
    @patch("requests.post")
    def test_order_from_one_cafe_cannot_pay_for_a_booking_at_another_cafe(self, mock_post, mock_get):
        mock_post.return_value, mock_get.return_value = _mock_cashfree_responses(order_amount=200)
        order = payments.create_cafe_booking_order_handler(self.cafe_id, 200)

        result = payments.verify_cashfree_payment(order["order_id"], 20000, cafe_id=self.other_cafe_id)
        self.assertFalse(result, "An order created for one cafe must not verify a booking at a different cafe.")

    @patch("requests.get")
    def test_unknown_order_id_is_rejected(self, mock_get):
        _, mock_get.return_value = _mock_cashfree_responses(order_amount=200)
        result = payments.verify_cashfree_payment("order_never_created", 20000, cafe_id=self.cafe_id)
        self.assertFalse(result)


@override_settings(CASHFREE_CLIENT_ID="platform_test_client_id", CASHFREE_CLIENT_SECRET="platform_test_secret")
class TournamentCashfreeRoutingTests(SecurityTestCase):
    """Paid tournament entry fees route through the same per-cafe Cashfree logic as
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

    @patch("requests.post")
    def test_unconfigured_cafe_tournament_order_falls_back_to_platform(self, mock_post):
        mock_post.return_value, _ = _mock_cashfree_responses(order_amount=400)
        resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/payments/create-order/",
            {"amount": 400},
            format="json",
            **self.auth_header(self.player_token),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["used_platform_fallback"])

    @patch("requests.get")
    @patch("requests.post")
    def test_registration_succeeds_with_verified_platform_fallback_payment(self, mock_post, mock_get):
        mock_post.return_value, mock_get.return_value = _mock_cashfree_responses(order_amount=400)
        order = payments.create_cafe_booking_order_handler(self.cafe_id, 400)

        resp = self.client.post(
            f"/api/v1/main/tournaments/{self.tournament_id}/register/",
            {
                "gamer_ids": ["Player1"],
                "cashfree_order_id": order["order_id"],
            },
            format="json",
            **self.auth_header(self.player_token),
        )
        self.assertEqual(resp.status_code, 200)

        registration = self.db.registrations.find_one({"tournament_title": "Sectest Tournament"})
        self.assertEqual(registration["payment_settlement"], "platform_pending_payout")

    @patch("requests.get")
    @patch("requests.post")
    def test_registration_stamps_direct_to_cafe_when_owner_connected(self, mock_post, mock_get):
        mock_post.return_value, mock_get.return_value = _mock_cashfree_responses(order_amount=400)
        connect_resp = _connect_owner_credentials(self, self.cafe_id, self.owner_token)
        self.assertEqual(connect_resp.status_code, 200)
        order = payments.create_cafe_booking_order_handler(self.cafe_id, 400)

        resp = self.client.post(
            f"/api/v1/main/tournaments/{self.tournament_id}/register/",
            {
                "gamer_ids": ["Player1"],
                "cashfree_order_id": order["order_id"],
            },
            format="json",
            **self.auth_header(self.player_token),
        )
        self.assertEqual(resp.status_code, 200)
        registration = self.db.registrations.find_one({"tournament_title": "Sectest Tournament"})
        self.assertEqual(registration["payment_settlement"], "direct_to_cafe")

    def test_registration_rejected_without_any_payment_fields(self):
        resp = self.client.post(
            f"/api/v1/main/tournaments/{self.tournament_id}/register/",
            {"gamer_ids": ["Player1"]},
            format="json",
            **self.auth_header(self.player_token),
        )
        self.assertEqual(resp.status_code, 400)

    @patch("requests.get")
    @patch("requests.post")
    def test_order_from_a_different_cafe_cannot_pay_for_this_tournament(self, mock_post, mock_get):
        mock_post.return_value, mock_get.return_value = _mock_cashfree_responses(order_amount=400)
        other_owner_email, _ = self.make_active_user(role="admin", collection="admins")
        other_cafe_id = self.make_cafe(owner_email=other_owner_email)
        try:
            order = payments.create_cafe_booking_order_handler(other_cafe_id, 400)
            resp = self.client.post(
                f"/api/v1/main/tournaments/{self.tournament_id}/register/",
                {
                    "gamer_ids": ["Player1"],
                    "cashfree_order_id": order["order_id"],
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

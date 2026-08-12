# test_booking_security.py
# Regression tests for: booking detail IDOR (update), booking-list IDOR (cafe_id scoped
# listing), and the payment-integrity fix (server-side price + Cashfree verification).

from bson import ObjectId

from .base import SecurityTestCase


class BookingDetailOwnershipTests(SecurityTestCase):
    """Guards against any authenticated user modifying another user's booking."""

    def test_unrelated_user_cannot_update_someone_elses_booking(self):
        owner_email, _ = self.make_active_user()
        attacker_email, attacker_token = self.make_active_user()
        cafe_id = self.make_cafe(owner_email="cafe-owner-b@bookmyconsole.invalid")
        booking_id = self.make_booking(owner_email, cafe_id, payment_status="pending")

        resp = self.client.put(
            f"/api/v1/main/bookings/{booking_id}/",
            {"payment_status": "paid"},
            format="json",
            **self.auth_header(attacker_token),
        )
        self.assertEqual(resp.status_code, 403)
        doc = self.db.bookings.find_one({"_id": ObjectId(booking_id)})
        self.assertEqual(doc["payment_status"], "pending")

    def test_owner_cannot_self_mark_their_own_booking_as_paid(self):
        """A booking's own user is not a 'privileged' caller — only the cafe owner/super
        admin may change payment_status, otherwise a user could grant themselves a free
        paid booking by PUTting their own record."""
        owner_email, owner_token = self.make_active_user()
        cafe_id = self.make_cafe(owner_email="cafe-owner-d@bookmyconsole.invalid")
        booking_id = self.make_booking(owner_email, cafe_id, payment_status="pending")

        # Mix payment_status in with a field an owner IS allowed to touch, to prove
        # payment_status specifically gets filtered out rather than the whole request
        # merely having nothing left to update.
        resp = self.client.put(
            f"/api/v1/main/bookings/{booking_id}/",
            {"status": "Cancelled", "payment_status": "paid"},
            format="json",
            **self.auth_header(owner_token),
        )
        self.assertEqual(resp.status_code, 200)  # allowed to touch their own booking...
        doc = self.db.bookings.find_one({"_id": ObjectId(booking_id)})
        self.assertEqual(doc["status"], "Cancelled")       # ...the allowed field updates...
        self.assertEqual(doc["payment_status"], "pending")  # ...but payment_status must not move

    def test_cafe_owner_can_mark_a_customers_booking_as_paid(self):
        cafe_owner_email, cafe_owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=cafe_owner_email)
        customer_email, _ = self.make_active_user()
        booking_id = self.make_booking(customer_email, cafe_id, payment_status="pending")

        resp = self.client.put(
            f"/api/v1/main/bookings/{booking_id}/",
            {"payment_status": "paid"},
            format="json",
            **self.auth_header(cafe_owner_token),
        )
        self.assertEqual(resp.status_code, 200)
        doc = self.db.bookings.find_one({"_id": ObjectId(booking_id)})
        self.assertEqual(doc["payment_status"], "paid")


class BookingListOwnershipTests(SecurityTestCase):
    """Guards against any authenticated user listing another cafe's full booking data
    (customer names/emails/phone numbers/codes) via ?cafe_id=."""

    def test_unrelated_user_cannot_list_bookings_by_cafe_id(self):
        cafe_owner_email, _ = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=cafe_owner_email)
        customer_email, customer_token = self.make_active_user()
        self.make_booking(customer_email, cafe_id)

        resp = self.client.get(f"/api/v1/main/bookings/?cafe_id={cafe_id}", **self.auth_header(customer_token))
        self.assertEqual(resp.status_code, 403)

    def test_cafe_owner_can_list_bookings_by_cafe_id(self):
        cafe_owner_email, cafe_owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=cafe_owner_email)
        customer_email, _ = self.make_active_user()
        self.make_booking(customer_email, cafe_id)

        resp = self.client.get(f"/api/v1/main/bookings/?cafe_id={cafe_id}", **self.auth_header(cafe_owner_token))
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.json()["bookings"]), 1)

    def test_super_admin_can_list_bookings_by_cafe_id(self):
        # A dynamic super_admin JWT, not the static ADMIN_TOKEN — the static token isn't
        # accepted by authenticate_request (JWT/cookie only), which this view also calls,
        # independent of the cafe_id-ownership fix under test here.
        cafe_owner_email, _ = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=cafe_owner_email)
        customer_email, _ = self.make_active_user()
        self.make_booking(customer_email, cafe_id)
        _, super_admin_token = self.make_active_user(role="super_admin", collection="super_admin")

        resp = self.client.get(f"/api/v1/main/bookings/?cafe_id={cafe_id}", **self.auth_header(super_admin_token))
        self.assertEqual(resp.status_code, 200)


class BookingNoSqlInjectionTests(SecurityTestCase):
    """Guards against cafe_id/date being interpreted as a MongoDB query operator instead of
    a literal value — create_booking_handler passes them straight into query filters."""

    def test_dollar_ne_cafe_id_does_not_leak_conflicts_from_an_unrelated_cafe(self):
        # Cafe A has a real, existing booking for PC #01 at a specific slot.
        cafe_a = self.make_cafe(owner_email="cafe-owner-injA@bookmyconsole.invalid", price_per_hour=100)
        self.make_booking(user_email="other-user@bookmyconsole.invalid", cafe_id=cafe_a)

        # The attacker's own cafe (unrelated to cafe A) has no bookings at all.
        customer_email, customer_token = self.make_active_user()

        # If cafe_id were passed unsanitized into the Mongo filter, {"$ne": "<bogus>"} would
        # match every booking in the collection (including cafe A's), falsely reporting a
        # conflict for cafe B — which has nothing booked at all — for the identical rig/slot.
        resp = self.client.post(
            "/api/v1/main/bookings/",
            {
                "cafe_id": {"$ne": "000000000000000000000000"},
                "cafe_name": "Sectest Cafe",
                "zone": "Regular Zone",
                "date": "2099-01-01",
                "slots": ["10:00 AM - 11:00 AM"],
                "rig": "PC #01",
            },
            format="json",
            **self.auth_header(customer_token),
        )
        # Coerced to a literal string, it can't match cafe A's real booking, so this must
        # NOT be the cross-cafe "Conflict detected" error the injection would otherwise cause.
        if resp.status_code == 400:
            self.assertNotIn("Conflict detected", resp.json().get("message", ""))


class BookingPaymentIntegrityTests(SecurityTestCase):
    """Guards against client-controlled price/payment_status bypassing real payment."""

    def test_paid_slot_booking_rejected_without_payment_verification(self):
        customer_email, customer_token = self.make_active_user()
        cafe_id = self.make_cafe(owner_email="cafe-owner-e@bookmyconsole.invalid", price_per_hour=200)

        resp = self.client.post(
            "/api/v1/main/bookings/",
            {
                "cafe_id": cafe_id,
                "cafe_name": "Sectest Cafe",
                "zone": "Regular Zone",
                "date": "2099-01-01",
                "slots": ["10:00 AM - 11:00 AM"],
                "price": 1,          # attacker-supplied bogus low price — must be ignored
                "paymentStatus": "paid",  # attacker-supplied — must be ignored
            },
            format="json",
            **self.auth_header(customer_token),
        )
        self.assertEqual(resp.status_code, 402)
        # nothing should have been booked
        self.assertIsNone(self.db.bookings.find_one({"cafe_id": cafe_id, "user_email": customer_email}))

    def test_free_slot_booking_succeeds_without_payment(self):
        customer_email, customer_token = self.make_active_user()
        cafe_id = self.make_cafe(owner_email="cafe-owner-f@bookmyconsole.invalid", price_per_hour=0)

        resp = self.client.post(
            "/api/v1/main/bookings/",
            {
                "cafe_id": cafe_id,
                "cafe_name": "Sectest Cafe",
                "zone": "Regular Zone",
                "date": "2099-01-02",
                "slots": ["11:00 AM - 12:00 PM"],
            },
            format="json",
            **self.auth_header(customer_token),
        )
        self.assertEqual(resp.status_code, 201)
        booking = resp.json()["booking"]
        self.track("bookings", ObjectId(booking["id"]))
        self.assertEqual(booking["price"], 0)

    def test_price_is_computed_server_side_from_cafe_rate_not_client_input(self):
        customer_email, customer_token = self.make_active_user()
        cafe_id = self.make_cafe(owner_email="cafe-owner-g@bookmyconsole.invalid", price_per_hour=0)

        resp = self.client.post(
            "/api/v1/main/bookings/",
            {
                "cafe_id": cafe_id,
                "cafe_name": "Sectest Cafe",
                "zone": "Regular Zone",
                "date": "2099-01-03",
                "slots": ["01:00 PM - 02:00 PM"],
                "price": 999999,  # attacker tries to inflate/deflate — must be ignored either way
            },
            format="json",
            **self.auth_header(customer_token),
        )
        self.assertEqual(resp.status_code, 201)
        booking = resp.json()["booking"]
        self.track("bookings", ObjectId(booking["id"]))
        self.assertEqual(booking["price"], 0)  # cafe's real rate (0), not the client's 999999

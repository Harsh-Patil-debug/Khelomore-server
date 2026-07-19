# test_subscriptions.py
# Regression tests for the ₹1599/month cafe-owner platform subscription: a brand-new
# cafe's 15-day free trial, default backfill for pre-existing (pre-trial-feature) cafes,
# Razorpay order/verify extending the due date correctly, the manual super-admin
# override, ownership enforcement, and the grace-period cutoff that hides an unpaid cafe
# from the public listing without locking the owner out of their own dashboard.

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from ..Handlers import subscriptions
from ..Handlers.db_connection import get_db
from .base import SecurityTestCase


class AddOneMonthHelperTests(unittest.TestCase):
    """Pure unit tests for subscriptions._add_one_month — no DB needed. Guards against
    the flat-30-days-drifts-against-real-months bug directly, rather than only indirectly
    through the API-level tests below."""

    def test_mid_month_date_advances_normally(self):
        self.assertEqual(
            subscriptions._add_one_month(datetime(2026, 3, 10, 14, 30)),
            datetime(2026, 4, 10, 14, 30),
        )

    def test_january_31_clamps_to_february_28_in_a_non_leap_year(self):
        self.assertEqual(
            subscriptions._add_one_month(datetime(2027, 1, 31, 9, 0)),
            datetime(2027, 2, 28, 9, 0),
        )

    def test_january_31_clamps_to_february_29_in_a_leap_year(self):
        self.assertEqual(
            subscriptions._add_one_month(datetime(2028, 1, 31, 9, 0)),
            datetime(2028, 2, 29, 9, 0),
        )

    def test_august_31_clamps_to_september_30(self):
        self.assertEqual(
            subscriptions._add_one_month(datetime(2026, 8, 31)),
            datetime(2026, 9, 30),
        )

    def test_december_rolls_over_to_january_of_the_next_year(self):
        self.assertEqual(
            subscriptions._add_one_month(datetime(2026, 12, 15)),
            datetime(2027, 1, 15),
        )

    def test_twelve_consecutive_months_land_on_the_same_calendar_date_a_year_later(self):
        """The whole point of this fix: chaining twelve real calendar months from a
        mid-month anchor must land exactly one year later, unlike 12 * 30 = 360 days
        (which would be 5-6 days short of a real year)."""
        start = datetime(2026, 3, 15)
        current = start
        for _ in range(12):
            current = subscriptions._add_one_month(current)
        self.assertEqual(current, datetime(2027, 3, 15))

    def test_timezone_is_preserved(self):
        aware = datetime(2026, 1, 31, 12, 0, tzinfo=subscriptions.IST)
        result = subscriptions._add_one_month(aware)
        self.assertEqual(result.tzinfo, subscriptions.IST)
        self.assertEqual(result, datetime(2026, 2, 28, 12, 0, tzinfo=subscriptions.IST))


def _mock_razorpay_client(order_amount, order_status="paid", signature_valid=True):
    mock_client = MagicMock()
    if signature_valid:
        mock_client.utility.verify_payment_signature.return_value = True
    else:
        import razorpay.errors
        mock_client.utility.verify_payment_signature.side_effect = razorpay.errors.SignatureVerificationError("bad sig")
    mock_client.order.fetch.return_value = {"amount": order_amount, "status": order_status}
    return mock_client


class SubscriptionDefaultsTests(SecurityTestCase):
    def test_brand_new_cafe_gets_a_fifteen_day_free_trial(self):
        """A cafe with no subscription fields at all yet (the lazy-init "never touched
        before" branch of _ensure_defaults) is a brand-new cafe — it must start in
        "trial", not "active", with its due date exactly SUBSCRIPTION_TRIAL_DAYS out at
        the new ₹1599 rate."""
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)

        resp = self.client.get(
            f"/api/v1/main/cafes/{cafe_id}/subscription/", **self.auth_header(owner_token)
        )
        self.assertEqual(resp.status_code, 200)
        sub = resp.json()["subscription"]
        self.assertEqual(sub["status"], "trial")
        self.assertEqual(sub["amount"], 1599)
        self.assertTrue(14 <= sub["days_remaining"] <= 15, sub)
        due_date = datetime.fromisoformat(sub["due_date"])
        trial_end = datetime.fromisoformat(sub["trial_end"])
        self.assertAlmostEqual((due_date - trial_end).total_seconds(), 0, delta=1)
        # No grace period for the trial itself — grace_until must equal due_date exactly
        # (zero days), not due_date + SUBSCRIPTION_GRACE_DAYS.
        grace_until = datetime.fromisoformat(sub["grace_until"])
        self.assertAlmostEqual((grace_until - due_date).total_seconds(), 0, delta=1)

    def test_unpaid_trial_suspends_immediately_with_no_overdue_window(self):
        """The core behavior this guards: once an unpaid trial's due date passes, status
        must jump straight to "suspended" — "overdue" must never appear for a trial."""
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        # Force the trial's due date into the past, simulating "the 15 days ran out".
        self.db.cafes.update_one(
            {"_id": subscriptions._safe_oid(cafe_id)},
            {"$set": {
                "subscription_due_date": datetime.now(timezone.utc) - timedelta(seconds=5),
                "subscription_grace_until": datetime.now(timezone.utc) - timedelta(seconds=5),
                "subscription_trial_end": datetime.now(timezone.utc) - timedelta(seconds=5),
                "subscription_amount": 1599,
            }},
        )

        resp = self.client.get(
            f"/api/v1/main/cafes/{cafe_id}/subscription/", **self.auth_header(owner_token)
        )
        sub = resp.json()["subscription"]
        self.assertEqual(sub["status"], "suspended")

    def test_cafe_that_already_had_subscription_fields_never_gets_a_trial(self):
        """A cafe already on the OLD ₹1500 rate before this trial feature shipped must
        never retroactively show "trial" — only the "no subscription fields at all yet"
        branch grants one."""
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        self.db.cafes.update_one(
            {"_id": subscriptions._safe_oid(cafe_id)},
            {"$set": {
                "subscription_due_date": datetime.now(timezone.utc) + timedelta(days=20),
                "subscription_grace_until": datetime.now(timezone.utc) + timedelta(days=27),
                "subscription_amount": 1500,
                "subscription_status": "active",
            }},
        )

        resp = self.client.get(
            f"/api/v1/main/cafes/{cafe_id}/subscription/", **self.auth_header(owner_token)
        )
        sub = resp.json()["subscription"]
        self.assertEqual(sub["status"], "active")
        self.assertEqual(sub["amount"], 1500)
        self.assertIsNone(sub["trial_end"])


class SubscriptionOwnershipTests(SecurityTestCase):
    def test_owner_can_view_their_own_subscription(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        resp = self.client.get(
            f"/api/v1/main/cafes/{cafe_id}/subscription/", **self.auth_header(owner_token)
        )
        self.assertEqual(resp.status_code, 200)

    def test_unrelated_admin_cannot_view_someone_elses_subscription(self):
        owner_email, _ = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        attacker_email, attacker_token = self.make_active_user(role="admin")
        self.make_cafe(owner_email=attacker_email)

        resp = self.client.get(
            f"/api/v1/main/cafes/{cafe_id}/subscription/", **self.auth_header(attacker_token)
        )
        self.assertEqual(resp.status_code, 403)

    def test_unrelated_admin_cannot_create_an_order_for_someone_elses_cafe(self):
        owner_email, _ = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        attacker_email, attacker_token = self.make_active_user(role="admin")
        self.make_cafe(owner_email=attacker_email)

        resp = self.client.post(
            f"/api/v1/main/cafes/{cafe_id}/subscription/create-order/", **self.auth_header(attacker_token)
        )
        self.assertEqual(resp.status_code, 403)

    def test_super_admin_can_view_any_cafes_subscription(self):
        owner_email, _ = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        resp = self.client.get(f"/api/v1/main/cafes/{cafe_id}/subscription/", **self.admin_header())
        self.assertEqual(resp.status_code, 200)


class SubscriptionOrderAndPaymentTests(SecurityTestCase):
    def setUp(self):
        super().setUp()
        self.owner_email, self.owner_token = self.make_active_user(role="admin")
        self.cafe_id = self.make_cafe(owner_email=self.owner_email)

    def tearDown(self):
        super().tearDown()
        get_db().subscription_payments.delete_many({"cafe_id": self.cafe_id})

    def test_create_order_is_for_fifteen_ninety_nine_rupees(self):
        resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/subscription/create-order/", **self.auth_header(self.owner_token)
        )
        self.assertEqual(resp.status_code, 200)
        order = resp.json()["order"]
        self.assertEqual(order["amount"], 159900)  # paise

    @patch("razorpay.Client")
    def test_verified_payment_during_trial_extends_from_trial_end_and_clears_trial_status(self, mock_client_cls):
        mock_client_cls.return_value = _mock_razorpay_client(order_amount=159900, order_status="paid")

        before = self.client.get(
            f"/api/v1/main/cafes/{self.cafe_id}/subscription/", **self.auth_header(self.owner_token)
        ).json()["subscription"]
        self.assertEqual(before["status"], "trial")
        due_before = datetime.fromisoformat(before["due_date"])

        resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/subscription/verify/",
            {
                "razorpay_order_id": f"order_test_{uuid.uuid4().hex[:10]}",
                "razorpay_payment_id": f"pay_test_{uuid.uuid4().hex[:10]}",
                "razorpay_signature": "irrelevant-mocked",
            },
            format="json",
            **self.auth_header(self.owner_token),
        )
        self.assertEqual(resp.status_code, 200)
        sub = resp.json()["subscription"]
        # Paying during the trial converts it to a real paid subscription immediately —
        # never shows "trial" again even though today is still before the old trial_end.
        self.assertEqual(sub["status"], "active")
        self.assertIsNone(sub["trial_end"])
        due_after = datetime.fromisoformat(sub["due_date"])
        # Extends FROM the trial's end date by exactly one CALENDAR month, not reset to
        # today+30 — paying early during the trial must never shorten the free period,
        # and a flat 30-day assumption would be wrong for any month that isn't 30 days.
        expected_due_after = subscriptions._add_one_month(due_before)
        self.assertAlmostEqual((due_after - expected_due_after).total_seconds(), 0, delta=5)
        # Once paid, this cycle (and every one after it) gets the normal 7-day grace
        # period — the zero-grace rule only applies to an unpaid trial itself.
        grace_after = datetime.fromisoformat(sub["grace_until"])
        expected_grace_after = due_after + timedelta(days=subscriptions.SUBSCRIPTION_GRACE_DAYS)
        self.assertAlmostEqual((grace_after - expected_grace_after).total_seconds(), 0, delta=5)

        history = self.client.get(
            f"/api/v1/main/cafes/{self.cafe_id}/subscription/", **self.auth_header(self.owner_token)
        ).json()["history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["amount"], 1599)
        self.assertEqual(history[0]["method"], "razorpay")

    @patch("razorpay.Client")
    def test_verify_rejects_wrong_amount_and_does_not_extend(self, mock_client_cls):
        """A genuinely-paid order for the wrong amount must not unlock a renewal —
        same class of bug this pattern already guards against for bookings."""
        mock_client_cls.return_value = _mock_razorpay_client(order_amount=100, order_status="paid")

        before = self.client.get(
            f"/api/v1/main/cafes/{self.cafe_id}/subscription/", **self.auth_header(self.owner_token)
        ).json()["subscription"]

        resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/subscription/verify/",
            {
                "razorpay_order_id": f"order_test_{uuid.uuid4().hex[:10]}",
                "razorpay_payment_id": f"pay_test_{uuid.uuid4().hex[:10]}",
                "razorpay_signature": "irrelevant-mocked",
            },
            format="json",
            **self.auth_header(self.owner_token),
        )
        self.assertEqual(resp.status_code, 402)

        after = self.client.get(
            f"/api/v1/main/cafes/{self.cafe_id}/subscription/", **self.auth_header(self.owner_token)
        ).json()["subscription"]
        # Compare with a small tolerance, not exact string equality — MongoDB truncates
        # datetimes to millisecond precision on round-trip, so the "before" value (still
        # held in-memory at full Python precision) can differ from "after" (freshly
        # re-read from Mongo) by a fraction of a millisecond even though nothing changed.
        before_dt = datetime.fromisoformat(before["due_date"])
        after_dt = datetime.fromisoformat(after["due_date"])
        self.assertAlmostEqual((after_dt - before_dt).total_seconds(), 0, delta=1)

    def test_manual_mark_paid_requires_super_admin(self):
        # A valid token belonging to a real (but non-super-admin) account is a 403 "wrong
        # role", not a 401 "no/invalid token" — matches this codebase's established
        # distinction elsewhere (see e.g. TournamentOwnerMutationTests).
        resp = self.client.post(
            f"/api/v1/main/subscriptions/{self.cafe_id}/mark-paid/", **self.auth_header(self.owner_token)
        )
        self.assertEqual(resp.status_code, 403)

    def test_super_admin_manual_mark_paid_extends_subscription(self):
        before = self.client.get(
            f"/api/v1/main/cafes/{self.cafe_id}/subscription/", **self.auth_header(self.owner_token)
        ).json()["subscription"]
        due_before = datetime.fromisoformat(before["due_date"])

        resp = self.client.post(f"/api/v1/main/subscriptions/{self.cafe_id}/mark-paid/", **self.admin_header())
        self.assertEqual(resp.status_code, 200)
        due_after = datetime.fromisoformat(resp.json()["subscription"]["due_date"])
        expected_due_after = subscriptions._add_one_month(due_before)
        self.assertAlmostEqual((due_after - expected_due_after).total_seconds(), 0, delta=5)

        history = self.client.get(
            f"/api/v1/main/cafes/{self.cafe_id}/subscription/", **self.auth_header(self.owner_token)
        ).json()["history"]
        self.assertEqual(history[0]["method"], "manual")


class SubscriptionGracePeriodEnforcementTests(SecurityTestCase):
    """The core enforcement guarantee: only actually-suspended (past grace) cafes vanish
    from the public listing, and even then only there — never from the owner's own view."""

    def _set_subscription_state(self, cafe_id, due_offset_days, grace_offset_days):
        now = datetime.now(timezone.utc)
        self.db.cafes.update_one(
            {"_id": subscriptions._safe_oid(cafe_id)},
            {"$set": {
                "subscription_due_date": now + timedelta(days=due_offset_days),
                "subscription_grace_until": now + timedelta(days=grace_offset_days),
                "subscription_amount": 1500,
                "subscription_status": "suspended" if grace_offset_days < 0 else "active",
            }},
        )

    def test_suspended_cafe_is_hidden_from_public_listing(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        self._set_subscription_state(cafe_id, due_offset_days=-10, grace_offset_days=-3)

        resp = self.client.get("/api/v1/main/cafes/")
        ids = {c["id"] for c in resp.json()["cafes"]}
        self.assertNotIn(cafe_id, ids)

    def test_suspended_cafe_still_visible_to_its_own_owner(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        self._set_subscription_state(cafe_id, due_offset_days=-10, grace_offset_days=-3)

        resp = self.client.get("/api/v1/main/cafes/my/", **self.auth_header(owner_token))
        ids = {c["id"] for c in resp.json()["cafes"]}
        self.assertIn(cafe_id, ids)

    def test_overdue_but_within_grace_still_shows_publicly(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        self._set_subscription_state(cafe_id, due_offset_days=-2, grace_offset_days=5)

        resp = self.client.get("/api/v1/main/cafes/")
        ids = {c["id"] for c in resp.json()["cafes"]}
        self.assertIn(cafe_id, ids)

    def test_cafe_predating_this_feature_is_not_retroactively_hidden(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        # No subscription_grace_until field at all — never touched.

        resp = self.client.get("/api/v1/main/cafes/")
        ids = {c["id"] for c in resp.json()["cafes"]}
        self.assertIn(cafe_id, ids)


class SubscriptionsListAccessTests(SecurityTestCase):
    def test_requires_super_admin(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        self.make_cafe(owner_email=owner_email)
        resp = self.client.get("/api/v1/main/subscriptions/", **self.auth_header(owner_token))
        self.assertEqual(resp.status_code, 403)

    def test_super_admin_sees_the_cafe_in_the_list(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        resp = self.client.get("/api/v1/main/subscriptions/", **self.admin_header())
        self.assertEqual(resp.status_code, 200)
        ids = {r["cafe_id"] for r in resp.json()["subscriptions"]}
        self.assertIn(cafe_id, ids)


class TrialWelcomePopupTests(SecurityTestCase):
    """The one-time "you're on a 15-day free trial" popup cafe-command-center shows a
    brand-new owner on first login — must default to unseen and flip to seen exactly
    once, persisted server-side (not just localStorage) so it doesn't reappear on
    another device/browser."""

    def test_new_cafe_has_not_seen_the_welcome_popup_yet(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)

        resp = self.client.get(
            f"/api/v1/main/cafes/{cafe_id}/subscription/", **self.auth_header(owner_token)
        )
        sub = resp.json()["subscription"]
        self.assertEqual(sub["status"], "trial")
        self.assertFalse(sub["trial_welcome_shown"])

    def test_marking_shown_persists_and_is_idempotent(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)

        resp = self.client.post(
            f"/api/v1/main/cafes/{cafe_id}/subscription/trial-welcome-shown/", **self.auth_header(owner_token)
        )
        self.assertEqual(resp.status_code, 200)

        after = self.client.get(
            f"/api/v1/main/cafes/{cafe_id}/subscription/", **self.auth_header(owner_token)
        ).json()["subscription"]
        self.assertTrue(after["trial_welcome_shown"])

        # Calling it again (e.g. a double-click, or the modal re-firing before the query
        # cache refreshes) must not error.
        resp2 = self.client.post(
            f"/api/v1/main/cafes/{cafe_id}/subscription/trial-welcome-shown/", **self.auth_header(owner_token)
        )
        self.assertEqual(resp2.status_code, 200)

    def test_unrelated_admin_cannot_mark_someone_elses_cafe(self):
        owner_email, _ = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        attacker_email, attacker_token = self.make_active_user(role="admin")
        self.make_cafe(owner_email=attacker_email)

        resp = self.client.post(
            f"/api/v1/main/cafes/{cafe_id}/subscription/trial-welcome-shown/", **self.auth_header(attacker_token)
        )
        self.assertEqual(resp.status_code, 403)

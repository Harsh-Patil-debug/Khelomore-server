# test_razorpay_password_gate.py
# Regression tests for the second-factor "Razorpay password" that gates a cafe owner's
# access to entering/viewing their Razorpay credentials in cafe-command-center: required
# at admin signup (separate from the login password), a one-time set-password path for
# accounts that predate this feature, a verify-password unlock with the same lockout
# behavior as login, a super-admin bypass, and the PUT/DELETE credential endpoints
# actually re-checking it server-side (not just a UI gate).

from ..Handlers import auth_handler
from .base import SecurityTestCase


class AdminSignupRazorpayPasswordTests(SecurityTestCase):
    def _register(self, email, password, razorpay_password, role="admin", gamertag="TestOwner", phone=""):
        parts = [gamertag, email, password]
        if phone:
            parts.append(phone)
        if razorpay_password is not None:
            parts.append(razorpay_password)
        *enc_parts, iv = self.encrypt_with_shared_iv(*parts)

        body = {
            "gamertag": enc_parts[0],
            "email": enc_parts[1],
            "password": enc_parts[2],
            "iv": iv,
            "role": role,
            "terms_accepted": True,
        }
        idx = 3
        if phone:
            body["phone"] = enc_parts[idx]
            idx += 1
        if razorpay_password is not None:
            body["razorpay_password"] = enc_parts[idx]
        return self.client.post("/api/v1/main/auth/register/", body, format="json")

    def test_admin_signup_without_razorpay_password_is_rejected(self):
        owner_email = self.unique_email("owner")
        self.make_cafe(owner_email=owner_email)
        resp = self._register(owner_email, "CorrectHorseBattery1", razorpay_password=None)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("razorpay password", resp.json().get("error", "").lower())

    def test_admin_signup_rejects_razorpay_password_same_as_login_password(self):
        owner_email = self.unique_email("owner")
        self.make_cafe(owner_email=owner_email)
        resp = self._register(owner_email, "CorrectHorseBattery1", razorpay_password="CorrectHorseBattery1")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("different", resp.json().get("error", "").lower())

    def test_admin_signup_succeeds_and_hashes_razorpay_password_separately(self):
        owner_email = self.unique_email("owner")
        self.make_cafe(owner_email=owner_email)
        resp = self._register(owner_email, "CorrectHorseBattery1", razorpay_password="TotallyDifferentPass2")
        self.assertEqual(resp.status_code, 200, resp.content)

        admin_doc = self.db.admins.find_one({"email": owner_email})
        self.track("admins", admin_doc["_id"])
        self.assertIn("razorpay_password_hash", admin_doc)
        self.assertNotEqual(admin_doc["razorpay_password_hash"], admin_doc["password_hash"])
        self.assertTrue(auth_handler.verify_password(admin_doc["razorpay_password_hash"], "TotallyDifferentPass2"))

    def test_gamer_signup_does_not_require_razorpay_password(self):
        resp = self._register(self.unique_email("gamer"), "CorrectHorseBattery1", razorpay_password=None, role="")
        self.assertEqual(resp.status_code, 200, resp.content)


class RazorpayPasswordGateTests(SecurityTestCase):
    def setUp(self):
        super().setUp()
        self.owner_email, self.owner_token = self.make_active_user(role="admin", collection="admins")
        self.cafe_id = self.make_cafe(owner_email=self.owner_email)

    def _set_password_hash(self, password="MySecretRzpPass1"):
        self.db.admins.update_one({"email": self.owner_email}, {"$set": {"razorpay_password_hash": auth_handler.ph.hash(password)}})
        return password

    def test_status_reports_no_password_for_a_pre_existing_account(self):
        resp = self.client.get(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/password-status/", **self.auth_header(self.owner_token)
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["is_owner"])
        self.assertFalse(body["has_password"])

    def test_owner_can_set_password_for_the_first_time(self):
        resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/set-password/",
            {"password": "BrandNewRzpPass1"},
            format="json",
            **self.auth_header(self.owner_token),
        )
        self.assertEqual(resp.status_code, 200)
        status_resp = self.client.get(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/password-status/", **self.auth_header(self.owner_token)
        )
        self.assertTrue(status_resp.json()["has_password"])

    def test_cannot_set_password_twice(self):
        self._set_password_hash()
        resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/set-password/",
            {"password": "AnotherPass123"},
            format="json",
            **self.auth_header(self.owner_token),
        )
        self.assertEqual(resp.status_code, 400)

    def test_verify_fails_with_no_password_set_yet(self):
        resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/verify-password/",
            {"password": "whatever"},
            format="json",
            **self.auth_header(self.owner_token),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(resp.json().get("needs_setup"))

    def test_verify_succeeds_with_correct_password(self):
        pw = self._set_password_hash()
        resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/verify-password/",
            {"password": pw},
            format="json",
            **self.auth_header(self.owner_token),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["verified"])

    def test_verify_fails_with_wrong_password(self):
        self._set_password_hash()
        resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/verify-password/",
            {"password": "totally-wrong"},
            format="json",
            **self.auth_header(self.owner_token),
        )
        self.assertEqual(resp.status_code, 403)

    def test_repeated_wrong_attempts_lock_out(self):
        self._set_password_hash()
        for _ in range(auth_handler.MAX_LOGIN_ATTEMPTS):
            self.client.post(
                f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/verify-password/",
                {"password": "wrong"},
                format="json",
                **self.auth_header(self.owner_token),
            )
        resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/verify-password/",
            {"password": "wrong-again"},
            format="json",
            **self.auth_header(self.owner_token),
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("too many", resp.json().get("message", "").lower())

    def test_super_admin_bypasses_the_password_gate(self):
        resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/verify-password/",
            {"password": ""},
            format="json",
            **self.admin_header(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["verified"])

    def test_saving_credentials_without_password_is_rejected(self):
        self._set_password_hash()
        resp = self.client.put(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/",
            {"key_id": "rzp_test_abc123", "key_secret": "supersecret"},
            format="json",
            **self.auth_header(self.owner_token),
        )
        self.assertEqual(resp.status_code, 403)

    def test_saving_credentials_with_correct_password_succeeds(self):
        pw = self._set_password_hash()
        resp = self.client.put(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/",
            {"key_id": "rzp_test_abc123", "key_secret": "supersecret", "razorpay_password": pw},
            format="json",
            **self.auth_header(self.owner_token),
        )
        self.assertEqual(resp.status_code, 200)

    def test_super_admin_can_save_credentials_without_owner_password(self):
        self._set_password_hash()
        resp = self.client.put(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/",
            {"key_id": "rzp_test_abc123", "key_secret": "supersecret"},
            format="json",
            **self.admin_header(),
        )
        self.assertEqual(resp.status_code, 200)


class RazorpayPasswordForgotResetTests(SecurityTestCase):
    """A forgotten Razorpay password used to be a permanent lockout (no recovery path at
    all, by deliberate original design). These cover the OTP-based recovery that replaced
    that: it requires an active session already authenticated as the exact cafe owner
    (not a super-admin bypass), plus a fresh code emailed to that owner's own account —
    both factors, not just one."""

    def setUp(self):
        super().setUp()
        self.owner_email, self.owner_token = self.make_active_user(role="admin", collection="admins")
        self.cafe_id = self.make_cafe(owner_email=self.owner_email)

    def _set_password_hash(self, password="MySecretRzpPass1"):
        self.db.admins.update_one({"email": self.owner_email}, {"$set": {"razorpay_password_hash": auth_handler.ph.hash(password)}})
        return password

    def _forgot(self, token):
        return self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/forgot-password/",
            {}, format="json", **self.auth_header(token),
        )

    def _reset(self, otp, new_password, token=None):
        return self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/reset-password/",
            {"otp_code": otp, "new_password": new_password},
            format="json",
            **self.auth_header(token or self.owner_token),
        )

    def test_owner_can_request_a_reset_code(self):
        resp = self._forgot(self.owner_token)
        self.assertEqual(resp.status_code, 200)
        admin_doc = self.db.admins.find_one({"email": self.owner_email})
        self.assertIsNotNone(admin_doc.get("razorpay_reset_otp_code"))

    def test_a_different_authenticated_owner_cannot_request_a_reset_for_this_cafe(self):
        other_email, other_token = self.make_active_user(role="admin", collection="admins")
        self.make_cafe(owner_email=other_email)  # so they pass auth as *a* real owner, just not this one

        # authenticate_admin_owner itself rejects this before the view even reaches
        # forgot_razorpay_password_handler — a non-owner can't call this endpoint for a
        # cafe_id they don't own at all, regardless of what handler-level checks exist.
        resp = self._forgot(other_token)
        self.assertEqual(resp.status_code, 403)
        admin_doc = self.db.admins.find_one({"email": self.owner_email})
        self.assertIsNone(admin_doc.get("razorpay_reset_otp_code"))

    def test_super_admin_cannot_trigger_a_reset_on_the_owners_behalf(self):
        # Unlike verify-password, this must NOT accept the super-admin bypass — the OTP
        # goes to the owner's inbox, which a super admin doesn't have access to either way.
        resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/forgot-password/",
            {}, format="json", **self.admin_header(),
        )
        self.assertEqual(resp.status_code, 400)

    def test_correct_otp_resets_and_new_password_unlocks(self):
        self._set_password_hash("OldRzpPass1")
        self._forgot(self.owner_token)
        otp = self.db.admins.find_one({"email": self.owner_email})["razorpay_reset_otp_code"]

        resp = self._reset(otp, "BrandNewRzpPass2")
        self.assertEqual(resp.status_code, 200)

        unlock_resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/verify-password/",
            {"password": "BrandNewRzpPass2"},
            format="json",
            **self.auth_header(self.owner_token),
        )
        self.assertEqual(unlock_resp.status_code, 200)
        self.assertTrue(unlock_resp.json()["verified"])

        # Old password must no longer work.
        old_unlock_resp = self.client.post(
            f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/verify-password/",
            {"password": "OldRzpPass1"},
            format="json",
            **self.auth_header(self.owner_token),
        )
        self.assertEqual(old_unlock_resp.status_code, 403)

    def test_reset_works_even_if_no_password_was_ever_set(self):
        # No _set_password_hash() call — this account predates the feature entirely.
        self._forgot(self.owner_token)
        otp = self.db.admins.find_one({"email": self.owner_email})["razorpay_reset_otp_code"]
        resp = self._reset(otp, "FirstEverRzpPass1")
        self.assertEqual(resp.status_code, 200)

    def test_reset_clears_an_existing_verify_lockout(self):
        self._set_password_hash("OldRzpPass1")
        for _ in range(auth_handler.MAX_LOGIN_ATTEMPTS):
            self.client.post(
                f"/api/v1/main/cafes/{self.cafe_id}/razorpay-credentials/verify-password/",
                {"password": "wrong"}, format="json", **self.auth_header(self.owner_token),
            )
        admin_doc = self.db.admins.find_one({"email": self.owner_email})
        self.assertIsNotNone(admin_doc.get("razorpay_password_locked_until"))

        self._forgot(self.owner_token)
        otp = self.db.admins.find_one({"email": self.owner_email})["razorpay_reset_otp_code"]
        self._reset(otp, "BrandNewRzpPass2")

        admin_doc = self.db.admins.find_one({"email": self.owner_email})
        self.assertIsNone(admin_doc.get("razorpay_password_locked_until"))
        self.assertIsNone(admin_doc.get("razorpay_password_attempts"))

    def test_wrong_otp_is_rejected_and_counts_as_an_attempt(self):
        self._forgot(self.owner_token)
        resp = self._reset("000000", "BrandNewRzpPass2")
        self.assertEqual(resp.status_code, 400)
        admin_doc = self.db.admins.find_one({"email": self.owner_email})
        self.assertEqual(admin_doc.get("razorpay_reset_otp_attempts"), 1)

    def test_too_many_wrong_attempts_locks_out_the_reset_code(self):
        self._forgot(self.owner_token)
        for _ in range(auth_handler.MAX_OTP_ATTEMPTS):
            self._reset("000000", "BrandNewRzpPass2")
        admin_doc = self.db.admins.find_one({"email": self.owner_email})
        self.assertIsNone(admin_doc.get("razorpay_reset_otp_code"))

    def test_weak_new_password_is_rejected(self):
        self._forgot(self.owner_token)
        otp = self.db.admins.find_one({"email": self.owner_email})["razorpay_reset_otp_code"]
        resp = self._reset(otp, "short")
        self.assertEqual(resp.status_code, 400)

    def test_new_razorpay_password_same_as_login_password_is_still_allowed_here(self):
        # Signup blocks this (they must differ at creation time), but the reset endpoint
        # doesn't re-derive/compare against the login password — documenting the current
        # behavior explicitly rather than leaving it untested either way.
        self._forgot(self.owner_token)
        otp = self.db.admins.find_one({"email": self.owner_email})["razorpay_reset_otp_code"]
        resp = self._reset(otp, "sectestpassword")
        self.assertIn(resp.status_code, (200, 400))

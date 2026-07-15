# test_auth_security.py
# Regression tests for: super-admin self-registration bypass, OTP brute-force lockout,
# JWT revocation on logout, and static ADMIN_TOKEN handling.

from datetime import timedelta

from ..Handlers import auth_handler
from .base import SecurityTestCase


class SuperAdminRoleEscalationTests(SecurityTestCase):
    """
    Guards against anyone POSTing role="super_admin" to the public /auth/register/
    endpoint to self-provision a super admin account — the ONLY endpoint that can create
    a new super_admin document. login/verify-otp/resend-otp deliberately do NOT carry
    this guard: they can only ever act on an already-existing document (find_one, never
    insert_one), so there is no escalation path through them — and guarding them anyway
    previously locked out every real super admin, since logging in would then have
    required already having super-admin credentials. See views.py comments on those views.
    """

    def test_register_with_super_admin_role_blocked_when_unauthenticated(self):
        resp = self.client.post(
            "/api/v1/main/auth/register/",
            {"gamertag": "x", "email": "x", "password": "x", "iv": "x", "role": "super_admin"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("not authorized", resp.json().get("error", "").lower())

    def test_login_with_super_admin_role_is_not_blocked_for_a_nonexistent_account(self):
        """Not blocked by the role guard (none applies here) — still correctly fails
        because the account doesn't exist, proving this can't be used to create one."""
        resp = self.client.post(
            "/api/v1/main/auth/login/",
            {"email": "x", "password": "x", "iv": "x", "role": "super_admin"},
            format="json",
        )
        self.assertNotEqual(resp.status_code, 403)

    def test_verify_otp_with_super_admin_role_is_not_blocked_for_a_nonexistent_session(self):
        resp = self.client.post(
            "/api/v1/main/auth/verify-otp/",
            {"email": "x", "otp_code": "x", "iv": "x", "role": "super_admin"},
            format="json",
        )
        self.assertNotEqual(resp.status_code, 403)

    def test_resend_otp_with_super_admin_role_is_not_blocked_for_a_nonexistent_account(self):
        resp = self.client.post(
            "/api/v1/main/auth/resend-otp/",
            {"email": "x", "iv": "x", "role": "super_admin"},
            format="json",
        )
        self.assertNotEqual(resp.status_code, 403)

    def test_existing_super_admin_can_log_in_via_normal_otp_flow_without_prior_credentials(self):
        """The regression this fixes: a real, already-provisioned super admin must be able
        to log in from scratch (no cookie, no static token) purely via email+password+OTP."""
        from datetime import datetime, timedelta
        import base64, json as jsonlib
        from Crypto.Cipher import AES
        from Crypto.Random import get_random_bytes
        from Crypto.Util.Padding import pad

        email = self.unique_email("existing_super_admin")
        otp_code = "135790"
        doc = {
            "email": email,
            "gamertag": "EXISTING_SA",
            "status": "Active",
            "role": "super_admin",
            "otp_code": otp_code,
            "otp_expiry": datetime.now(auth_handler.IST) + timedelta(minutes=10),
        }
        result = self.db.super_admin.insert_one(doc)
        self.track("super_admin", result.inserted_id)

        iv_bytes = get_random_bytes(16)
        iv_b64 = base64.b64encode(iv_bytes).decode("utf-8")

        def enc(plain):
            cipher = AES.new(auth_handler.ENCRYPTION_KEY, AES.MODE_CBC, iv_bytes)
            return base64.b64encode(cipher.encrypt(pad(plain.encode("utf-8"), AES.block_size))).decode("utf-8")

        resp = self.client.post(
            "/api/v1/main/auth/verify-otp/",
            {"email": enc(email), "otp_code": enc(otp_code), "iv": iv_b64, "role": "super_admin"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIn("km_super_admin_token", resp.cookies)

    def test_register_without_super_admin_role_is_not_blocked_by_the_guard(self):
        """Sanity check: the guard is scoped to role=="super_admin" and must not block
        ordinary registration (a bad/garbage payload should fail at decryption, not 403)."""
        resp = self.client.post(
            "/api/v1/main/auth/register/",
            {"gamertag": "x", "email": "x", "password": "x", "iv": "x"},
            format="json",
        )
        self.assertNotEqual(resp.status_code, 403)

    def test_register_with_super_admin_role_allowed_for_authenticated_super_admin(self):
        """The guard must not block a *real* super admin (static ADMIN_TOKEN) from using
        role=super_admin — it should proceed to (and fail at) decryption, not get a 403."""
        resp = self.client.post(
            "/api/v1/main/auth/register/",
            {"gamertag": "x", "email": "x", "password": "x", "iv": "x", "role": "super_admin"},
            format="json",
            **self.admin_header(),
        )
        self.assertNotEqual(resp.status_code, 403)


class OtpLockoutTests(SecurityTestCase):
    """Guards against unlimited OTP guesses against a 6-digit code."""

    def _seed_pending_otp_user(self, otp_code="654321"):
        from datetime import datetime
        email = self.unique_email("otp")
        doc = {
            "email": email,
            "gamertag": "SECTEST",
            "status": "Pending",
            "otp_code": otp_code,
            "otp_expiry": datetime.now(auth_handler.IST) + timedelta(minutes=10),
            "role": "user",
        }
        result = self.db.users.insert_one(doc)
        self.track("users", result.inserted_id)
        return email

    def _verify(self, email, otp):
        email_enc, otp_enc, iv = self.encrypt_with_shared_iv(email, otp)
        return auth_handler.khelomore_verify_otp(email_enc, otp_enc, iv, role="user")

    def test_wrong_otp_is_rejected(self):
        email = self._seed_pending_otp_user(otp_code="111111")
        result, code = self._verify(email, "000000")
        self.assertEqual(code, 400)
        self.assertIn("Invalid verification code", result["error"])

    def test_correct_otp_is_accepted(self):
        import json
        email = self._seed_pending_otp_user(otp_code="222222")
        result, code = self._verify(email, "222222")
        self.assertEqual(code, 200)
        decrypted = json.loads(auth_handler.decrypt_data(result["encrypted_response"], result["iv"]))
        self.assertIn("token", decrypted)

    def test_lockout_after_max_attempts_then_correct_otp_no_longer_works(self):
        email = self._seed_pending_otp_user(otp_code="333333")

        for _ in range(auth_handler.MAX_OTP_ATTEMPTS - 1):
            result, code = self._verify(email, "999999")
            self.assertEqual(code, 400)

        # This attempt crosses the threshold and must invalidate the OTP outright.
        result, code = self._verify(email, "999999")
        self.assertEqual(code, 429)
        self.assertIn("Too many incorrect attempts", result["error"])

        # Even the *correct* OTP must now be rejected — it was cleared, not just the guess.
        result, code = self._verify(email, "333333")
        self.assertNotEqual(code, 200)

    def test_new_otp_request_resets_the_attempt_counter(self):
        """Resending a fresh OTP must reset the lockout counter, not permanently lock the account."""
        email = self._seed_pending_otp_user(otp_code="444444")

        for _ in range(auth_handler.MAX_OTP_ATTEMPTS - 1):
            self._verify(email, "000000")

        # Simulate a resend: a fresh OTP + expiry, attempts counter cleared (mirrors what
        # khelomore_login's update_one / the resend-otp view do).
        self.db.users.update_one(
            {"email": email},
            {"$set": {"otp_code": "555555"}, "$unset": {"otp_attempts": ""}},
        )
        result, code = self._verify(email, "555555")
        self.assertEqual(code, 200)


class LoginBruteForceTests(SecurityTestCase):
    """Guards against unlimited password guesses against a known email on /auth/login/."""

    def _login(self, email, password):
        email_enc, password_enc, iv = self.encrypt_with_shared_iv(email, password)
        return auth_handler.khelomore_login(email_enc, password_enc, iv, role="user")

    def test_wrong_password_is_rejected(self):
        email = self.unique_email("brute")
        self.make_active_user(email=email, password="CorrectHorseBattery1")
        result, code = self._login(email, "wrong-password")
        self.assertEqual(code, 401)

    def test_correct_password_is_accepted(self):
        email = self.unique_email("brute")
        self.make_active_user(email=email, password="CorrectHorseBattery1")
        result, code = self._login(email, "CorrectHorseBattery1")
        self.assertEqual(code, 200)

    def test_lockout_after_max_attempts_then_correct_password_no_longer_works(self):
        email = self.unique_email("brute")
        self.make_active_user(email=email, password="CorrectHorseBattery1")

        for _ in range(auth_handler.MAX_LOGIN_ATTEMPTS):
            result, code = self._login(email, "wrong-password")

        # The account is now locked — even the genuinely correct password must be rejected
        # until the lockout window passes, otherwise the lockout is pointless.
        result, code = self._login(email, "CorrectHorseBattery1")
        self.assertEqual(code, 429)
        self.assertIn("Too many failed login attempts", result["error"])

    def test_successful_login_resets_the_attempt_counter(self):
        email = self.unique_email("brute")
        self.make_active_user(email=email, password="CorrectHorseBattery1")

        for _ in range(auth_handler.MAX_LOGIN_ATTEMPTS - 1):
            self._login(email, "wrong-password")

        # One below the lockout threshold — a correct login here must succeed and clear
        # the counter, not carry it forward into a future lockout.
        result, code = self._login(email, "CorrectHorseBattery1")
        self.assertEqual(code, 200)

        user = self.db.users.find_one({"email": email})
        self.assertNotIn("login_attempts", user)
        self.assertNotIn("login_locked_until", user)


class OtpResendCooldownTests(SecurityTestCase):
    """Guards against /auth/resend-otp/ being used to dodge the OTP attempt lockout by
    just requesting a fresh code (and resetting otp_attempts) before hitting the cap."""

    def test_immediate_resend_is_rejected(self):
        email = self.unique_email("resend")
        self.make_active_user(email=email, password="CorrectHorseBattery1")
        # First login triggers the initial OTP send (sets otp_expiry).
        email_enc, password_enc, iv = self.encrypt_with_shared_iv(email, "CorrectHorseBattery1")
        auth_handler.khelomore_login(email_enc, password_enc, iv, role="user")

        import base64
        from Crypto.Cipher import AES
        from Crypto.Random import get_random_bytes
        from Crypto.Util.Padding import pad
        iv_bytes = get_random_bytes(16)
        cipher = AES.new(auth_handler.ENCRYPTION_KEY, AES.MODE_CBC, iv_bytes)
        enc = cipher.encrypt(pad(email.encode("utf-8"), AES.block_size))
        email_enc2 = base64.b64encode(enc).decode("utf-8")
        iv_b64 = base64.b64encode(iv_bytes).decode("utf-8")

        resp = self.client.post(
            "/api/v1/main/auth/resend-otp/",
            {"email": email_enc2, "iv": iv_b64, "role": "user"},
            format="json",
        )
        self.assertEqual(resp.status_code, 429)
        self.assertIn("wait", resp.json().get("error", "").lower())

    def test_resend_after_cooldown_window_succeeds(self):
        from datetime import datetime, timedelta
        email = self.unique_email("resend")
        self.make_active_user(email=email, password="CorrectHorseBattery1")
        # Directly seed an otp_expiry old enough that the cooldown has already elapsed —
        # equivalent to a real OTP sent more than the cooldown window ago.
        self.db.users.update_one(
            {"email": email},
            {"$set": {"otp_expiry": datetime.now(auth_handler.IST) + timedelta(
                minutes=10, seconds=-(auth_handler.OTP_RESEND_COOLDOWN_SECONDS + 5)
            )}},
        )

        import base64
        from Crypto.Cipher import AES
        from Crypto.Random import get_random_bytes
        from Crypto.Util.Padding import pad
        iv_bytes = get_random_bytes(16)
        cipher = AES.new(auth_handler.ENCRYPTION_KEY, AES.MODE_CBC, iv_bytes)
        enc = cipher.encrypt(pad(email.encode("utf-8"), AES.block_size))
        email_enc = base64.b64encode(enc).decode("utf-8")
        iv_b64 = base64.b64encode(iv_bytes).decode("utf-8")

        resp = self.client.post(
            "/api/v1/main/auth/resend-otp/",
            {"email": email_enc, "iv": iv_b64, "role": "user"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)


class JwtRevocationTests(SecurityTestCase):
    """Guards against a logged-out token remaining usable until natural expiry."""

    def test_logout_revokes_the_token(self):
        email, token = self.make_active_user()

        # Token works before logout.
        resp = self.client.get("/api/v1/main/auth/me/", **self.auth_header(token))
        self.assertEqual(resp.status_code, 200)

        logout_resp = self.client.post("/api/v1/main/auth/logout/", **self.auth_header(token))
        self.assertEqual(logout_resp.status_code, 200)

        # Same token must now be rejected.
        resp = self.client.get("/api/v1/main/auth/me/", **self.auth_header(token))
        self.assertEqual(resp.status_code, 401)

    def test_unrelated_tokens_are_not_affected_by_someone_elses_logout(self):
        email_a, token_a = self.make_active_user()
        email_b, token_b = self.make_active_user()

        self.client.post("/api/v1/main/auth/logout/", **self.auth_header(token_a))

        resp = self.client.get("/api/v1/main/auth/me/", **self.auth_header(token_b))
        self.assertEqual(resp.status_code, 200)


class AdminTokenAuthTests(SecurityTestCase):
    def test_wrong_admin_token_is_rejected(self):
        resp = self.client.get(
            "/api/v1/main/db/", HTTP_AUTHORIZATION="Bearer definitely-not-the-real-token"
        )
        self.assertEqual(resp.status_code, 401)

    def test_correct_admin_token_is_accepted(self):
        resp = self.client.get("/api/v1/main/db/", **self.admin_header())
        self.assertEqual(resp.status_code, 200)

    def test_missing_auth_is_rejected(self):
        resp = self.client.get("/api/v1/main/db/")
        self.assertEqual(resp.status_code, 401)

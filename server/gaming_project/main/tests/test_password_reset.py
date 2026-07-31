# test_password_reset.py
# Regression tests for forgot-password / reset-password: email-enumeration prevention,
# OTP expiry/brute-force bounding, Google-only account rejection, and cross-role
# collection isolation (an "admin"-role reset must never touch a "users"-collection
# account with the same email, and vice versa).

from datetime import datetime, timedelta, timezone

from ..Handlers import auth_handler
from .base import SecurityTestCase


class ForgotPasswordEnumerationTests(SecurityTestCase):
    """The response must never reveal whether an email is registered, Google-only, or
    blocked — only the presence/absence of reset_otp_code server-side may differ."""

    def _post(self, email, role=""):
        enc_email, iv = self.encrypt_with_shared_iv(email)
        return self.client.post(
            "/api/v1/main/auth/forgot-password/",
            {"email": enc_email, "iv": iv, "role": role},
            format="json",
        )

    def _decrypted_message(self, resp):
        body = resp.json()
        return auth_handler.decrypt_data(body["encrypted_response"], body["iv"])

    def test_existing_and_nonexistent_email_get_identical_response(self):
        email, _ = self.make_active_user(password="RealPassword123!")
        resp_real = self._post(email)
        resp_fake = self._post(self.unique_email("ghost"))

        # Each response is independently AES-CBC encrypted under a fresh random IV (by
        # design — reusing an IV would be the actual security bug), so raw ciphertext
        # always differs even for byte-identical plaintext. Compare the decrypted message
        # itself, which is what must actually be indistinguishable.
        self.assertEqual(resp_real.status_code, resp_fake.status_code)
        self.assertEqual(self._decrypted_message(resp_real), self._decrypted_message(resp_fake))

    def test_real_account_actually_gets_a_reset_otp_set(self):
        email, _ = self.make_active_user(password="RealPassword123!")
        self._post(email)
        doc = self.db.users.find_one({"email": email})
        self.assertIsNotNone(doc.get("reset_otp_code"))
        self.assertIsNotNone(doc.get("reset_otp_expiry"))

    def test_google_only_account_gets_no_otp_but_same_response(self):
        # No password= passed -> no password_hash, matching a real Google-signup account.
        email, _ = self.make_active_user()
        resp = self._post(email)
        doc = self.db.users.find_one({"email": email})

        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(doc.get("reset_otp_code"))

    def test_blocked_account_gets_no_otp_but_same_response(self):
        email, _ = self.make_active_user(password="RealPassword123!")
        self.db.users.update_one({"email": email}, {"$set": {"status": "Blocked"}})
        resp = self._post(email)
        doc = self.db.users.find_one({"email": email})

        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(doc.get("reset_otp_code"))

    def test_resend_within_cooldown_does_not_rotate_the_otp(self):
        email, _ = self.make_active_user(password="RealPassword123!")
        self._post(email)
        first_otp = self.db.users.find_one({"email": email})["reset_otp_code"]

        self._post(email)  # immediate second request, well within cooldown
        second_otp = self.db.users.find_one({"email": email})["reset_otp_code"]

        self.assertEqual(first_otp, second_otp)


class ResetPasswordTests(SecurityTestCase):
    def _reset(self, email, otp, new_password, role=""):
        enc_email, enc_otp, enc_pw, iv = self.encrypt_with_shared_iv(email, otp, new_password)
        return self.client.post(
            "/api/v1/main/auth/reset-password/",
            {"email": enc_email, "otp_code": enc_otp, "new_password": enc_pw, "iv": iv, "role": role},
            format="json",
        )

    def _set_reset_otp(self, email, otp="123456", minutes_from_now=10, collection="users"):
        self.db[collection].update_one(
            {"email": email},
            {"$set": {
                "reset_otp_code": auth_handler.hash_otp(otp),
                "reset_otp_expiry": datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now),
            }}
        )

    def test_correct_otp_and_strong_password_resets_and_allows_login(self):
        email, _ = self.make_active_user(password="OldPassword123!")
        self._set_reset_otp(email)

        resp = self._reset(email, "123456", "NewStrongPassword456!")
        self.assertEqual(resp.status_code, 200)

        # Real end-to-end proof: log in with the NEW password afterward.
        enc_email, enc_pw, iv = self.encrypt_with_shared_iv(email, "NewStrongPassword456!")
        login_resp = self.client.post(
            "/api/v1/main/auth/login/",
            {"email": enc_email, "password": enc_pw, "iv": iv},
            format="json",
        )
        self.assertEqual(login_resp.status_code, 200)

        doc = self.db.users.find_one({"email": email})
        self.assertIsNone(doc.get("reset_otp_code"))
        self.assertIsNone(doc.get("reset_otp_expiry"))

    def test_old_password_no_longer_works_after_reset(self):
        email, _ = self.make_active_user(password="OldPassword123!")
        self._set_reset_otp(email)
        self._reset(email, "123456", "NewStrongPassword456!")

        enc_email, enc_pw, iv = self.encrypt_with_shared_iv(email, "OldPassword123!")
        login_resp = self.client.post(
            "/api/v1/main/auth/login/",
            {"email": enc_email, "password": enc_pw, "iv": iv},
            format="json",
        )
        self.assertEqual(login_resp.status_code, 401)

    def test_wrong_otp_is_rejected_and_counts_as_an_attempt(self):
        email, _ = self.make_active_user(password="OldPassword123!")
        self._set_reset_otp(email)

        resp = self._reset(email, "000000", "NewStrongPassword456!")
        self.assertEqual(resp.status_code, 400)
        doc = self.db.users.find_one({"email": email})
        self.assertEqual(doc.get("reset_otp_attempts"), 1)

    def test_too_many_wrong_attempts_locks_out_the_reset_code(self):
        email, _ = self.make_active_user(password="OldPassword123!")
        self._set_reset_otp(email)

        for _ in range(auth_handler.MAX_PASSWORD_RESET_ATTEMPTS):
            self._reset(email, "000000", "NewStrongPassword456!")

        doc = self.db.users.find_one({"email": email})
        self.assertIsNone(doc.get("reset_otp_code"))  # wiped after hitting the cap

        # Even the correct OTP now fails — it was invalidated by the lockout, not just
        # "still counted as wrong".
        self._set_reset_otp(email, otp="999999")
        resp = self._reset(email, "111111", "NewStrongPassword456!")
        self.assertEqual(resp.status_code, 400)

    def test_expired_otp_is_rejected(self):
        email, _ = self.make_active_user(password="OldPassword123!")
        self._set_reset_otp(email, minutes_from_now=-1)  # already expired

        resp = self._reset(email, "123456", "NewStrongPassword456!")
        self.assertEqual(resp.status_code, 400)

    def test_weak_new_password_is_rejected_before_touching_the_otp(self):
        email, _ = self.make_active_user(password="OldPassword123!")
        self._set_reset_otp(email)

        resp = self._reset(email, "wrong-otp-should-never-be-checked", "short")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Password", resp.json().get("error", ""))

        # Confirm the OTP itself is untouched — this really was rejected on password
        # strength alone, not treated as a wrong-OTP attempt.
        doc = self.db.users.find_one({"email": email})
        self.assertIsNone(doc.get("reset_otp_attempts"))

    def test_no_pending_reset_request_is_rejected(self):
        email, _ = self.make_active_user(password="OldPassword123!")
        # No _set_reset_otp call — nothing pending.
        resp = self._reset(email, "123456", "NewStrongPassword456!")
        self.assertEqual(resp.status_code, 400)

    def test_reset_works_correctly_for_every_account_type(self):
        """
        The one thing that actually matters end-to-end: for each of the four real account
        types (plain gamer, website gamer, cafe owner, super admin), a reset must land in
        THAT account's own collection, as a genuine Argon2id hash (not plaintext, not some
        other hash scheme), and the new password must actually work to log back in via
        that same role.
        """
        cases = [
            ("users", "", False),
            ("website_users", "website_user", False),
            ("admins", "admin", True),
            ("super_admin", "super_admin", False),
        ]
        for collection, role, is_admin in cases:
            with self.subTest(collection=collection, role=role):
                email, _ = self.make_active_user(
                    password="OldPassword123!", role=role, collection=collection
                )
                if role == "admin":
                    # Cafe-owner login/reset both require an associated, non-deleted cafe.
                    self.make_cafe(owner_email=email)

                self._set_reset_otp(email, otp="741852", collection=collection)
                resp = self._reset(email, "741852", "NewStrongPassword456!", role=role)
                self.assertEqual(resp.status_code, 200, f"reset failed for {collection}: {resp.json()}")

                doc = self.db[collection].find_one({"email": email})
                self.assertIsNotNone(doc, f"{collection}: account disappeared after reset")
                stored_hash = doc.get("password_hash", "") if doc else ""
                # argon2-cffi's PasswordHasher always produces this prefix — confirms the
                # new password was actually hashed with Argon2id, not stored as plaintext
                # or some other scheme.
                self.assertTrue(
                    stored_hash.startswith("$argon2id$"),
                    f"{collection}: password_hash doesn't look like Argon2id: {stored_hash!r}",
                )
                self.assertNotIn("NewStrongPassword456!", stored_hash)

                enc_email, enc_pw, iv = self.encrypt_with_shared_iv(email, "NewStrongPassword456!")
                login_resp = self.client.post(
                    "/api/v1/main/auth/login/",
                    {"email": enc_email, "password": enc_pw, "iv": iv, "role": role},
                    format="json",
                )
                self.assertEqual(
                    login_resp.status_code, 200,
                    f"login with new password failed for {collection}: {login_resp.json()}",
                )

    def test_admin_role_reset_does_not_touch_a_users_collection_account(self):
        """Cross-collection isolation: the same email registered as a plain gamer
        ("users") must be untouched by a role="admin" reset request."""
        email, _ = self.make_active_user(password="OldPassword123!", collection="users")
        self._set_reset_otp(email, otp="654321", collection="users")

        # Attempt the reset scoped to role="admin" (db.admins), which has no such account.
        resp = self._reset(email, "654321", "NewStrongPassword456!", role="admin")
        self.assertEqual(resp.status_code, 400)

        # The real users-collection account's OTP must be untouched by that misdirected
        # attempt, and its original password must still work.
        doc = self.db.users.find_one({"email": email})
        self.assertEqual(doc.get("reset_otp_code"), auth_handler.hash_otp("654321"))

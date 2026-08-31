# test_refresh_tokens.py
# Regression tests for the access+refresh token upgrade: verify-otp/Google login now issue
# a refresh token alongside the (now short-lived, for non-gamer buckets) access token,
# /auth/refresh/ rotates it, reuse of an already-rotated token revokes the whole family,
# cross-bucket confusion is rejected, and logout revokes the refresh family too — not just
# the access token. Runs against the real MongoDB test database, same as every other file
# in this suite (see base.py).

import json as jsonlib
from datetime import datetime, timedelta, timezone

from ..Handlers import auth_handler
from .base import SecurityTestCase


class VerifyOtpIssuesRefreshTokenTests(SecurityTestCase):
    def _make_pending_gamer(self, otp_code="246810"):
        email = self.unique_email("refresh_gamer")
        doc = {
            "email": email,
            "gamertag": "REFRESHTEST",
            "status": "Pending",
            "role": "user",
            "otp_code": auth_handler.hash_otp(otp_code),
            "otp_expiry": datetime.now(auth_handler.IST) + timedelta(minutes=10),
        }
        result = self.db.users.insert_one(doc)
        self.track("users", result.inserted_id)
        return email

    def test_verify_otp_response_includes_refresh_token_and_sets_both_cookies(self):
        email = self._make_pending_gamer()
        enc_email, enc_otp, iv_b64 = self.encrypt_with_shared_iv(email, "246810")

        resp = self.client.post(
            "/api/v1/main/auth/verify-otp/",
            {"email": enc_email, "otp_code": enc_otp, "iv": iv_b64, "role": "user"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIn("bmc_gamer_token", resp.cookies)
        self.assertIn("bmc_gamer_refresh", resp.cookies)
        self.assertEqual(resp.cookies["bmc_gamer_refresh"]["path"], "/api/v1/main/auth/refresh/")

        body = resp.json()
        plain = auth_handler.decrypt_data(body["encrypted_response"], body["iv"])
        parsed = jsonlib.loads(plain)
        self.assertTrue(parsed.get("token"))
        self.assertTrue(parsed.get("refresh_token"))

    def test_admin_bucket_gets_a_short_lived_access_token_now(self):
        """The core behavior change: non-gamer buckets no longer get a 30-day JWT directly
        — they get a short ACCESS_TOKEN_EXP_SECONDS one, backed by the new refresh token."""
        owner_email, _ = self.make_active_user(role="admin")
        access_token = auth_handler.generate_token(owner_email, is_admin=True, role="admin")
        decoded = auth_handler.jwt.decode(access_token, auth_handler.JWT_SECRET, algorithms=[auth_handler.JWT_ALGORITHM])
        lifetime = decoded["exp"] - datetime.now(auth_handler.IST).timestamp()
        self.assertLess(lifetime, auth_handler.JWT_ADMIN_EXP_DELTA_SECONDS)
        self.assertLessEqual(lifetime, auth_handler.ACCESS_TOKEN_EXP_SECONDS + 5)

    def test_gamer_bucket_keeps_its_original_long_lived_access_token(self):
        """Deliberately unchanged for now — see the ACCESS_TOKEN_EXP_SECONDS comment in
        auth_handler.py: the shipped mobile app has no refresh support yet."""
        email, _ = self.make_active_user(role="user")
        access_token = auth_handler.generate_token(email, is_admin=False, role="user")
        decoded = auth_handler.jwt.decode(access_token, auth_handler.JWT_SECRET, algorithms=[auth_handler.JWT_ALGORITHM])
        lifetime = decoded["exp"] - datetime.now(auth_handler.IST).timestamp()
        self.assertGreater(lifetime, auth_handler.ACCESS_TOKEN_EXP_SECONDS * 10)


class RefreshRotationTests(SecurityTestCase):
    def test_rotation_issues_a_new_access_and_refresh_token(self):
        email, _ = self.make_active_user(role="admin")
        access1, refresh1 = auth_handler.issue_token_pair(email, is_admin=True, role="admin")

        result = auth_handler.refresh_access_token(refresh1, expected_bucket="admin")
        self.assertIsNotNone(result)
        access2, refresh2, bucket = result
        self.assertEqual(bucket, "admin")
        self.assertNotEqual(access1, access2)
        self.assertNotEqual(refresh1, refresh2)

    def test_reusing_an_already_rotated_refresh_token_revokes_the_whole_family(self):
        email, _ = self.make_active_user(role="admin")
        _, refresh1 = auth_handler.issue_token_pair(email, is_admin=True, role="admin")

        result2 = auth_handler.refresh_access_token(refresh1, expected_bucket="admin")
        self.assertIsNotNone(result2)
        _, refresh2, _ = result2

        # Reuse of the now-spent refresh1 must fail...
        reuse_result = auth_handler.refresh_access_token(refresh1, expected_bucket="admin")
        self.assertIsNone(reuse_result)

        # ...AND must have revoked the whole family, including the still-fresh refresh2.
        result3 = auth_handler.refresh_access_token(refresh2, expected_bucket="admin")
        self.assertIsNone(result3)

    def test_cross_bucket_refresh_is_rejected(self):
        email, _ = self.make_active_user(role="admin")
        _, refresh_token = auth_handler.issue_token_pair(email, is_admin=True, role="admin")

        result = auth_handler.refresh_access_token(refresh_token, expected_bucket="super_admin")
        self.assertIsNone(result)

        # ...but works fine for its real bucket.
        result_ok = auth_handler.refresh_access_token(refresh_token, expected_bucket="admin")
        self.assertIsNotNone(result_ok)

    def test_expired_refresh_token_is_rejected(self):
        email, _ = self.make_active_user(role="user")
        _, refresh_token = auth_handler.issue_token_pair(email, is_admin=False, role="user")

        token_hash = auth_handler._hash_refresh_token(refresh_token)
        self.db.refresh_tokens.update_one(
            {"token_hash": token_hash},
            {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=10)}},
        )
        self.track("refresh_tokens", self.db.refresh_tokens.find_one({"token_hash": token_hash})["_id"])

        result = auth_handler.refresh_access_token(refresh_token, expected_bucket="gamer")
        self.assertIsNone(result)

    def test_unknown_refresh_token_is_rejected(self):
        self.assertIsNone(auth_handler.refresh_access_token("not-a-real-token", expected_bucket=None))

    def test_empty_refresh_token_is_rejected(self):
        self.assertIsNone(auth_handler.refresh_access_token("", expected_bucket=None))


class RefreshEndpointHttpTests(SecurityTestCase):
    def _make_pending_gamer(self, otp_code="112233"):
        email = self.unique_email("refresh_http_gamer")
        doc = {
            "email": email,
            "gamertag": "REFRESHHTTP",
            "status": "Pending",
            "role": "user",
            "otp_code": auth_handler.hash_otp(otp_code),
            "otp_expiry": datetime.now(auth_handler.IST) + timedelta(minutes=10),
        }
        result = self.db.users.insert_one(doc)
        self.track("users", result.inserted_id)
        return email

    def test_refresh_via_cookie_after_verify_otp(self):
        """Web-client flow: verify-otp sets cookies, and the test client's cookie jar
        automatically carries them into the next request — exactly like a browser."""
        email = self._make_pending_gamer()
        enc_email, enc_otp, iv_b64 = self.encrypt_with_shared_iv(email, "112233")
        verify_resp = self.client.post(
            "/api/v1/main/auth/verify-otp/",
            {"email": enc_email, "otp_code": enc_otp, "iv": iv_b64, "role": "user"},
            format="json",
        )
        self.assertEqual(verify_resp.status_code, 200, verify_resp.content)

        refresh_resp = self.client.post("/api/v1/main/auth/refresh/", {}, format="json")
        self.assertEqual(refresh_resp.status_code, 200, refresh_resp.content)
        self.assertTrue(refresh_resp.json().get("token"))
        self.assertIn("bmc_gamer_token", refresh_resp.cookies)
        self.assertIn("bmc_gamer_refresh", refresh_resp.cookies)

    def test_refresh_via_body_for_mobile_style_client(self):
        """Mobile-style flow: no cookies at all, refresh_token passed explicitly in the
        POST body — must still work, since the shipped mobile app can't rely on cookies."""
        email, _ = self.make_active_user(role="admin")
        _, refresh_token = auth_handler.issue_token_pair(email, is_admin=True, role="admin")

        resp = self.client.post(
            "/api/v1/main/auth/refresh/",
            {"refresh_token": refresh_token},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json().get("token"))
        self.assertTrue(resp.json().get("refresh_token"))

    def test_refresh_with_invalid_token_returns_401_and_clears_cookies(self):
        resp = self.client.post(
            "/api/v1/main/auth/refresh/",
            {"refresh_token": "garbage-not-a-real-token"},
            format="json",
        )
        self.assertEqual(resp.status_code, 401)
        self.assertIn("bmc_gamer_token", resp.cookies)
        self.assertEqual(resp.cookies["bmc_gamer_token"].value, "")

    def test_logout_revokes_refresh_family_via_cookie(self):
        email = self._make_pending_gamer("998877")
        enc_email, enc_otp, iv_b64 = self.encrypt_with_shared_iv(email, "998877")
        verify_resp = self.client.post(
            "/api/v1/main/auth/verify-otp/",
            {"email": enc_email, "otp_code": enc_otp, "iv": iv_b64, "role": "user"},
            format="json",
        )
        self.assertEqual(verify_resp.status_code, 200, verify_resp.content)
        body = verify_resp.json()
        plain = auth_handler.decrypt_data(body["encrypted_response"], body["iv"])
        parsed = jsonlib.loads(plain)
        access_token = parsed["token"]
        raw_refresh_before_logout = parsed["refresh_token"]

        logout_resp = self.client.post("/api/v1/main/auth/logout/", **self.auth_header(access_token))
        self.assertEqual(logout_resp.status_code, 200)

        # Proves actual server-side revocation (not just that the cookie jar got cleared
        # client-side) — the exact raw refresh token issued at verify-otp must now fail.
        result = auth_handler.refresh_access_token(raw_refresh_before_logout, expected_bucket="gamer")
        self.assertIsNone(result)

    def test_logout_revokes_refresh_family_via_body_for_mobile(self):
        email, _ = self.make_active_user(role="user")
        access_token, refresh_token = auth_handler.issue_token_pair(email, is_admin=False, role="user")

        logout_resp = self.client.post(
            "/api/v1/main/auth/logout/",
            {"refresh_token": refresh_token},
            format="json",
            **self.auth_header(access_token),
        )
        self.assertEqual(logout_resp.status_code, 200)

        result = auth_handler.refresh_access_token(refresh_token, expected_bucket="gamer")
        self.assertIsNone(result)

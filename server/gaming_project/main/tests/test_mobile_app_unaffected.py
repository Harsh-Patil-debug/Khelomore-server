# test_mobile_app_unaffected.py
# Confirms the access+refresh upgrade does NOT change behavior for the shipped mobile app:
# it sends no `role` field and no ADMIN_TOKEN header on /auth/verify-otp/ (see
# khelomore-cyberpunk-hub/mobile/src/lib/auth-store.ts's apiPost calls), which must still
# land in the "gamer" bucket and receive the ORIGINAL long-lived access token — not the
# new short-lived one. Also confirms the response shape stays backward compatible: the
# pre-existing `token`/`user` fields are unchanged, refresh_token is purely additive, so an
# already-installed app build that has never heard of `refresh_token` keeps working exactly
# as before (it just ignores the extra field).

import json as jsonlib
from datetime import datetime, timedelta

from ..Handlers import auth_handler
from .base import SecurityTestCase


class MobileAppExactRequestShapeTests(SecurityTestCase):
    def test_verify_otp_with_no_role_field_lands_in_gamer_bucket_with_long_lived_token(self):
        """Mirrors mobile/src/lib/auth-store.ts's real verifyOTP() call: body is exactly
        { email, otp_code, iv } — no role key at all, no Authorization header."""
        email = self.unique_email("mobile_compat")
        otp_code = "554433"
        doc = {
            "email": email,
            "gamertag": "MOBILECOMPAT",
            "status": "Pending",
            "role": "user",
            "otp_code": auth_handler.hash_otp(otp_code),
            "otp_expiry": datetime.now(auth_handler.IST) + timedelta(minutes=10),
        }
        result = self.db.users.insert_one(doc)
        self.track("users", result.inserted_id)

        enc_email, enc_otp, iv_b64 = self.encrypt_with_shared_iv(email, otp_code)
        # Deliberately NO 'role' key and NO Authorization header — this is exactly what
        # the real mobile app sends today.
        resp = self.client.post(
            "/api/v1/main/auth/verify-otp/",
            {"email": enc_email, "otp_code": enc_otp, "iv": iv_b64},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        body = resp.json()
        plain = auth_handler.decrypt_data(body["encrypted_response"], body["iv"])
        parsed = jsonlib.loads(plain)

        # Backward-compatible response shape: the fields an old app build already reads.
        self.assertIn("token", parsed)
        self.assertIn("user", parsed)
        self.assertIn("id", parsed["user"])
        self.assertIn("email", parsed["user"])

        # The actual guarantee: still the ORIGINAL long-lived access token, not the new
        # short one — an already-installed app build with zero refresh-token support must
        # see IDENTICAL session-length behavior to before this change shipped.
        decoded = auth_handler.jwt.decode(parsed["token"], auth_handler.JWT_SECRET, algorithms=[auth_handler.JWT_ALGORITHM])
        lifetime_seconds = decoded["exp"] - datetime.now(auth_handler.IST).timestamp()
        self.assertGreater(
            lifetime_seconds, auth_handler.ACCESS_TOKEN_EXP_SECONDS * 10,
            "mobile app's default (no-role) login must NOT receive the new short-lived access token",
        )
        self.assertGreater(lifetime_seconds, 29 * 24 * 3600, "must still be ~30 days, matching pre-upgrade behavior")

        # Cookie set is the gamer one — confirms bucket classification is unaffected too.
        self.assertIn("bmc_gamer_token", resp.cookies)

    def test_old_style_pre_upgrade_token_still_verifies_fine(self):
        """A token minted with the OLD formula (before this session's change) — i.e. any
        session a real user was already holding at the moment this deploy went out — must
        keep working exactly as before. verify_token()'s logic is completely unchanged by
        this upgrade; this proves that empirically rather than by inspection alone."""
        import jwt as pyjwt
        import uuid as uuidlib
        email = self.unique_email("pre_upgrade_session")
        old_style_payload = {
            "email": email,
            "jti": uuidlib.uuid4().hex,
            "exp": datetime.now(auth_handler.IST) + timedelta(days=29),  # as if issued under the old 30-day formula
        }
        old_token = pyjwt.encode(old_style_payload, auth_handler.JWT_SECRET, auth_handler.JWT_ALGORITHM)

        verified_email = auth_handler.verify_token(old_token)
        self.assertEqual(verified_email, email)

        resp = self.client.get("/api/v1/main/auth/me/", **self.auth_header(old_token))
        # 404 (account doesn't actually exist) is fine here — the point is it's NOT 401
        # "invalid/expired token", proving the token itself is still accepted as valid.
        self.assertNotEqual(resp.status_code, 401)

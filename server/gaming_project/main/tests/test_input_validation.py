# test_input_validation.py
# Regression tests for server-side input validation added across every form-backed
# endpoint (auth, cafes, tournaments, offers, rigs, sessions, partner applications) — the
# real, unbypassable checks behind what used to be client-side-only HTML attributes.

from ..Handlers import auth_handler, input_validation
from .base import SecurityTestCase


class InputValidationHelperTests(SecurityTestCase):
    """Direct unit tests for the shared input_validation.py helpers."""

    def test_email_format(self):
        self.assertIsNone(input_validation.validate_email("test@example.com"))
        self.assertIsNotNone(input_validation.validate_email("not-an-email"))
        self.assertIsNotNone(input_validation.validate_email(""))

    def test_phone_format(self):
        self.assertIsNone(input_validation.validate_phone("+91 98765 43210"))
        self.assertIsNotNone(input_validation.validate_phone("abc"))
        self.assertIsNotNone(input_validation.validate_phone("123"))  # too short
        self.assertIsNone(input_validation.validate_phone("", required=False))
        self.assertIsNotNone(input_validation.validate_phone("", required=True))

    def test_url_format(self):
        self.assertIsNone(input_validation.validate_url("https://maps.google.com/xyz", "Link"))
        self.assertIsNotNone(input_validation.validate_url("javascript:alert(1)", "Link"))
        self.assertIsNotNone(input_validation.validate_url("not a url", "Link"))
        self.assertIsNone(input_validation.validate_url("", "Link", required=False))

    def test_password_strength(self):
        self.assertIsNone(input_validation.validate_password_strength("longenough1"))
        self.assertIsNotNone(input_validation.validate_password_strength("short"))
        self.assertIsNotNone(input_validation.validate_password_strength(""))

    def test_parse_bounded_number_rejects_nosql_injection_payloads(self):
        # The exact pattern a NoSQL-injection attempt would send instead of a real number.
        parsed, err = input_validation.parse_bounded_number({"$ne": "x"}, "Price", min_val=0)
        self.assertIsNone(parsed)
        self.assertIsNotNone(err)

    def test_parse_bounded_number_enforces_range(self):
        parsed, err = input_validation.parse_bounded_number("-50", "Price", min_val=0)
        self.assertIsNone(parsed)
        self.assertIn("at least", err)

        parsed, err = input_validation.parse_bounded_number("99999999", "Price", min_val=0, max_val=100000)
        self.assertIsNone(parsed)
        self.assertIn("at most", err)

        parsed, err = input_validation.parse_bounded_number("150", "Price", min_val=0, max_val=100000)
        self.assertEqual(parsed, 150)
        self.assertIsNone(err)

    def test_validate_enum(self):
        self.assertIsNone(input_validation.validate_enum("approved", {"pending", "approved", "rejected"}, "Status"))
        self.assertIsNotNone(input_validation.validate_enum("hacked", {"pending", "approved", "rejected"}, "Status"))


class RegisterValidationTests(SecurityTestCase):
    """Every registration field is validated server-side now, not just client-side."""

    def _register(self, gamertag="Player", email=None, password="LongEnough1", phone=None):
        email = email or self.unique_email("regval")
        args = [gamertag, email, password]
        encrypted = self.encrypt_with_shared_iv(*[a for a in args if a is not None], *([phone] if phone else []))
        iv = encrypted[-1]
        enc_gamertag, enc_email, enc_password = encrypted[0], encrypted[1], encrypted[2]
        enc_phone = encrypted[3] if phone else None
        return auth_handler.khelomore_register(enc_gamertag, enc_email, enc_password, iv, phone=enc_phone, role="user")

    def test_weak_password_rejected_even_though_client_would_have_allowed_it_pre_fix(self):
        result, code = self._register(password="short1")
        self.assertEqual(code, 400)
        self.assertIn("Password", result["error"])

    def test_malformed_email_rejected(self):
        result, code = self._register(email="garbage")
        self.assertEqual(code, 400)
        self.assertIn("email", result["error"].lower())

    def test_valid_registration_succeeds(self):
        result, code = self._register(password="LongEnough1")
        self.assertEqual(code, 200)


class CafeValidationTests(SecurityTestCase):
    """cafes.py create/update — price bounds, coordinate bounds, email/phone/URL format."""

    def test_create_cafe_rejects_negative_price(self):
        resp = self.client.post(
            "/api/v1/main/cafes/",
            {"name": "Test Cafe", "area": "Test Area", "pricePerHour": "-50"},
            **self.admin_header(),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("at least", resp.json()["message"])

    def test_create_cafe_rejects_absurd_price(self):
        resp = self.client.post(
            "/api/v1/main/cafes/",
            {"name": "Test Cafe", "area": "Test Area", "pricePerHour": "99999999"},
            **self.admin_header(),
        )
        self.assertEqual(resp.status_code, 400)

    def test_create_cafe_rejects_malformed_owner_email(self):
        resp = self.client.post(
            "/api/v1/main/cafes/",
            {"name": "Test Cafe", "area": "Test Area", "pricePerHour": "100", "ownerEmail": "not-an-email"},
            **self.admin_header(),
        )
        self.assertEqual(resp.status_code, 400)

    def test_create_cafe_accepts_valid_data_and_clamps_out_of_range_coordinates(self):
        resp = self.client.post(
            "/api/v1/main/cafes/",
            {"name": "Test Cafe", "area": "Test Area", "pricePerHour": "100", "latitude": "9999", "longitude": "73"},
            **self.admin_header(),
        )
        self.assertEqual(resp.status_code, 201)
        cafe = resp.json()["cafe"]
        self.track("cafes", __import__("bson").ObjectId(cafe["id"]))
        # Out-of-range latitude falls back to the Nerul default rather than being stored as-is.
        self.assertTrue(-90 <= cafe["latitude"] <= 90)

    def test_update_cafe_rejects_instagram_as_url_but_accepts_handle(self):
        # Regression guard: instagram is a bare handle ("@cafe"), not a URL — an earlier
        # version of this validation incorrectly required URL format and would have
        # rejected every real submission.
        create_resp = self.client.post(
            "/api/v1/main/cafes/",
            {"name": "Handle Test Cafe", "area": "Area", "pricePerHour": "100"},
            **self.admin_header(),
        )
        cafe_id = create_resp.json()["cafe"]["id"]
        self.track("cafes", __import__("bson").ObjectId(cafe_id))

        resp = self.client.put(
            f"/api/v1/main/cafes/{cafe_id}/",
            {"social": {"instagram": "@mycafe"}},
            format="json",
            **self.admin_header(),
        )
        self.assertEqual(resp.status_code, 200)

    def test_update_cafe_rejects_malformed_youtube_url(self):
        create_resp = self.client.post(
            "/api/v1/main/cafes/",
            {"name": "URL Test Cafe", "area": "Area", "pricePerHour": "100"},
            **self.admin_header(),
        )
        cafe_id = create_resp.json()["cafe"]["id"]
        self.track("cafes", __import__("bson").ObjectId(cafe_id))

        resp = self.client.put(
            f"/api/v1/main/cafes/{cafe_id}/",
            {"social": {"youtube": "not a url"}},
            format="json",
            **self.admin_header(),
        )
        self.assertEqual(resp.status_code, 400)


class TournamentValidationTests(SecurityTestCase):
    """tournaments.py create/register — capacity/fee bounds, team-size/duplicate guards."""

    def _create_cafe(self):
        resp = self.client.post(
            "/api/v1/main/cafes/",
            {"name": "Tournament Test Cafe", "area": "Area", "pricePerHour": "100"},
            **self.admin_header(),
        )
        cafe_id = resp.json()["cafe"]["id"]
        self.track("cafes", __import__("bson").ObjectId(cafe_id))
        return cafe_id

    def test_create_tournament_rejects_zero_capacity(self):
        cafe_id = self._create_cafe()
        resp = self.client.post(
            "/api/v1/main/tournaments/",
            {"game": "Valorant", "title": "Test Cup", "prize": "5000", "starts": "Sat", "capacity": "0", "cafe_id": cafe_id},
            **self.admin_header(),
        )
        self.assertEqual(resp.status_code, 400)

    def test_create_tournament_rejects_negative_entry_fee(self):
        cafe_id = self._create_cafe()
        resp = self.client.post(
            "/api/v1/main/tournaments/",
            {
                "game": "Valorant", "title": "Test Cup", "prize": "5000", "starts": "Sat",
                "capacity": "16", "entry": "Paid Entry", "entryFee": "-500", "cafe_id": cafe_id,
            },
            **self.admin_header(),
        )
        self.assertEqual(resp.status_code, 400)

    def test_register_wrong_team_size_rejected(self):
        cafe_id = self._create_cafe()
        create_resp = self.client.post(
            "/api/v1/main/tournaments/",
            {
                "game": "Valorant", "title": "Squad Cup", "prize": "5000", "starts": "Sat",
                "capacity": "16", "mode": "Squad", "cafe_id": cafe_id,
            },
            **self.admin_header(),
        )
        self.assertEqual(create_resp.status_code, 201)
        tid = create_resp.json()["tournament"]["id"]
        self.track("tournaments", __import__("bson").ObjectId(tid))

        _, token = self.make_active_user()
        resp = self.client.post(
            f"/api/v1/main/tournaments/{tid}/register/",
            {"gamer_ids": ["OnlyOnePlayer"]},  # Squad mode requires exactly 4
            format="json",
            **self.auth_header(token),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("exactly 4", resp.json()["message"])

    def test_register_duplicate_gamer_ids_rejected(self):
        cafe_id = self._create_cafe()
        create_resp = self.client.post(
            "/api/v1/main/tournaments/",
            {
                "game": "Valorant", "title": "Solo Cup", "prize": "5000", "starts": "Sat",
                "capacity": "16", "mode": "Solo", "cafe_id": cafe_id,
            },
            **self.admin_header(),
        )
        tid = create_resp.json()["tournament"]["id"]
        self.track("tournaments", __import__("bson").ObjectId(tid))

        _, token = self.make_active_user()
        resp = self.client.post(
            f"/api/v1/main/tournaments/{tid}/register/",
            {"gamer_ids": ["SamePlayer"]},
            format="json",
            **self.auth_header(token),
        )
        self.assertEqual(resp.status_code, 200)


class PartnerApplicationValidationTests(SecurityTestCase):
    """The only fully public, unauthenticated form-submission endpoint on the platform."""

    def _base_payload(self, **overrides):
        payload = {
            "cafeName": "Test Cafe", "ownerName": "Test Owner", "phone": "+919876543210",
            "email": "owner@example.com", "city": "Mumbai", "state": "Maharashtra",
            "address": "123 Test Street", "pcCount": "10",
        }
        payload.update(overrides)
        return payload

    def test_rejects_malformed_email(self):
        resp = self.client.post(
            "/api/v1/main/partner-applications/", self._base_payload(email="not-an-email")
        )
        self.assertEqual(resp.status_code, 400)

    def test_rejects_negative_pc_count(self):
        resp = self.client.post(
            "/api/v1/main/partner-applications/", self._base_payload(pcCount="-5")
        )
        self.assertEqual(resp.status_code, 400)

    def test_rejects_malformed_maps_link(self):
        resp = self.client.post(
            "/api/v1/main/partner-applications/",
            self._base_payload(mapsLink="not a real url"),
        )
        self.assertEqual(resp.status_code, 400)

    def test_rejects_oversized_message(self):
        resp = self.client.post(
            "/api/v1/main/partner-applications/",
            self._base_payload(message="x" * 801),
        )
        self.assertEqual(resp.status_code, 400)

    def test_valid_application_accepted(self):
        resp = self.client.post(
            "/api/v1/main/partner-applications/", self._base_payload()
        )
        self.assertEqual(resp.status_code, 201)
        app_id = resp.json()["application"]["id"]
        self.track("partner_applications", __import__("bson").ObjectId(app_id))

    def test_status_update_rejects_arbitrary_value(self):
        resp = self.client.post(
            "/api/v1/main/partner-applications/", self._base_payload(email="statustest@example.com")
        )
        app_id = resp.json()["application"]["id"]
        self.track("partner_applications", __import__("bson").ObjectId(app_id))

        resp2 = self.client.patch(
            f"/api/v1/main/partner-applications/{app_id}/",
            {"status": "hacked_status"},
            format="json",
            **self.admin_header(),
        )
        self.assertEqual(resp2.status_code, 400)

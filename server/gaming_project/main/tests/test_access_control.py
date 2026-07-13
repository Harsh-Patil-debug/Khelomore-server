# test_access_control.py
# Regression tests for: soft-deleted cafe PII disclosure gating, the OAuth open-redirect
# fix, the public cafe endpoints leaking the owner's login email, and tournament
# registration visibility for a cafe-scoped tournament's own owner.

from .base import SecurityTestCase


class TournamentRegistrationsOwnershipTests(SecurityTestCase):
    """
    A cafe-scoped tournament's own owner must be able to view its registrations (not
    just super admin) — otherwise the cafe-owner dashboard has no legitimate way to show
    a cafe owner their own tournament's sign-ups.
    """

    def _make_tournament(self, cafe_id=None):
        doc = {
            "game": "VALORANT", "title": "Sectest Cup", "prize": "₹1,000",
            "entry": "Free Entry", "registered": 0, "capacity": 8,
            "cafe_id": cafe_id, "status": "upcoming", "registration_open": True,
        }
        result = self.db.tournaments.insert_one(doc)
        return self.track("tournaments", result.inserted_id)

    def test_cafe_owner_can_view_registrations_for_their_own_tournament(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        tournament_id = self._make_tournament(cafe_id=cafe_id)

        resp = self.client.get(
            f"/api/v1/main/tournaments/{tournament_id}/", **self.auth_header(owner_token)
        )
        self.assertEqual(resp.status_code, 200)

    def test_unrelated_user_cannot_view_registrations_for_someone_elses_tournament(self):
        cafe_owner_email, _ = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=cafe_owner_email)
        tournament_id = self._make_tournament(cafe_id=cafe_id)

        attacker_email, attacker_token = self.make_active_user()
        resp = self.client.get(
            f"/api/v1/main/tournaments/{tournament_id}/", **self.auth_header(attacker_token)
        )
        self.assertEqual(resp.status_code, 403)

    def test_platform_wide_tournament_registrations_require_super_admin(self):
        tournament_id = self._make_tournament(cafe_id=None)
        cafe_owner_email, cafe_owner_token = self.make_active_user(role="admin")
        self.make_cafe(owner_email=cafe_owner_email)

        resp = self.client.get(
            f"/api/v1/main/tournaments/{tournament_id}/", **self.auth_header(cafe_owner_token)
        )
        self.assertEqual(resp.status_code, 403)

        resp2 = self.client.get(f"/api/v1/main/tournaments/{tournament_id}/", **self.admin_header())
        self.assertEqual(resp2.status_code, 200)


class CafeOwnerEmailExposureTests(SecurityTestCase):
    """
    Guards against owner_email (the cafe admin's login identifier, not just a contact
    address) being visible on the public cafe listing/detail endpoints.
    """

    def test_public_cafe_list_does_not_expose_owner_email(self):
        cafe_id = self.make_cafe(owner_email="real-owner@khelomore.invalid")
        resp = self.client.get("/api/v1/main/cafes/")
        self.assertEqual(resp.status_code, 200)
        cafe = next(c for c in resp.json()["cafes"] if c["id"] == cafe_id)
        self.assertEqual(cafe["owner_email"], "")

    def test_public_cafe_detail_does_not_expose_owner_email(self):
        cafe_id = self.make_cafe(owner_email="real-owner2@khelomore.invalid")
        resp = self.client.get(f"/api/v1/main/cafes/{cafe_id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["cafe"]["owner_email"], "")

    def test_contact_email_does_not_silently_fall_back_to_owner_email_publicly(self):
        cafe_id = self.make_cafe(owner_email="real-owner3@khelomore.invalid")
        resp = self.client.get(f"/api/v1/main/cafes/{cafe_id}/")
        self.assertEqual(resp.json()["cafe"]["contact_email"], "")

    def test_owner_still_sees_their_own_email_on_my_cafes(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        self.make_cafe(owner_email=owner_email)
        resp = self.client.get("/api/v1/main/cafes/my/", **self.auth_header(owner_token))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(all(c["owner_email"] == owner_email for c in resp.json()["cafes"]))

    def test_super_admin_still_sees_owner_email_via_include_deleted(self):
        doc = {
            "name": "Sectest Owner-Visible Deleted Cafe", "area": "Test Area",
            "price_per_hour": 100, "owner_email": "deleted-owner2@khelomore.invalid",
            "is_deleted": True,
        }
        result = self.db.cafes.insert_one(doc)
        cafe_id = str(self.track("cafes", result.inserted_id))
        resp = self.client.get("/api/v1/main/cafes/?include_deleted=true", **self.admin_header())
        cafe = next(c for c in resp.json()["cafes"] if c["id"] == cafe_id)
        self.assertEqual(cafe["owner_email"], "deleted-owner2@khelomore.invalid")


class CafeIncludeDeletedTests(SecurityTestCase):
    """Guards against unauthenticated users listing soft-deleted cafes (owner PII)."""

    def test_include_deleted_requires_auth(self):
        resp = self.client.get("/api/v1/main/cafes/?include_deleted=true")
        self.assertEqual(resp.status_code, 401)

    def test_include_deleted_allowed_for_super_admin(self):
        resp = self.client.get("/api/v1/main/cafes/?include_deleted=true", **self.admin_header())
        self.assertEqual(resp.status_code, 200)

    def test_normal_cafes_list_is_public(self):
        resp = self.client.get("/api/v1/main/cafes/")
        self.assertEqual(resp.status_code, 200)

    def test_deleted_cafe_hidden_from_public_but_visible_to_super_admin(self):
        doc = {
            "name": "Sectest Deleted Cafe",
            "area": "Test Area",
            "price_per_hour": 100,
            "owner_email": "deleted-owner@khelomore.invalid",
            "is_deleted": True,
        }
        result = self.db.cafes.insert_one(doc)
        deleted_id = str(self.track("cafes", result.inserted_id))

        resp_public = self.client.get("/api/v1/main/cafes/")
        public_ids = {c["id"] for c in resp_public.json()["cafes"]}
        self.assertNotIn(deleted_id, public_ids)

        resp_admin = self.client.get("/api/v1/main/cafes/?include_deleted=true", **self.admin_header())
        admin_ids = {c["id"] for c in resp_admin.json()["cafes"]}
        self.assertIn(deleted_id, admin_ids)


class DbCheckAccessTests(SecurityTestCase):
    """Guards against the unauthenticated live-DB-write diagnostic endpoint."""

    def test_db_check_requires_auth(self):
        resp = self.client.get("/api/v1/main/db/")
        self.assertEqual(resp.status_code, 401)

    def test_db_check_allowed_for_super_admin(self):
        resp = self.client.get("/api/v1/main/db/", **self.admin_header())
        self.assertEqual(resp.status_code, 200)


class OAuthRedirectTests(SecurityTestCase):
    """Guards against the OAuth state/return_url open redirect that could leak a session
    token to an attacker-controlled site."""

    def test_helper_rejects_arbitrary_https_url(self):
        from ..views import _is_allowed_oauth_redirect_target
        self.assertFalse(_is_allowed_oauth_redirect_target("https://evil.example.com/steal"))

    def test_helper_rejects_lookalike_domain(self):
        from ..views import _is_allowed_oauth_redirect_target
        self.assertFalse(_is_allowed_oauth_redirect_target("https://khelomore.com.evil.example.com/"))

    def test_helper_allows_app_schemes(self):
        from ..views import _is_allowed_oauth_redirect_target
        self.assertTrue(_is_allowed_oauth_redirect_target("khelomore://callback"))
        self.assertTrue(_is_allowed_oauth_redirect_target("exp://192.168.1.1:19000"))

    def test_helper_rejects_empty_target(self):
        from ..views import _is_allowed_oauth_redirect_target
        self.assertFalse(_is_allowed_oauth_redirect_target(""))
        self.assertFalse(_is_allowed_oauth_redirect_target(None))

    def test_google_login_rejects_untrusted_return_url(self):
        resp = self.client.get("/api/v1/main/auth/google/login/?return_url=https://evil.example.com")
        self.assertEqual(resp.status_code, 400)

    def test_google_login_allows_app_scheme_return_url(self):
        resp = self.client.get("/api/v1/main/auth/google/login/?return_url=khelomore://callback")
        self.assertEqual(resp.status_code, 302)

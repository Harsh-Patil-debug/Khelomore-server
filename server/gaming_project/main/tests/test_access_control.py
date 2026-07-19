# test_access_control.py
# Regression tests for: soft-deleted cafe PII disclosure gating, the OAuth open-redirect
# fix, the public cafe endpoints leaking the owner's login email, and tournament
# registration visibility for a cafe-scoped tournament's own owner.

from bson import ObjectId

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


class TournamentOwnerMutationTests(SecurityTestCase):
    """
    A cafe-scoped tournament's own owner must be able to create/update/delete it, not just
    super admin. Regression guard for a real bug: create/patch/delete required
    authenticate_super_admin_request, so a cafe owner managing their own tournament from
    cafe-command-center (Start Live/End/Revert/Edit/Delete all go through PATCH) got a 401
    "Authorization token missing or invalid" — even though GET (viewing registrations) on
    the same view already had the correct cafe-owner-or-super-admin check.
    """

    def _make_tournament(self, cafe_id):
        doc = {
            "game": "VALORANT", "title": "Sectest Owner Cup", "prize": "₹1,000",
            "entry": "Free Entry", "registered": 0, "capacity": 8,
            "cafe_id": cafe_id, "status": "upcoming", "registration_open": True,
        }
        result = self.db.tournaments.insert_one(doc)
        return str(self.track("tournaments", result.inserted_id))

    def test_cafe_owner_can_create_tournament_for_their_own_cafe(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)

        resp = self.client.post(
            "/api/v1/main/tournaments/",
            {
                "cafe_id": cafe_id, "game": "Valorant", "title": "Owner Cup",
                "prize": "5000", "entry": "Free Entry", "capacity": "16",
                "starts": "Test Slot", "startsIso": "2026-08-01T12:00:00Z",
            },
            format="multipart", **self.auth_header(owner_token),
        )
        self.assertEqual(resp.status_code, 201)
        self.track("tournaments", ObjectId(resp.json()["tournament"]["id"]))

    def test_cafe_owner_can_update_and_cancel_their_own_tournament(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        tournament_id = self._make_tournament(cafe_id)

        resp = self.client.patch(
            f"/api/v1/main/tournaments/{tournament_id}/",
            {"status": "cancelled"}, format="multipart", **self.auth_header(owner_token),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["tournament"]["status"], "cancelled")

    def test_cafe_owner_can_delete_their_own_tournament(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        tournament_id = self._make_tournament(cafe_id)

        resp = self.client.delete(
            f"/api/v1/main/tournaments/{tournament_id}/", **self.auth_header(owner_token)
        )
        self.assertEqual(resp.status_code, 200)

    def test_unrelated_admin_cannot_update_someone_elses_tournament(self):
        owner_email, _ = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        tournament_id = self._make_tournament(cafe_id)

        attacker_email, attacker_token = self.make_active_user(role="admin")
        self.make_cafe(owner_email=attacker_email)
        resp = self.client.patch(
            f"/api/v1/main/tournaments/{tournament_id}/",
            {"status": "live"}, format="multipart", **self.auth_header(attacker_token),
        )
        self.assertEqual(resp.status_code, 403)


class RigOwnerMutationTests(SecurityTestCase):
    """
    A cafe-scoped rig's (system's) own owner must be able to create/update/delete it, not
    just super admin. Same class of bug as tournaments — the Systems page in
    cafe-command-center called these endpoints while they required
    authenticate_super_admin_request.
    """

    def test_cafe_owner_can_create_update_delete_their_own_rig(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)

        resp = self.client.post(
            "/api/v1/main/rigs/", {"cafe_id": cafe_id, "name": "PC #99", "type": "PC"},
            format="json", **self.auth_header(owner_token),
        )
        self.assertEqual(resp.status_code, 201)
        rig_id = resp.json()["rig"]["id"]
        self.track("rigs", ObjectId(rig_id))

        resp2 = self.client.put(
            f"/api/v1/main/rigs/{rig_id}/", {"status": "maintenance"},
            format="json", **self.auth_header(owner_token),
        )
        self.assertEqual(resp2.status_code, 200)

        resp3 = self.client.delete(f"/api/v1/main/rigs/{rig_id}/", **self.auth_header(owner_token))
        self.assertEqual(resp3.status_code, 200)

    def test_unrelated_admin_cannot_modify_someone_elses_rig(self):
        owner_email, _ = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        doc = {"cafe_id": cafe_id, "type": "PC", "name": "PC #01", "status": "available"}
        result = self.db.rigs.insert_one(doc)
        rig_id = str(self.track("rigs", result.inserted_id))

        attacker_email, attacker_token = self.make_active_user(role="admin")
        self.make_cafe(owner_email=attacker_email)
        resp = self.client.put(
            f"/api/v1/main/rigs/{rig_id}/", {"status": "maintenance"},
            format="json", **self.auth_header(attacker_token),
        )
        self.assertEqual(resp.status_code, 403)


class OfferOwnerMutationTests(SecurityTestCase):
    """
    A cafe-scoped offer's own owner must be able to list/create/update/delete it, not just
    super admin. Same class of bug as tournaments/rigs — the Offers page in
    cafe-command-center called these endpoints while they required
    authenticate_super_admin_request.
    """

    def test_cafe_owner_can_list_create_update_their_own_offer(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)

        resp = self.client.post(
            "/api/v1/main/offers/",
            {
                "cafe_id": cafe_id, "name": "Owner Offer", "type": "custom",
                "discount_pct": 10, "start_date": "2026-01-01", "end_date": "2026-12-31",
            },
            format="json", **self.auth_header(owner_token),
        )
        self.assertEqual(resp.status_code, 201)
        offer_id = resp.json()["offer"]["id"]
        self.track("offers", ObjectId(offer_id))

        resp2 = self.client.get(
            f"/api/v1/main/offers/?cafe_id={cafe_id}", **self.auth_header(owner_token)
        )
        self.assertEqual(resp2.status_code, 200)

        resp3 = self.client.patch(
            f"/api/v1/main/offers/{offer_id}/", {"is_deleted": True},
            format="json", **self.auth_header(owner_token),
        )
        self.assertEqual(resp3.status_code, 200)
        self.assertTrue(resp3.json()["offer"]["is_deleted"])

    def test_unrelated_admin_cannot_modify_someone_elses_offer(self):
        owner_email, _ = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        doc = {
            "cafe_id": cafe_id, "name": "X", "type": "custom", "discount_pct": 10,
            "start_date": "2026-01-01", "end_date": "2026-12-31",
            "is_active": True, "is_deleted": False,
        }
        result = self.db.offers.insert_one(doc)
        offer_id = str(self.track("offers", result.inserted_id))

        attacker_email, attacker_token = self.make_active_user(role="admin")
        self.make_cafe(owner_email=attacker_email)
        resp = self.client.patch(
            f"/api/v1/main/offers/{offer_id}/", {"is_deleted": True},
            format="json", **self.auth_header(attacker_token),
        )
        self.assertEqual(resp.status_code, 403)


class CafeProfileOwnerMutationTests(SecurityTestCase):
    """
    A cafe owner must be able to edit their own Cafe Profile page from
    cafe-command-center — CafeDetailView.put required authenticate_super_admin_request,
    the same class of bug as tournaments/rigs/offers.
    """

    def test_cafe_owner_can_update_their_own_cafe_profile(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)

        resp = self.client.put(
            f"/api/v1/main/cafes/{cafe_id}/", {"name": "Updated Cafe Name"},
            format="json", **self.auth_header(owner_token),
        )
        self.assertEqual(resp.status_code, 200)

    def test_unrelated_admin_cannot_update_someone_elses_cafe_profile(self):
        owner_email, _ = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)

        attacker_email, attacker_token = self.make_active_user(role="admin")
        self.make_cafe(owner_email=attacker_email)
        resp = self.client.put(
            f"/api/v1/main/cafes/{cafe_id}/", {"name": "Hijacked"},
            format="json", **self.auth_header(attacker_token),
        )
        self.assertEqual(resp.status_code, 403)


class WebsiteUserMeCookieRoutingTests(SecurityTestCase):
    """
    /auth/me/'s cookie-based lookup-order heuristic must resolve a website_user session to
    db.website_users first, not db.users. Regression guard for a real bug: the public
    website's Google-login flow set its session cookie under the name "km_gamer_token" —
    the SAME name used for the mobile app's role="user" sessions — so /auth/me/ treated
    every website session as if it were a mobile one and checked db.users first. Anyone
    whose email happened to also exist in db.users (e.g. from also using the mobile app)
    got that OTHER account's data back — in the reported case, an empty phone number —
    which silently overwrote the correct session and made the "Complete your onboarding"
    phone prompt reappear on every page load despite a phone already being saved. Fixed by
    giving website_user sessions their own cookie name, "km_website_token".
    """

    def _make_duplicate_accounts(self):
        email = self.unique_email("dup")
        self.db.website_users.delete_many({"email": email})
        self.db.users.delete_many({"email": email})
        from ..Handlers import auth_handler
        real_phone = auth_handler.encrypt_phone_field("9812345678")
        result_w = self.db.website_users.insert_one({
            "email": email, "gamertag": "WEBSITE_ACCOUNT", "status": "Active",
            "role": "website_user", "auth_provider": "google", "phone": real_phone,
        })
        self.track("website_users", result_w.inserted_id)
        result_u = self.db.users.insert_one({
            "email": email, "gamertag": "MOBILE_DUPLICATE", "status": "Active",
            "role": "user", "auth_provider": "google", "phone": "",
        })
        self.track("users", result_u.inserted_id)
        return email

    def test_website_cookie_resolves_the_website_users_account_not_the_duplicate(self):
        from ..Handlers import auth_handler
        email = self._make_duplicate_accounts()
        token = auth_handler.generate_token(email, role="website_user")

        client = self.client
        client.cookies["km_website_token"] = token
        resp = client.get("/api/v1/main/auth/me/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["user"]["phone"], "9812345678")

    def test_bearer_header_also_resolves_the_website_users_account(self):
        from ..Handlers import auth_handler
        email = self._make_duplicate_accounts()
        token = auth_handler.generate_token(email, role="website_user")

        resp = self.client.get("/api/v1/main/auth/me/", **self.auth_header(token))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["user"]["phone"], "9812345678")

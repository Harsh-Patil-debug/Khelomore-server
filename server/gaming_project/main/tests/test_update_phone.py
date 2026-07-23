# test_update_phone.py
# Regression test for /auth/update-phone/: it must route to the caller's own collection via
# role (same as every other auth endpoint), not guess by checking website_users before users.
# The guess previously meant a mobile user's phone update silently landed on a website_users
# document instead whenever the same email existed in both collections.

from ..Handlers import auth_handler
from .base import SecurityTestCase


class UpdatePhoneCollectionRoutingTests(SecurityTestCase):
    def _update_phone(self, token, phone, role=""):
        enc_phone, iv = self.encrypt_with_shared_iv(phone)
        return self.client.post(
            "/api/v1/main/auth/update-phone/",
            {"phone": enc_phone, "iv": iv, "role": role},
            format="json",
            **self.auth_header(token),
        )

    def test_mobile_user_phone_update_lands_in_users_collection(self):
        email, token = self.make_active_user(collection="users")
        resp = self._update_phone(token, "9876543210", role="")
        self.assertEqual(resp.status_code, 200)

        doc = self.db.users.find_one({"email": email})
        self.assertEqual(auth_handler.decrypt_phone_field(doc["phone"]), "9876543210")

    def test_website_user_phone_update_lands_in_website_users_collection(self):
        email, token = self.make_active_user(collection="website_users")
        resp = self._update_phone(token, "9876543211", role="website_user")
        self.assertEqual(resp.status_code, 200)

        doc = self.db.website_users.find_one({"email": email})
        self.assertEqual(auth_handler.decrypt_phone_field(doc["phone"]), "9876543211")

    def test_same_email_in_both_collections_only_updates_the_caller_s_own(self):
        # The exact collision scenario the old website_users-then-users guess got wrong.
        email = self.unique_email("collision")
        _, mobile_token = self.make_active_user(email=email, collection="users")
        _, website_token = self.make_active_user(email=email, collection="website_users")

        self._update_phone(mobile_token, "1111111111", role="")

        mobile_doc = self.db.users.find_one({"email": email})
        website_doc = self.db.website_users.find_one({"email": email})
        self.assertEqual(auth_handler.decrypt_phone_field(mobile_doc["phone"]), "1111111111")
        self.assertNotIn("phone", website_doc)

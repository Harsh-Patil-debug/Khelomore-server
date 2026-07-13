# base.py
# Shared scaffolding for the backend security-regression suite.
#
# Runs against an isolated MongoDB test database (see manage.py: MONGO_DB_NAME is
# swapped to KheloMoreDB_test for `manage.py test` runs) — never touches real data.
# Seeds/queries MongoDB directly via pymongo for fast, deterministic setup (bypassing the
# encrypted HTTP contract where that's not what's under test), then exercises the real
# handler functions / DRF views for assertions, so these tests fail if a future change
# reopens any of the specific vulnerabilities they guard against.

import base64
import uuid
from datetime import datetime, timedelta

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad
from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

from ..Handlers import auth_handler
from ..Handlers.db_connection import get_db, MONGO_DB_NAME


class SecurityTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        assert MONGO_DB_NAME.endswith("_test"), (
            f"Refusing to run security tests against non-test database '{MONGO_DB_NAME}' — "
            "check manage.py's MONGO_DB_NAME override for `test` runs."
        )
        cls.db = get_db()
        assert cls.db is not None, "MongoDB test database is not reachable — check MONGO_URL."

    def setUp(self):
        self.client = APIClient()
        self._created = []

    def tearDown(self):
        for collection_name, doc_id in self._created:
            self.db[collection_name].delete_one({"_id": doc_id})

    def track(self, collection_name, doc_id):
        self._created.append((collection_name, doc_id))
        return doc_id

    def unique_email(self, tag="user"):
        return f"sectest.{tag}.{uuid.uuid4().hex[:10]}@khelomore.invalid"

    def encrypt_with_shared_iv(self, *plaintexts):
        """
        Encrypts multiple values under one shared IV, matching the real client contract for
        multi-field encrypted bodies (e.g. khelomore_verify_otp decrypts both `email` and
        `otp_code` using a single `iv` field).
        """
        iv_bytes = get_random_bytes(16)
        iv_b64 = base64.b64encode(iv_bytes).decode('utf-8')
        results = []
        for plain in plaintexts:
            cipher = AES.new(auth_handler.ENCRYPTION_KEY, AES.MODE_CBC, iv_bytes)
            enc = cipher.encrypt(pad(plain.encode('utf-8'), AES.block_size))
            results.append(base64.b64encode(enc).decode('utf-8'))
        return (*results, iv_b64)

    def make_active_user(self, email=None, role="", collection="users"):
        """Directly inserts an Active user/admin/super_admin doc, bypassing OTP."""
        email = email or self.unique_email(role or "user")
        coll = self.db[collection]
        doc = {
            "email": email,
            "gamertag": "SECTEST",
            "status": "Active",
            "role": role or "user",
            "xp": 0,
            "rank": "Recruit PRO I",
        }
        result = coll.insert_one(doc)
        self.track(collection, result.inserted_id)
        token = auth_handler.generate_token(email)
        return email, token

    def make_cafe(self, owner_email, price_per_hour=0):
        doc = {
            "name": "Sectest Cafe",
            "area": "Test Area",
            "price_per_hour": price_per_hour,
            "owner_email": owner_email,
            "is_deleted": False,
        }
        result = self.db.cafes.insert_one(doc)
        self.track("cafes", result.inserted_id)
        return str(result.inserted_id)

    def make_booking(self, user_email, cafe_id, price=100, payment_status="paid", date="2099-01-01"):
        doc = {
            "user_email": user_email,
            "user_name": "SECTEST",
            "cafe_id": cafe_id,
            "cafe_name": "Sectest Cafe",
            "zone": "Regular Zone",
            "date": date,
            "slots": ["10:00 AM - 11:00 AM"],
            "slot": "10:00 AM - 11:00 AM",
            "price": price,
            "code": "123456",
            "rig": "PC #01",
            "status": "Upcoming",
            "payment_status": payment_status,
        }
        result = self.db.bookings.insert_one(doc)
        self.track("bookings", result.inserted_id)
        return str(result.inserted_id)

    def auth_header(self, token):
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def admin_header(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {settings.ADMIN_TOKEN}"}

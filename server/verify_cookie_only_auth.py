# verify_cookie_only_auth.py
# Proves that a real super_admin OTP login results in a cookie that, ALONE (no
# Authorization header at all), is sufficient for subsequent super-admin-gated requests.
#
# This is the exact mechanism the playhub-command / cafe-command-center frontend fix
# depends on: removing the static VITE_ADMIN_TOKEN fallback only works if
# `credentials: "include"` + the HttpOnly cookie the backend already sets is enough on
# its own.
#
# Uses DRF's APIClient rather than `requests` deliberately: the cookie the backend sets
# is Secure=True (correctly — it should never be sent over plain HTTP), so a real HTTP
# client talking to a plain http:// dev server will correctly refuse to send it back,
# which looks like a failure but is actually correct browser-security behavior, not a
# backend bug. APIClient isolates the actual application logic (does the Django view
# correctly authenticate via request.COOKIES?) from that transport-layer detail. The real
# frontends only ever talk to this backend over HTTPS (the ngrok tunnel in VITE_API_URL),
# so the Secure flag is satisfied in actual use.

import base64
import os
import sys
from datetime import datetime, timedelta

sys.path.append(r"C:\Users\DELL\OneDrive\Desktop\khelomore-server\server")
os.environ["MONGO_DB_NAME"] = os.getenv("MONGO_DB_NAME_TEST", "KheloMoreDB_test")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings")

import django
django.setup()

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad
from rest_framework.test import APIClient

from gaming_project.main.Handlers import auth_handler
from gaming_project.main.Handlers.db_connection import get_db


def encrypt(plain: str, iv_bytes: bytes) -> str:
    cipher = AES.new(auth_handler.ENCRYPTION_KEY, AES.MODE_CBC, iv_bytes)
    return base64.b64encode(cipher.encrypt(pad(plain.encode("utf-8"), AES.block_size))).decode("utf-8")


def main():
    db = get_db()
    assert db is not None, "MongoDB test database is not reachable."

    email = "cookie-auth-verify@khelomore.invalid"
    db.super_admin.delete_many({"email": email})

    print("=" * 70)
    print("  COOKIE-ONLY SUPER-ADMIN AUTH — VERIFICATION")
    print("=" * 70)

    otp_code = "424242"
    db.super_admin.insert_one({
        "email": email, "gamertag": "COOKIE_TEST", "status": "Active", "role": "super_admin",
        "otp_code": otp_code, "otp_expiry": datetime.now(auth_handler.IST) + timedelta(minutes=10),
    })

    client = APIClient()

    print("\n[STEP 1] POST /auth/verify-otp/ with role=super_admin (as the frontend does)...")
    iv_bytes = get_random_bytes(16)
    iv_b64 = base64.b64encode(iv_bytes).decode("utf-8")
    resp = client.post("/api/v1/main/auth/verify-otp/", {
        "email": encrypt(email, iv_bytes),
        "otp_code": encrypt(otp_code, iv_bytes),
        "iv": iv_b64,
        "role": "super_admin",
    }, format="json")
    print(f"  - Status: {resp.status_code}")
    assert resp.status_code == 200, f"verify-otp failed: {resp.content}"

    cookie_names = list(resp.cookies.keys())
    print(f"  - Cookies received: {cookie_names}")
    assert "km_super_admin_token" in cookie_names, "km_super_admin_token cookie was not set!"

    print("\n[STEP 2] GET /users/ (super-admin-gated) using ONLY the client's cookies —"
          " deliberately with NO Authorization header, exactly what the fixed frontend"
          " will do...")
    resp2 = client.get("/api/v1/main/users/")  # APIClient carries cookies automatically
    print(f"  - Status: {resp2.status_code}")
    assert resp2.status_code == 200, (
        f"Cookie-only request FAILED with {resp2.status_code}: {resp2.content}\n"
        "This means removing the static admin token from the frontend WOULD break it — "
        "do not proceed with that fix without investigating this first."
    )
    print(f"  - Response ok, {len(resp2.json().get('users', []))} users returned")

    print("\n[STEP 3] Confirm a request with NEITHER a cookie NOR an Authorization header"
          " is correctly rejected (sanity check — cookie-only access shouldn't mean"
          " no-auth-at-all access)...")
    fresh_client = APIClient()
    resp3 = fresh_client.get("/api/v1/main/users/")
    print(f"  - Status: {resp3.status_code}")
    assert resp3.status_code == 401, f"Expected 401 with zero credentials, got {resp3.status_code}"

    db.super_admin.delete_many({"email": email})

    print("\n" + "=" * 70)
    print("  CONFIRMED: the HttpOnly km_super_admin_token cookie, set once during OTP")
    print("  verification, is sufficient on its own for super-admin-gated requests.")
    print("  Removing the static VITE_ADMIN_TOKEN fallback from the frontend is SAFE —")
    print("  a real logged-in super admin will keep working via the cookie alone, over")
    print("  the HTTPS connection both frontends already use.")
    print("=" * 70)


if __name__ == "__main__":
    main()

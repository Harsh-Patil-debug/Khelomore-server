import os
import sys
import django
import json
import pymongo
from pymongo import MongoClient

# Setup django
sys.path.append(r"C:\Users\DELL\OneDrive\Desktop\bookmyconsole-server\server")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings")
django.setup()

from django.conf import settings
from gaming_project.main.Handlers.db_connection import get_db
from gaming_project.main.Handlers import auth_handler, auth_middleware
from gaming_project.main.Handlers.auth_handler import ENCRYPTION_KEY, decrypt_data
from rest_framework.request import Request
from django.test import RequestFactory

def verify():
    print("======================================================================")
    print("   SUPER ADMIN ZERO-KNOWLEDGE & ARGON2ID VERIFICATION TEST")
    print("======================================================================")
    
    db = get_db()
    test_email = "verify_superadmin@bookmyconsole.com"
    db.super_admin.delete_many({"email": test_email})
    
    import base64
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    from Crypto.Random import get_random_bytes

    # Generate encryption IV
    iv_bytes = get_random_bytes(16)
    iv_b64 = base64.b64encode(iv_bytes).decode('utf-8')

    def encrypt(plain):
        cipher = AES.new(ENCRYPTION_KEY, AES.MODE_CBC, iv_bytes)
        enc = cipher.encrypt(pad(plain.encode('utf-8'), AES.block_size))
        return base64.b64encode(enc).decode('utf-8')

    print("\n[STEP 1] Encrypting payloads on client side (Zero-Knowledge transit)...")
    gamertag_enc = encrypt("SUPER_TESTER")
    email_enc = encrypt(test_email)
    password_enc = encrypt("supersecurepassword123")
    
    print(f"  - Generated IV (base64)    : {iv_b64}")
    print(f"  - Encrypted Email (base64) : {email_enc}")
    print(f"  - Encrypted Password (base64): {password_enc}")

    print("\n[STEP 2] Sending registration request to backend...")
    result, status = auth_handler.bookmyconsole_register(
        gamertag=gamertag_enc,
        email=email_enc,
        password=password_enc,
        iv=iv_b64,
        is_admin=False,
        role="super_admin"
    )
    print(f"  - Registration Status Code: {status}")
    assert status == 200, f"Registration failed: {result}"

    print("\n[STEP 3] Verifying database record in 'super_admin' collection...")
    doc = db.super_admin.find_one({"email": test_email})
    assert doc is not None, "Failed to retrieve doc from super_admin collection!"
    
    print(f"  - Collection Name          : super_admin")
    print(f"  - Stored Gamertag          : {doc.get('gamertag')}")
    print(f"  - Stored Email             : {doc.get('email')}")
    print(f"  - Stored Status            : {doc.get('status')}")
    print(f"  - Stored Role              : {doc.get('role')}")
    
    password_hash = doc.get("password_hash")
    print(f"  - Password Hash from DB    : {password_hash}")
    
    # Check if Argon2id format
    is_argon2 = password_hash.startswith("$argon2id$")
    print(f"  - Is Argon2id Hashed?      : {is_argon2}")
    assert is_argon2, "Password is not hashed with Argon2id!"

    print("\n[STEP 4] Simulating login request (Step 1)...")
    login_iv_bytes = get_random_bytes(16)
    login_iv_b64 = base64.b64encode(login_iv_bytes).decode('utf-8')
    
    def encrypt_login(plain):
        cipher = AES.new(ENCRYPTION_KEY, AES.MODE_CBC, login_iv_bytes)
        enc = cipher.encrypt(pad(plain.encode('utf-8'), AES.block_size))
        return base64.b64encode(enc).decode('utf-8')
        
    email_enc_login = encrypt_login(test_email)
    password_enc_login = encrypt_login("supersecurepassword123")

    login_result, login_status = auth_handler.bookmyconsole_login(
        email=email_enc_login,
        password=password_enc_login,
        iv=login_iv_b64,
        is_admin=False,
        role="super_admin"
    )
    print(f"  - Login Status Code: {login_status}")
    assert login_status == 200, f"Login failed: {login_result}"
    
    # Get OTP code from DB
    doc = db.super_admin.find_one({"email": test_email})
    otp_code = doc.get("otp_code")
    print(f"  - Intercepted OTP Code     : {otp_code}")

    print("\n[STEP 5] Verifying OTP and generating dynamic JWT token...")
    verify_iv_bytes = get_random_bytes(16)
    verify_iv_b64 = base64.b64encode(verify_iv_bytes).decode('utf-8')
    
    def encrypt_verify(plain):
        cipher = AES.new(ENCRYPTION_KEY, AES.MODE_CBC, verify_iv_bytes)
        enc = cipher.encrypt(pad(plain.encode('utf-8'), AES.block_size))
        return base64.b64encode(enc).decode('utf-8')

    email_enc_verify = encrypt_verify(test_email)
    otp_enc_verify = encrypt_verify(otp_code)

    verify_result, verify_status = auth_handler.bookmyconsole_verify_otp(
        email=email_enc_verify,
        otp_code=otp_enc_verify,
        iv=verify_iv_b64,
        is_admin=False,
        role="super_admin"
    )
    print(f"  - Verify Status Code       : {verify_status}")
    assert verify_status == 200, f"Verification failed: {verify_result}"

    # Decrypt response payload
    decrypted_response = decrypt_data(verify_result["encrypted_response"], verify_result["iv"])
    response_data = json.loads(decrypted_response)
    token = response_data["token"]
    user_info = response_data["user"]
    print(f"  - Issued Token             : {token[:30]}...")
    print(f"  - User Info from Token     : {user_info}")
    
    # Check if doc status updated to Active
    doc = db.super_admin.find_one({"email": test_email})
    print(f"  - Database Status now      : {doc.get('status')}")
    assert doc.get("status") == "Active", "Status did not update to Active!"

    print("\n[STEP 6] Authenticating request with dynamic token via middleware...")
    factory = RequestFactory()
    django_req = factory.get('/tournaments/all/', HTTP_AUTHORIZATION=f'Bearer {token}')
    req = Request(django_req)
    
    authenticated_email, err = auth_middleware.authenticate_super_admin_request(req)
    print(f"  - Middleware result email  : {authenticated_email}")
    print(f"  - Middleware result error  : {err}")
    assert err is None, f"Middleware authentication failed: {err}"
    assert authenticated_email == test_email

    # Cleanup
    db.super_admin.delete_many({"email": test_email})
    print("\n======================================================================")
    print("  ALL VERIFICATION CHECKS PASSED SUCCESSFULLY!")
    print("  - Zero-Knowledge AES-CBC Transit Encryption works.")
    print("  - Argon2id Password Hashing is active and stored in super_admin collection.")
    print("  - Dynamic JWT Authentications & Auth middleware are 100% correct.")
    print("======================================================================")

if __name__ == "__main__":
    verify()

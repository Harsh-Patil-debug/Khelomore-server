import os
import io
import jwt
import json
import uuid
import qrcode
import base64
import pyotp
import base64
from Crypto.Random import get_random_bytes
from datetime import datetime, timedelta, timezone
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad, pad
from .db_connection import db_main
from dotenv import load_dotenv
import random
from .email_handler import send_admin_otp_email, send_sms_otp, send_welcome_email
from bson.objectid import ObjectId
from typing import Tuple, Any, Dict, List
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import requests
from django.conf import settings
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

load_dotenv()

OGGY_CLOUDINARY_URLS = [
    "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1779079685/cafe_project/avatars/hbbjrire5vcjtb1r7kgt.jpg",
    "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1779079687/cafe_project/avatars/kqj23wukyvk44y1zh06h.jpg",
    "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1779079689/cafe_project/avatars/litsvrjoj1qeln81jrtk.jpg",
    "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1779079690/cafe_project/avatars/ffpsxkzvkweejhczbkrn.jpg",
    "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1779079692/cafe_project/avatars/lwt89w3uz7qcjflgzag6.jpg"
]

def get_oggy_avatar(identifier: str) -> str:
    if not identifier:
        return OGGY_CLOUDINARY_URLS[0]
    unique_str = str(identifier).lower()
    hash_val = 0
    for char in unique_str:
        hash_val = ord(char) + ((hash_val << 5) - hash_val)
        hash_val &= 0xFFFFFFFF
        if hash_val > 0x7FFFFFFF:
            hash_val -= 0x100000000
    char_index = abs(hash_val) % len(OGGY_CLOUDINARY_URLS)
    return OGGY_CLOUDINARY_URLS[char_index]

JWT_SECRET = os.getenv("JWT_SECRET", "")
if not JWT_SECRET:
    # SECURITY: never sign/verify JWTs with an empty key — that makes every token
    # (including super_admin tokens) trivially forgeable by anyone.
    raise RuntimeError("JWT_SECRET environment variable is not set.")
JWT_ALGORITHM         = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXP_DELTA_SECONDS = int(os.getenv("JWT_EXP_DELTA_SECONDS", "2592000"))
ENCRYPTION_KEY        = base64.b64decode(os.getenv("ENCRYPTION_KEY", ""))
IV                    = base64.b64decode(os.getenv("IV", ""))
IST = timezone(timedelta(hours=5, minutes=30))
MAX_OTP_ATTEMPTS = int(os.getenv("MAX_OTP_ATTEMPTS", "5"))

ph = PasswordHasher(
    time_cost=5,      # Number of iterations
    memory_cost=128 * 1024,  # Memory in KB (256 MB here)
    parallelism=2,    # Number of threads
    hash_len=32,      # Length of hash
    salt_len=16       # Length of salt
)

def verify_password(stored_hash, input_password):
    try:
        return ph.verify(stored_hash, input_password)
    except VerifyMismatchError:
        return False

def encrypt_data(plain_text: str, key: bytes) -> Tuple[str, str]:
    iv = get_random_bytes(16)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted_bytes = cipher.encrypt(pad(plain_text.encode('utf-8'), AES.block_size))
    return base64.b64encode(encrypted_bytes).decode('utf-8'), base64.b64encode(iv).decode('utf-8')

def encrypt_secret_key(plain_text: str, key: bytes) -> str:
    """
    Encrypts a TOTP secret with a fresh random IV per call (never reuse an IV across
    encryptions under CBC — that breaks semantic security). The IV is bundled with the
    ciphertext (same `ciphertext:iv` convention as encrypt_phone_field).
    """
    enc_data, iv_data = encrypt_data(plain_text, key)
    return f"{enc_data}:{iv_data}"

def decrypt_secret_key(encrypted_data: str) -> str:
    """Decrypts a TOTP secret produced by encrypt_secret_key using the module-level ENCRYPTION_KEY."""
    if ":" not in encrypted_data:
        raise ValueError("Invalid encrypted secret format: missing IV.")
    enc_part, iv_part = encrypted_data.split(":", 1)
    return decrypt_data(enc_part, iv_part)


def decrypt_data(encrypted_data: str, iv: str) -> str:
    try:
        iv_bytes        = base64.b64decode(iv)
        encrypted_bytes = base64.b64decode(encrypted_data)
        cipher          = AES.new(ENCRYPTION_KEY, AES.MODE_CBC, iv_bytes)
        decrypted_bytes = unpad(cipher.decrypt(encrypted_bytes), AES.block_size)
        return decrypted_bytes.decode("utf-8")
    except Exception as e:
        print(f"DECRYPTION FAIL: {str(e)}")
        raise ValueError(f"Decryption failed: {str(e)}")

def encrypt_phone_field(phone_raw: str) -> str:
    if not phone_raw:
        return ""
    enc_data, iv_data = encrypt_data(phone_raw, ENCRYPTION_KEY)
    return f"{enc_data}:{iv_data}"

def decrypt_phone_field(phone_stored: str) -> str:
    if not phone_stored:
        return ""
    if ":" not in phone_stored:
        return phone_stored
    try:
        parts = phone_stored.split(":")
        return decrypt_data(parts[0], parts[1])
    except Exception as e:
        print(f"PHONE DECRYPTION FAIL: {str(e)}")
        return phone_stored
    
_revoked_index_ensured = False

def _ensure_revoked_tokens_index():
    """Lazily creates a TTL index so revoked-token records self-expire (matches this
    codebase's existing pattern of setup-on-first-access rather than a migration step)."""
    global _revoked_index_ensured
    if not _revoked_index_ensured:
        try:
            db_main.revoked_tokens.create_index("expires_at", expireAfterSeconds=0)
        except Exception:
            pass
        _revoked_index_ensured = True

def generate_token(email: str) -> str:
    payload = {
        'email': email,
        'jti': uuid.uuid4().hex,
        'exp': datetime.now(IST) + timedelta(seconds=JWT_EXP_DELTA_SECONDS)
    }
    token = jwt.encode(payload, JWT_SECRET, JWT_ALGORITHM)
    return token

def revoke_token(token: str) -> None:
    """
    Invalidates a token server-side (used on logout) so a leaked/stolen token stops
    working immediately instead of remaining valid for its full JWT_EXP_DELTA_SECONDS
    lifetime. Safe to call with an already-expired or malformed token (no-op).
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM], options={"verify_exp": False})
    except Exception:
        return
    jti = payload.get('jti')
    if not jti:
        return
    exp_ts = payload.get('exp')
    expires_at = datetime.fromtimestamp(exp_ts, tz=timezone.utc) if exp_ts else (datetime.now(timezone.utc) + timedelta(days=31))
    _ensure_revoked_tokens_index()
    db_main.revoked_tokens.update_one(
        {"jti": jti},
        {"$set": {"jti": jti, "expires_at": expires_at}},
        upsert=True
    )

def verify_token(token: str) -> str:
    """Verifies JWT and returns the user's email if valid, otherwise raises exception."""
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    jti = payload.get('jti')
    # SECURITY: every token generate_token() issues includes a jti, used to check
    # revocation on logout. A token with no jti predates that (or was crafted some other
    # way) and can never be revoked — silently trusting it forever defeats the whole
    # point of server-side logout, so treat "no jti" as invalid rather than unrevocable.
    if not jti:
        raise jwt.InvalidTokenError("Token missing jti claim.")
    if db_main.revoked_tokens.find_one({"jti": jti}):
        raise jwt.InvalidTokenError("Token has been revoked.")
    return payload['email']
def generate_totp_uri(email: str, secret: str) -> str:
    decrypted_secret = decrypt_secret_key(secret)
    return pyotp.totp.TOTP(decrypted_secret).provisioning_uri(name=email, issuer_name="Bloomora")

def generate_qr_code(uri: str) -> str:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode('utf-8')
    






# ===============================================================================
# KheloMore Custom Auth Functions (email OTP mandatory for traditional auth)
# ===============================================================================

def get_user_collection(is_admin=False, role=""):
    if role == "super_admin":
        return db_main.super_admin
    if role == "website_user":
        return db_main.website_users
    if role == "admin":
        return db_main.admins
    return db_main.admins if is_admin else db_main.users

def khelomore_register(gamertag, email, password, iv, phone=None, is_admin=False, role=""):
    """Signup Step 1 - creates pending user, sends OTP, NO JWT yet."""
    try:
        dec_gamertag = decrypt_data(gamertag, iv).strip()
        dec_email    = decrypt_data(email, iv).strip().lower()
        dec_password = decrypt_data(password, iv)
        dec_phone    = decrypt_data(phone, iv).strip() if phone else ""
    except Exception as e:
        return {"error": f"Decryption failed: {str(e)}"}, 400

    coll = get_user_collection(is_admin, role)
    if (is_admin or role == "admin") and role != "super_admin":
        cafe_exists = db_main.cafes.find_one({"owner_email": dec_email, "is_deleted": {"$ne": True}})
        if not cafe_exists:
            return {"error": "This email is not authorized. Please contact the platform Super Admin to list your cafe first."}, 403

    if coll.find_one({"email": dec_email}):
        return {"error": "An account with this email already exists."}, 400

    password_hash = ph.hash(dec_password)
    otp_code      = str(random.randint(100000, 999999))
    otp_expiry    = datetime.now(IST) + timedelta(minutes=10)

    coll.insert_one({
        "gamertag":      dec_gamertag.upper().replace(" ", "_"),
        "email":         dec_email,
        "password_hash": password_hash,
        "phone":         encrypt_phone_field(dec_phone),
        "status":        "Pending",
        "otp_code":      otp_code,
        "otp_expiry":    otp_expiry,
        "xp":            0,
        "rank":          "Recruit PRO I",
        "createdAt":     datetime.now(IST),
        "role":          role if role else ("admin" if is_admin else "user"),
    })

    from .email_handler import send_otp_email
    send_otp_email(dec_email, otp_code, gamertag=dec_gamertag, purpose="signup")

    response_json = json.dumps({"message": "OTP sent to your email.", "email": dec_email})
    enc_resp, iv2 = encrypt_data(response_json, ENCRYPTION_KEY)
    return {"encrypted_response": enc_resp, "iv": iv2}, 200


def khelomore_login(email, password, iv, is_admin=False, role=""):
    """Login Step 1 - verifies credentials, sends OTP, NO JWT yet."""
    try:
        dec_email    = decrypt_data(email, iv).strip().lower()
        dec_password = decrypt_data(password, iv)
    except Exception as e:
        return {"error": f"Decryption failed: {str(e)}"}, 400

    coll = get_user_collection(is_admin, role)
    user = coll.find_one({"email": dec_email})
    if not user:
        return {"error": "Invalid email or password."}, 401

    if (is_admin or role == "admin") and role != "super_admin":
        cafe_exists = db_main.cafes.find_one({"owner_email": dec_email, "is_deleted": {"$ne": True}})
        if not cafe_exists:
            return {"error": "This account is not associated with any registered gaming cafe. Access denied."}, 403

    if user.get("status") == "Blocked":
        return {"error": "This account has been blocked."}, 403
    if not user.get("password_hash"):
        return {"error": "This account uses Google Sign-In."}, 400
    if not verify_password(user["password_hash"], dec_password):
        return {"error": "Invalid email or password."}, 401

    otp_code   = str(random.randint(100000, 999999))
    otp_expiry = datetime.now(IST) + timedelta(minutes=10)
    coll.update_one(
        {"_id": user["_id"]},
        {"$set": {"otp_code": otp_code, "otp_expiry": otp_expiry}, "$unset": {"otp_attempts": ""}}
    )

    gamertag = user.get("gamertag") or user.get("first_name", "PLAYER")
    from .email_handler import send_otp_email
    send_otp_email(dec_email, otp_code, gamertag=gamertag, purpose="login")

    response_json = json.dumps({"message": "OTP sent to your email.", "email": dec_email})
    enc_resp, iv2 = encrypt_data(response_json, ENCRYPTION_KEY)
    return {"encrypted_response": enc_resp, "iv": iv2}, 200


def khelomore_verify_otp(email, otp_code, iv, is_admin=False, role=""):
    """Step 2 (login + signup) - validates OTP, activates account, issues JWT."""
    try:
        dec_email = decrypt_data(email, iv).strip().lower()
        dec_otp   = decrypt_data(otp_code, iv).strip()
    except Exception as e:
        return {"error": f"Decryption failed: {str(e)}"}, 400

    coll = get_user_collection(is_admin, role)
    if (is_admin or role == "admin") and role != "super_admin":
        cafe_exists = db_main.cafes.find_one({"owner_email": dec_email, "is_deleted": {"$ne": True}})
        if not cafe_exists:
            return {"error": "This account is not associated with any active gaming cafe. Access denied."}, 403

    user = coll.find_one({"email": dec_email})
    if not user:
        return {"error": "Session not found. Please start again."}, 404

    stored_otp = user.get("otp_code")
    otp_exp    = user.get("otp_expiry")
    if not stored_otp or not otp_exp:
        return {"error": "No OTP request found."}, 400

    if otp_exp.tzinfo is None:
        otp_exp = otp_exp.replace(tzinfo=timezone.utc).astimezone(IST)
    if datetime.now(IST) > otp_exp:
        return {"error": "OTP has expired. Please request a new code."}, 400

    if stored_otp != dec_otp:
        # SECURITY: bound OTP guessing — a 6-digit code has only 1,000,000 possibilities,
        # so unlimited attempts against a single OTP would make it brute-forceable.
        # Invalidate the OTP outright after too many wrong guesses.
        attempts = int(user.get("otp_attempts", 0)) + 1
        if attempts >= MAX_OTP_ATTEMPTS:
            coll.update_one(
                {"_id": user["_id"]},
                {"$unset": {"otp_code": "", "otp_expiry": "", "otp_attempts": ""}}
            )
            return {"error": "Too many incorrect attempts. Please request a new code."}, 429
        coll.update_one({"_id": user["_id"]}, {"$set": {"otp_attempts": attempts}})
        return {"error": "Invalid verification code."}, 400

    is_new = user.get("status") == "Pending"
    coll.update_one(
        {"_id": user["_id"]},
        {"$set": {"status": "Active"}, "$unset": {"otp_code": "", "otp_expiry": "", "otp_attempts": ""}}
    )

    if is_new:
        gamertag = user.get("gamertag") or user.get("first_name", "PLAYER")
        try:
            from .email_handler import send_welcome_email
            send_welcome_email(dec_email, gamertag)
        except Exception:
            pass

    token = generate_token(dec_email)
    response_data = {
        "token":   token,
        "message": "Verification successful",
        "user": {
            "id":             str(user["_id"]),
            "email":          dec_email,
            "gamertag":       user.get("gamertag") or user.get("first_name", "PLAYER"),
            "rank":           user.get("rank", "Recruit PRO I"),
            "xp":             user.get("xp", 0),
            "auth_provider":  user.get("auth_provider", "traditional"),
            "total_playtime": user.get("total_playtime", 140),
            "role":           user.get("role", role if role else ("admin" if is_admin else "user")),
            "phone":          decrypt_phone_field(user.get("phone", "")),
        }
    }

    def _s(o):
        if isinstance(o, ObjectId): return str(o)
        if isinstance(o, datetime): return o.isoformat()
        raise TypeError

    enc_resp, iv2 = encrypt_data(json.dumps(response_data, default=_s), ENCRYPTION_KEY)
    return {"encrypted_response": enc_resp, "iv": iv2}, 200


def khelomore_google_auth(gmail, gamertag, iv, is_admin=False, role=""):
    """Google Sign-In: find or create user, return JWT DIRECTLY (no OTP needed)."""
    try:
        dec_email    = decrypt_data(gmail, iv).strip().lower()
        dec_gamertag = decrypt_data(gamertag, iv).strip()
    except Exception as e:
        return {"error": f"Decryption failed: {str(e)}"}, 400

    coll = get_user_collection(is_admin, role)
    if is_admin:
        cafe_exists = db_main.cafes.find_one({"owner_email": dec_email, "is_deleted": {"$ne": True}})
        if not cafe_exists:
            return {"error": "This email is not authorized. Please contact the platform Super Admin to list your cafe first."}, 403

    user = coll.find_one({"email": dec_email})
    if user and user.get("status") == "Blocked":
        return {"error": "This account has been blocked."}, 403

    is_new = not user
    if is_new:
        result = coll.insert_one({
            "gamertag":      dec_gamertag.upper().replace(" ", "_"),
            "email":         dec_email,
            "auth_provider": "google",
            "status":        "Active",
            "xp":            150,
            "rank":          "Recruit PRO I",
            "createdAt":     datetime.now(IST),
            "role":          role if role else ("admin" if is_admin else "user"),
        })
        user = coll.find_one({"_id": result.inserted_id})
        try:
            from .email_handler import send_welcome_email
            send_welcome_email(dec_email, dec_gamertag)
        except Exception:
            pass

    token = generate_token(dec_email)
    response_data = {
        "token":   token,
        "message": "Google login successful",
        "is_new":  is_new,
        "user": {
            "id":             str(user["_id"]),
            "email":          dec_email,
            "gamertag":       user.get("gamertag") or dec_gamertag,
            "rank":           user.get("rank", "Recruit PRO I"),
            "xp":             user.get("xp", 0),
            "auth_provider":  user.get("auth_provider", "google"),
            "total_playtime": user.get("total_playtime", 140),
            "role":           user.get("role", "admin" if is_admin else "user"),
            "phone":          decrypt_phone_field(user.get("phone", "")),
        }
    }

    def _s(o):
        if isinstance(o, ObjectId): return str(o)
        if isinstance(o, datetime): return o.isoformat()
        raise TypeError

    enc_resp, iv2 = encrypt_data(json.dumps(response_data, default=_s), ENCRYPTION_KEY)
    return {"encrypted_response": enc_resp, "iv": iv2}, 200


def khelomore_google_auth_code_verify(code: str, is_admin=False, role=""):
    """
    Exchanges Google Auth Code for ID Token, verifies it, and returns a Khelomore session.
    """
    try:
        token_url = "https://oauth2.googleapis.com/token"
        
        # 1. Exchange code for tokens
        data = {
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": f"{settings.BACKEND_URL}/api/v1/main/auth/google/callback/",
            "grant_type": "authorization_code",
        }

        token_response = requests.post(token_url, data=data)
        if token_response.status_code != 200:
            print(f"GOOGLE TOKEN EXCHANGE FAILED: {token_response.text}")
            return {"error": "Google token exchange failed"}, 400

        token_res = token_response.json()
        id_token_sent = token_res.get("id_token")

        if not id_token_sent:
            return {"error": "No ID token received from Google"}, 400

        # 2. Verify the ID token
        idinfo = id_token.verify_oauth2_token(
            id_token_sent, 
            google_requests.Request(), 
            settings.GOOGLE_CLIENT_ID
        )

        email = idinfo['email'].strip().lower()
        first_name = idinfo.get('given_name', 'Player')
        
        # derive a gamertag if new
        derived_gamertag = first_name.upper().replace(" ", "_")

        # 3. Check MongoDB: Get or Create User
        coll = get_user_collection(is_admin, role)
        if is_admin:
            cafe_exists = db_main.cafes.find_one({"owner_email": email, "is_deleted": {"$ne": True}})
            if not cafe_exists:
                return {"error": "This email is not authorized. Please contact the platform Super Admin to list your cafe first."}, 403

        user = coll.find_one({"email": email})

        if user and user.get("status") == "Blocked":
            return {"error": "This account has been blocked."}, 403

        is_new = not user
        if is_new:
            result = coll.insert_one({
                "gamertag":      derived_gamertag,
                "email":         email,
                "auth_provider": "google",
                "status":        "Active",
                "xp":            150,
                "rank":          "Recruit PRO I",
                "createdAt":     datetime.now(IST),
                "role":          role if role else ("admin" if is_admin else "user"),
            })
            user = coll.find_one({"_id": result.inserted_id})
            try:
                send_welcome_email(email, derived_gamertag)
            except Exception as e:
                print(f"WELCOME EMAIL ERROR: {str(e)}")

        token = generate_token(email)
        response_data = {
            "token":   token,
            "message": "Google login successful",
            "is_new":  is_new,
            "user": {
                "id":             str(user["_id"]),
                "email":          email,
                "gamertag":       user.get("gamertag") or derived_gamertag,
                "rank":           user.get("rank", "Recruit PRO I"),
                "xp":             user.get("xp", 0),
                "auth_provider":  user.get("auth_provider", "google"),
                "total_playtime": user.get("total_playtime", 140),
                "role":           user.get("role", "admin" if is_admin else "user"),
                "phone":          decrypt_phone_field(user.get("phone", "")),
            }
        }

        def _s(o):
            if isinstance(o, ObjectId): return str(o)
            if isinstance(o, datetime): return o.isoformat()
            raise TypeError

        enc_resp, iv2 = encrypt_data(json.dumps(response_data, default=_s), ENCRYPTION_KEY)
        return {"encrypted_response": enc_resp, "iv": iv2}, 200

    except Exception as e:
        print(f"GOOGLE AUTH ERROR: {str(e)}")
        return {"error": f"Google login failed: {str(e)}"}, 500


def khelomore_update_phone(email, phone_encrypted, iv):
    """Updates a user's phone number securely."""
    try:
        dec_phone = decrypt_data(phone_encrypted, iv).strip()
    except Exception as e:
        return {"error": f"Decryption failed: {str(e)}"}, 400

    if not dec_phone:
        return {"error": "Phone number is required."}, 400

    coll = db_main.website_users
    user = coll.find_one({"email": email})
    if not user:
        coll = db_main.users
        user = coll.find_one({"email": email})
    if not user:
        return {"error": "User not found."}, 404

    coll.update_one(
        {"_id": user["_id"]},
        {"$set": {"phone": encrypt_phone_field(dec_phone)}}
    )

    # Fetch updated user
    updated_user = coll.find_one({"_id": user["_id"]})

    response_data = {
        "message": "Phone number updated successfully",
        "user": {
            "id":             str(updated_user["_id"]),
            "email":          updated_user.get("email"),
            "gamertag":       updated_user.get("gamertag"),
            "rank":           updated_user.get("rank", "Recruit PRO I"),
            "xp":             updated_user.get("xp", 0),
            "auth_provider":  updated_user.get("auth_provider", "google"),
            "total_playtime": updated_user.get("total_playtime", 140),
            "role":           updated_user.get("role", "user"),
            "phone":          decrypt_phone_field(updated_user.get("phone", "")),
        }
    }

    def _s(o):
        if isinstance(o, ObjectId): return str(o)
        if isinstance(o, datetime): return o.isoformat()
        raise TypeError

    enc_resp, iv2 = encrypt_data(json.dumps(response_data, default=_s), ENCRYPTION_KEY)
    return {"encrypted_response": enc_resp, "iv": iv2}, 200

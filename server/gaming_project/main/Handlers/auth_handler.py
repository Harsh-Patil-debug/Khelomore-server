import os
import io
import jwt
import json
import uuid
import qrcode
import base64
import pyotp
import base64
import hmac
import hashlib
import cloudinary
import cloudinary.uploader
from Crypto.Random import get_random_bytes
from datetime import datetime, timedelta, timezone
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad, pad
from .db_connection import db_main
from . import input_validation
from .upload_validation import validate_image_upload
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
# Shorter-lived sessions for the cafe-owner and super-admin panels (playhub-command /
# cafe-command-center) — these control real money/cafe data, so they shouldn't stay
# logged in for the same 30 days as a casual gamer-app session.
JWT_ADMIN_EXP_DELTA_SECONDS = int(os.getenv("JWT_ADMIN_EXP_DELTA_SECONDS", "86400"))
ENCRYPTION_KEY        = base64.b64decode(os.getenv("ENCRYPTION_KEY", ""))
IV                    = base64.b64decode(os.getenv("IV", ""))
IST = timezone(timedelta(hours=5, minutes=30))
MAX_OTP_ATTEMPTS = int(os.getenv("MAX_OTP_ATTEMPTS", "5"))
# Super admin login/signup OTPs expire much faster than other roles — the platform's most
# sensitive login surface shouldn't leave a valid code sitting in an inbox for 10 minutes.
SUPER_ADMIN_OTP_EXPIRY_MINUTES = int(os.getenv("SUPER_ADMIN_OTP_EXPIRY_MINUTES", "2"))
OTP_EXPIRY_MINUTES = int(os.getenv("OTP_EXPIRY_MINUTES", "10"))


def get_otp_expiry_minutes(role: str = "") -> int:
    return SUPER_ADMIN_OTP_EXPIRY_MINUTES if role == "super_admin" else OTP_EXPIRY_MINUTES
MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "5"))
LOGIN_LOCKOUT_MINUTES = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "15"))
OTP_RESEND_COOLDOWN_SECONDS = int(os.getenv("OTP_RESEND_COOLDOWN_SECONDS", "45"))

ph = PasswordHasher(
    # OWASP's low-memory Argon2id recommendation (m=19 MiB, t=2, p=1) — the previous
    # 128 MiB/time_cost=5/parallelism=2 config was tuned for a beefy machine and was
    # crashing Render's free-tier instance (512 MB RAM, throttled CPU): a single login's
    # hash verify ran long enough to hit gunicorn's WORKER TIMEOUT, and the worker got
    # SIGKILL'd (confirmed live in Render logs). Still meets OWASP's minimum bar for
    # Argon2id, just sized for the hardware this actually runs on.
    time_cost=2,
    memory_cost=19 * 1024,  # 19 MiB
    parallelism=1,
    hash_len=32,
    salt_len=16
)

def verify_password(stored_hash, input_password):
    try:
        return ph.verify(stored_hash, input_password)
    except VerifyMismatchError:
        return False

def hash_otp(otp: str) -> str:
    """Hashes an OTP for storage. Deliberately HMAC-SHA256, not Argon2id like passwords —
    Argon2's memory/CPU cost is the point for passwords (resist offline brute-force of a
    high-entropy secret an attacker gets unlimited guesses at), but this Render instance
    already had to have its Argon2 params shrunk once to stop hitting gunicorn's worker
    timeout on the free tier (see the `ph = PasswordHasher(...)` comment above) — adding
    Argon2 calls to every OTP verification too would make that worse for no real benefit.
    A 6-digit OTP's actual protection is its short expiry and MAX_OTP_ATTEMPTS lockout,
    both already enforced before this hash is ever compared; hashing storage is
    defense-in-depth against a DB leak, not the primary defense, so a fast, correct HMAC
    is the right tool here."""
    return hmac.new(ENCRYPTION_KEY, otp.encode("utf-8"), hashlib.sha256).hexdigest()

def verify_otp_hash(stored_hash: str, input_otp: str) -> bool:
    if not stored_hash or not input_otp:
        return False
    return hmac.compare_digest(hash_otp(input_otp), stored_hash)

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

def generate_token(email: str, is_admin: bool = False, role: str = "") -> str:
    is_panel_account = role in ("admin", "super_admin") or is_admin
    exp_seconds = JWT_ADMIN_EXP_DELTA_SECONDS if is_panel_account else JWT_EXP_DELTA_SECONDS
    payload = {
        'email': email,
        'jti': uuid.uuid4().hex,
        'exp': datetime.now(IST) + timedelta(seconds=exp_seconds)
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
# BookMyConsole Custom Auth Functions (email OTP mandatory for traditional auth)
# ===============================================================================

def get_user_collection(is_admin=False, role=""):
    if role == "super_admin":
        return db_main.super_admin
    if role == "website_user":
        return db_main.website_users
    if role == "admin":
        return db_main.admins
    return db_main.admins if is_admin else db_main.users

def bookmyconsole_register(gamertag, email, password, iv, phone=None, is_admin=False, role="", razorpay_password=None):
    """Signup Step 1 - creates pending user, sends OTP, NO JWT yet."""
    try:
        dec_gamertag = decrypt_data(gamertag, iv).strip()
        dec_email    = decrypt_data(email, iv).strip().lower()
        dec_password = decrypt_data(password, iv)
        dec_phone    = decrypt_data(phone, iv).strip() if phone else ""
        # A cafe owner (role="admin") sets a SEPARATE password here that later gates access
        # to entering/viewing their Razorpay credentials in cafe-command-center — distinct
        # from their login password so a compromised login session alone can't unlock
        # payment-routing settings.
        is_cafe_owner_signup = (is_admin or role == "admin") and role != "super_admin"
        dec_razorpay_password = decrypt_data(razorpay_password, iv) if razorpay_password else ""
    except Exception as e:
        return {"error": f"Decryption failed: {str(e)}"}, 400

    # SECURITY: every one of these checks already exists as a client-side HTML attribute
    # somewhere in the four frontends — but client-side validation is bypassable by anyone
    # calling this endpoint directly. These are the real, unbypassable checks.
    error = (
        input_validation.validate_text(dec_gamertag, "Gamertag", max_len=40)
        or input_validation.validate_email(dec_email)
        or input_validation.validate_password_strength(dec_password)
        or input_validation.validate_phone(dec_phone, required=False)
    )
    if error:
        return {"error": error}, 400
    if is_cafe_owner_signup:
        if not dec_razorpay_password:
            return {"error": "A Razorpay password is required to protect your payment settings."}, 400
        razorpay_password_error = input_validation.validate_password_strength(dec_razorpay_password)
        if razorpay_password_error:
            return {"error": f"Razorpay password: {razorpay_password_error}"}, 400
        if dec_razorpay_password == dec_password:
            return {"error": "Your Razorpay password must be different from your login password."}, 400

    coll = get_user_collection(is_admin, role)
    if is_cafe_owner_signup:
        cafe_exists = db_main.cafes.find_one({"owner_email": dec_email, "is_deleted": {"$ne": True}})
        if not cafe_exists:
            return {"error": "This email is not authorized. Please contact the platform Super Admin to list your cafe first."}, 403

    if coll.find_one({"email": dec_email}):
        return {"error": "An account with this email already exists."}, 400

    password_hash = ph.hash(dec_password)
    otp_code      = str(random.randint(100000, 999999))
    otp_expiry    = datetime.now(IST) + timedelta(minutes=get_otp_expiry_minutes(role))

    new_user_doc = {
        "gamertag":      dec_gamertag.upper().replace(" ", "_"),
        "email":         dec_email,
        "password_hash": password_hash,
        "phone":         encrypt_phone_field(dec_phone),
        "status":        "Pending",
        "otp_code":      hash_otp(otp_code),
        "otp_expiry":    otp_expiry,
        "xp":            0,
        "rank":          "Recruit PRO I",
        "createdAt":     datetime.now(IST),
        "role":          role if role else ("admin" if is_admin else "user"),
    }
    if is_cafe_owner_signup:
        new_user_doc["razorpay_password_hash"] = ph.hash(dec_razorpay_password)

    coll.insert_one(new_user_doc)

    from .email_handler import send_otp_email
    send_otp_email(dec_email, otp_code, gamertag=dec_gamertag, purpose="signup")

    response_json = json.dumps({"message": "OTP sent to your email.", "email": dec_email})
    enc_resp, iv2 = encrypt_data(response_json, ENCRYPTION_KEY)
    return {"encrypted_response": enc_resp, "iv": iv2}, 200


def bookmyconsole_login(email, password, iv, is_admin=False, role=""):
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

    # SECURITY: bound password-guessing against a known email. Without this, an attacker
    # could try unlimited passwords for one account — the only thing stopping them
    # otherwise is the generic per-IP request throttle, which is far too loose in practice
    # to prevent this on its own.
    locked_until = user.get("login_locked_until")
    if locked_until:
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc).astimezone(IST)
        if datetime.now(IST) < locked_until:
            return {"error": "Too many failed login attempts. Please try again later."}, 429

    if not user.get("password_hash"):
        return {"error": "This account uses Google Sign-In."}, 400
    if not verify_password(user["password_hash"], dec_password):
        attempts = int(user.get("login_attempts", 0)) + 1
        update_fields: dict = {"login_attempts": attempts}
        if attempts >= MAX_LOGIN_ATTEMPTS:
            update_fields["login_locked_until"] = datetime.now(IST) + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)
        coll.update_one({"_id": user["_id"]}, {"$set": update_fields})
        return {"error": "Invalid email or password."}, 401

    if user.get("login_attempts") or user.get("login_locked_until"):
        coll.update_one({"_id": user["_id"]}, {"$unset": {"login_attempts": "", "login_locked_until": ""}})

    otp_code   = str(random.randint(100000, 999999))
    otp_expiry = datetime.now(IST) + timedelta(minutes=get_otp_expiry_minutes(role))
    coll.update_one(
        {"_id": user["_id"]},
        {"$set": {"otp_code": hash_otp(otp_code), "otp_expiry": otp_expiry}, "$unset": {"otp_attempts": ""}}
    )

    gamertag = user.get("gamertag") or user.get("first_name", "PLAYER")
    from .email_handler import send_otp_email
    send_otp_email(dec_email, otp_code, gamertag=gamertag, purpose="login")

    response_json = json.dumps({"message": "OTP sent to your email.", "email": dec_email})
    enc_resp, iv2 = encrypt_data(response_json, ENCRYPTION_KEY)
    return {"encrypted_response": enc_resp, "iv": iv2}, 200


def bookmyconsole_verify_otp(email, otp_code, iv, is_admin=False, role=""):
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

    if not verify_otp_hash(stored_otp, dec_otp):
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

    token = generate_token(dec_email, is_admin=is_admin, role=role)
    response_data = {
        "token":   token,
        "message": "Verification successful",
        "user": {
            "id":             str(user["_id"]),
            "email":          dec_email,
            "gamertag":       user.get("gamertag") or user.get("first_name", "PLAYER"),
            "full_name":      user.get("full_name", ""),
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


MAX_PASSWORD_RESET_ATTEMPTS = MAX_OTP_ATTEMPTS


def bookmyconsole_forgot_password(email, iv, is_admin=False, role=""):
    """
    Step 1 of password reset: if an account exists for this email, email it a reset OTP.

    SECURITY: always returns the same generic message regardless of whether the account
    exists, is Google-only (no password to reset), or is blocked — revealing any of that
    here would let an attacker enumerate registered emails or account types. Only the
    actual reset step (bookmyconsole_reset_password) needs the OTP to have been genuinely
    sent, which it silently isn't for any of those cases.
    """
    try:
        dec_email = decrypt_data(email, iv).strip().lower()
    except Exception as e:
        return {"error": f"Decryption failed: {str(e)}"}, 400

    error = input_validation.validate_email(dec_email)
    if error:
        return {"error": error}, 400

    generic_message = {"message": "If an account exists for this email, a password reset code has been sent."}

    coll = get_user_collection(is_admin, role)
    user = coll.find_one({"email": dec_email})

    def _respond():
        enc_resp, iv2 = encrypt_data(json.dumps(generic_message), ENCRYPTION_KEY)
        return {"encrypted_response": enc_resp, "iv": iv2}, 200

    if not user or user.get("status") == "Blocked" or not user.get("password_hash"):
        return _respond()

    # Same resend-cooldown protection as OTP resend — without it, an attacker with email
    # access could otherwise trigger unlimited fresh codes to dodge the reset-attempt cap.
    prev_expiry = user.get("reset_otp_expiry")
    if prev_expiry:
        if prev_expiry.tzinfo is None:
            prev_expiry = prev_expiry.replace(tzinfo=timezone.utc).astimezone(IST)
        last_sent = prev_expiry - timedelta(minutes=get_otp_expiry_minutes(role))
        elapsed = (datetime.now(IST) - last_sent).total_seconds()
        if elapsed < OTP_RESEND_COOLDOWN_SECONDS:
            return _respond()

    otp_code = str(random.randint(100000, 999999))
    otp_expiry = datetime.now(IST) + timedelta(minutes=get_otp_expiry_minutes(role))
    coll.update_one(
        {"_id": user["_id"]},
        {"$set": {"reset_otp_code": hash_otp(otp_code), "reset_otp_expiry": otp_expiry},
         "$unset": {"reset_otp_attempts": ""}}
    )

    gamertag = user.get("gamertag") or user.get("first_name", "PLAYER")
    from .email_handler import send_otp_email
    send_otp_email(dec_email, otp_code, gamertag=gamertag, purpose="password_reset")

    return _respond()


def bookmyconsole_reset_password(email, otp_code, new_password, iv, is_admin=False, role=""):
    """Step 2 of password reset: verify the reset OTP and set a new password."""
    try:
        dec_email = decrypt_data(email, iv).strip().lower()
        dec_otp = decrypt_data(otp_code, iv).strip()
        dec_new_password = decrypt_data(new_password, iv)
    except Exception as e:
        return {"error": f"Decryption failed: {str(e)}"}, 400

    password_error = input_validation.validate_password_strength(dec_new_password)
    if password_error:
        return {"error": password_error}, 400

    coll = get_user_collection(is_admin, role)
    user = coll.find_one({"email": dec_email})
    # Same message whether the account doesn't exist or simply has no reset request
    # pending — don't help an attacker distinguish the two.
    invalid_response = {"error": "Invalid or expired reset code. Please request a new one."}, 400
    if not user:
        return invalid_response

    stored_otp = user.get("reset_otp_code")
    otp_exp = user.get("reset_otp_expiry")
    if not stored_otp or not otp_exp:
        return invalid_response

    if otp_exp.tzinfo is None:
        otp_exp = otp_exp.replace(tzinfo=timezone.utc).astimezone(IST)
    if datetime.now(IST) > otp_exp:
        return {"error": "Reset code has expired. Please request a new one."}, 400

    if not verify_otp_hash(stored_otp, dec_otp):
        # SECURITY: bound OTP guessing exactly like login/signup OTPs — a 6-digit code
        # has only 1,000,000 possibilities, so unlimited attempts would make it
        # brute-forceable well within the code's expiry window.
        attempts = int(user.get("reset_otp_attempts", 0)) + 1
        if attempts >= MAX_PASSWORD_RESET_ATTEMPTS:
            coll.update_one(
                {"_id": user["_id"]},
                {"$unset": {"reset_otp_code": "", "reset_otp_expiry": "", "reset_otp_attempts": ""}}
            )
            return {"error": "Too many incorrect attempts. Please request a new code."}, 429
        coll.update_one({"_id": user["_id"]}, {"$set": {"reset_otp_attempts": attempts}})
        return {"error": "Invalid reset code."}, 400

    new_password_hash = ph.hash(dec_new_password)
    coll.update_one(
        {"_id": user["_id"]},
        {
            "$set": {"password_hash": new_password_hash},
            # Clearing login_attempts/login_locked_until too: a successful password reset
            # is stronger proof of ownership than a login OTP ever was, so any existing
            # login lockout from earlier guessing shouldn't outlive it.
            "$unset": {
                "reset_otp_code": "", "reset_otp_expiry": "", "reset_otp_attempts": "",
                "login_attempts": "", "login_locked_until": "",
            },
        },
    )

    response_data = {"message": "Password reset successfully. Please log in with your new password."}
    enc_resp, iv2 = encrypt_data(json.dumps(response_data), ENCRYPTION_KEY)
    return {"encrypted_response": enc_resp, "iv": iv2}, 200


def bookmyconsole_google_auth_code_verify(code: str, is_admin=False, role=""):
    """
    Exchanges Google Auth Code for ID Token, verifies it, and returns a BookMyConsole session.
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
        # Google's ID token carries the real display name ("Harsh Patil") separately from
        # given_name ("Harsh") — capture it so the frontend can show the person's actual
        # name instead of the ALL_CAPS gamertag or falling back to their email address.
        full_name = (idinfo.get('name') or '').strip()

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
                "full_name":     full_name,
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
        elif full_name and user.get("full_name") != full_name:
            # Backfills accounts created before full_name existed, and keeps it in sync if
            # the person's Google display name changes — harmless to refresh on every login.
            coll.update_one({"_id": user["_id"]}, {"$set": {"full_name": full_name}})
            user["full_name"] = full_name

        token = generate_token(email, is_admin=is_admin, role=role)
        response_data = {
            "token":   token,
            "message": "Google login successful",
            "is_new":  is_new,
            "user": {
                "id":             str(user["_id"]),
                "email":          email,
                "gamertag":       user.get("gamertag") or derived_gamertag,
                "full_name":      user.get("full_name") or full_name,
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


def bookmyconsole_update_phone(email, phone_encrypted, iv, is_admin=False, role=""):
    """Updates a user's phone number securely."""
    try:
        dec_phone = decrypt_data(phone_encrypted, iv).strip()
    except Exception as e:
        return {"error": f"Decryption failed: {str(e)}"}, 400

    error = input_validation.validate_phone(dec_phone, required=True)
    if error:
        return {"error": error}, 400

    # Route to the caller's own collection like every other auth handler does (see
    # get_user_collection) instead of guessing by trying website_users then users — that
    # guess silently wrote to the wrong document whenever the same email existed in both
    # collections (e.g. someone with both a mobile account and a website account).
    coll = get_user_collection(is_admin, role)
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
            "full_name":      updated_user.get("full_name", ""),
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


PROFILE_AVATAR_IDS = {"cyber_ghost", "neon_shadow", "alpha_recon", "glitch_phantom"}


def bookmyconsole_update_profile(email, updates: dict, is_admin=False, role=""):
    """Updates a user's editable profile fields (gamertag, city, gamer_id, avatar_id).
    Previously these all lived in AsyncStorage only on the device — a fresh install or a
    second device never saw them, and a re-login would silently revert to whatever the
    server still had (nothing). This is the real, server-persisted version."""
    coll = get_user_collection(is_admin, role)
    user = coll.find_one({"email": email})
    if not user:
        return {"error": "User not found."}, 404

    set_fields = {}

    if "gamertag" in updates:
        gamertag = str(updates["gamertag"]).strip()
        if not gamertag:
            return {"error": "Gamertag cannot be empty."}, 400
        if len(gamertag) > 40:
            return {"error": "Gamertag must be at most 40 characters."}, 400
        set_fields["gamertag"] = gamertag

    if "city" in updates:
        set_fields["city"] = str(updates["city"]).strip()[:60]

    if "gamer_id" in updates:
        set_fields["gamer_id"] = str(updates["gamer_id"]).strip()[:40]

    if "avatar_id" in updates:
        avatar_id = str(updates["avatar_id"]).strip()
        if avatar_id not in PROFILE_AVATAR_IDS:
            return {"error": f"Invalid avatar_id. Must be one of: {', '.join(sorted(PROFILE_AVATAR_IDS))}."}, 400
        set_fields["avatar_id"] = avatar_id

    if not set_fields:
        return {"error": "No valid profile fields to update."}, 400

    coll.update_one({"_id": user["_id"]}, {"$set": set_fields})
    updated_user = coll.find_one({"_id": user["_id"]})

    return {
        "status": "success",
        "user": {
            "id":         str(updated_user["_id"]),
            "email":      updated_user.get("email"),
            "gamertag":   updated_user.get("gamertag"),
            "city":       updated_user.get("city", ""),
            "gamer_id":   updated_user.get("gamer_id", ""),
            "avatar_id":  updated_user.get("avatar_id", "cyber_ghost"),
            "avatar_url": updated_user.get("avatar_url", ""),
        },
    }, 200


def bookmyconsole_upload_avatar(email, uploaded_file, is_admin=False, role=""):
    """Uploads a user's profile picture to Cloudinary and stores the resulting secure_url
    on their account. Mirrors the same validate-then-upload pattern used for cafe images
    (cafes.py) so avatars go through the same file-type/size checks."""
    if uploaded_file is None:
        return {"error": "No image file provided."}, 400

    validation_error = validate_image_upload(uploaded_file)
    if validation_error:
        return {"error": validation_error}, 400

    coll = get_user_collection(is_admin, role)
    user = coll.find_one({"email": email})
    if not user:
        return {"error": "User not found."}, 404

    try:
        upload_result = cloudinary.uploader.upload(uploaded_file, folder="bookmyconsole/avatars")
        avatar_url = upload_result.get("secure_url")
    except Exception as e:
        return {"error": f"Image upload failed: {e}"}, 500

    if not avatar_url:
        return {"error": "Image upload failed."}, 500

    coll.update_one({"_id": user["_id"]}, {"$set": {"avatar_url": avatar_url}})

    return {"status": "success", "avatar_url": avatar_url}, 200


def bookmyconsole_delete_account(email, is_admin=False, role=""):
    """Permanently deletes a user's account and personal data — required by Google Play
    for any app that supports account creation (there was previously no way for a user
    to do this at all). The account document itself is hard-deleted. Bookings and
    tournament registrations are anonymized rather than hard-deleted: a cafe owner's own
    revenue/attendance records shouldn't disappear because a customer deleted their
    account, but the deleted user's personal identifiers (name, phone, email, gamer IDs)
    must not remain attached to them."""
    coll = get_user_collection(is_admin, role)
    user = coll.find_one({"email": email})
    if not user:
        return {"error": "User not found."}, 404

    anon_email = f"deleted-{user['_id']}@bookmyconsole.deleted"

    db_main.bookings.update_many(
        {"user_email": email},
        {"$set": {"user_name": "Deleted User", "user_phone": "", "user_email": anon_email}},
    )
    db_main.registrations.update_many(
        {"user_email": email},
        {"$set": {"user_email": anon_email, "gamer_ids": []}},
    )
    db_main.push_tokens.delete_one({"user_email": email})
    db_main.favorites.delete_many({"user_email": email})
    coll.delete_one({"_id": user["_id"]})

    return {"status": "success", "message": "Account and personal data deleted."}, 200

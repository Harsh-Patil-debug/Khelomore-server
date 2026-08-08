"""
BookMyConsole Gaming Hub — API Views
─────────────────────────────────────────────────────────────────────────────
All views are thin wrappers. Business logic lives exclusively in Handlers/.
Each View calls a handler function and returns the result as a DRF Response.
─────────────────────────────────────────────────────────────────────────────
"""

from django.shortcuts import redirect
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.throttling import ScopedRateThrottle
from .Handlers import status_check, db_check, cafes, tournaments, bookings, rigs, payments, auth_handler, bookings_handler, auth_middleware, favorites, sessions, offers, users, partner_applications, subscriptions, notifications, support


# ── Status ─────────────────────────────────────────────────────────────────────

class StatusCheckView(APIView):
    """GET /status/ — Server health check (public)"""
    def get(self, request):
        response = status_check.status_check()
        return Response(response)


# ── Database ───────────────────────────────────────────────────────────────────

class DbCheckView(APIView):
    """GET /db/ — MongoDB read/write connectivity check (Super Admin only)"""
    def get(self, request):
        # SECURITY: this performs a live write/delete against the DB and can leak internal
        # infra details (hostnames, driver errors) via the raw exception message — not
        # something to leave open to the public internet.
        _, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        response = db_check.db_check()
        return Response(response)


from rest_framework.parsers import MultiPartParser, FormParser

# ── Cafes ──────────────────────────────────────────────────────────────────────

class CafeListCreateView(APIView):
    """GET /cafes/ — List/seed cafes, POST /cafes/ — Create a new cafe with image upload"""
    parser_classes = (MultiPartParser, FormParser)

    def get(self, request):
        latitude = request.query_params.get("latitude")
        longitude = request.query_params.get("longitude")
        include_deleted = request.query_params.get("include_deleted") == "true"

        if include_deleted:
            # SECURITY: soft-deleted cafes carry owner PII (email/phone/address) —
            # only a super admin may view them.
            _, error_response = auth_middleware.authenticate_super_admin_request(request)
            if error_response:
                return error_response

        response = cafes.get_cafes_handler(latitude=latitude, longitude=longitude, include_deleted=include_deleted)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(response, status=status.HTTP_200_OK)

    def post(self, request):
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        response = cafes.create_cafe_handler(request.data, request.FILES)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_201_CREATED)


class CafeMyListView(APIView):
    """
    GET /cafes/my/ — List cafes owned by the authenticated admin.

    SECURITY: this must NEVER opportunistically check authenticate_super_admin_request —
    auth cookies are set with no Domain restriction and SameSite=None, so a super_admin
    cookie left over from a previous playhub-command session on the same browser rides
    along with cafe-command-center requests to this same backend host. This view used to
    check super-admin auth first and return every cafe in that case, which meant any owner
    who also happened to have (or had ever logged into) a super_admin account would see
    every other owner's cafes in their own "my cafes" list — playhub-command doesn't even
    call this endpoint, so that branch served no legitimate purpose. Always scope strictly
    to the authenticated owner's own email.
    """
    def get(self, request):
        email, error_response = auth_middleware.authenticate_request(request)
        if error_response:
            print("[DEBUG CafeMyListView] Auth failed:", error_response.data)
            return error_response

        response = cafes.get_my_cafes_handler(email, is_super_admin=False)
        print(f"[DEBUG CafeMyListView] get_my_cafes_handler response: {response}")
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(response, status=status.HTTP_200_OK)


class CafeParseMapsUrlView(APIView):
    """
    GET /cafes/parse-maps-url/?url=... — Resolves and parses a Google Maps link (CORS safe).
    Public/unauthenticated — used by both playhub-command's "Add Gaming Cafe" form and the
    public "Partner Application" form on gaming-cafe-connect, which has no admin session to
    authenticate with. Safe to expose: it's a stateless, read-only utility (no DB access),
    and parse_google_maps_url_handler only ever follows a redirect when the URL's hostname
    is actually Google's own shortener domain — see the SECURITY comment there. Rate-limited
    via ScopedRateThrottle below since it makes an outbound network call per request.
    """
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "geo"

    def get(self, request):
        url = request.query_params.get("url")
        if not url:
            return Response({"status": "error", "message": "Missing 'url' parameter"}, status=status.HTTP_400_BAD_REQUEST)

        response = cafes.parse_google_maps_url_handler(url)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)



# ── Auth ───────────────────────────────────────────────────────────────────────

def check_is_admin(request):
    # SECURITY: never log the raw token (request-supplied or expected) — ADMIN_TOKEN is a
    # bearer credential and printing it turns every server log line into a live secret leak.
    auth_header = request.headers.get('Authorization') or request.META.get('HTTP_AUTHORIZATION')
    expected_token = getattr(settings, 'ADMIN_TOKEN', '')
    if auth_header and auth_header.startswith('Bearer '):
        parts = auth_header.split(' ')
        if len(parts) >= 2:
            token = parts[1].strip()
            return token == expected_token
    return False


def _parse_bool_flag(value):
    """
    Accepts either a native JSON boolean (website, DRF-parsed) or the string "true"/"false"
    (mobile app, whose apiPost only sends Record<string, string> bodies) — bool("false")
    is truthy in Python, so a plain bool() cast would silently treat an unchecked box
    as accepted.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def reject_unauthorized_super_admin_role(request, role):
    """
    SECURITY: role is a client-supplied field on public register/login/verify-otp/resend-otp
    endpoints. Without this guard, anyone could pass role="super_admin" to self-provision a
    super admin account. Only an already-authenticated super admin may create/auth as one.
    Returns a 403 Response if blocked, otherwise None.
    """
    if role == "super_admin":
        _, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return Response(
                {"error": "Not authorized to use the super_admin role."},
                status=status.HTTP_403_FORBIDDEN,
            )
    return None

class BookMyConsoleRegisterView(APIView):
    """
    POST /auth/register/
    Body: { gamertag, email, password, iv, phone, role }  — all AES-CBC encrypted (except role)
    Returns: encrypted { message, email }   — OTP sent, JWT NOT yet issued
    """
    # SECURITY: the generic per-IP throttle (see settings.DEFAULT_THROTTLE_RATES) is loose
    # enough to be meaningless for a credential-guessing endpoint — replace it with a tight,
    # scoped rate specific to auth endpoints.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        data = request.data
        role = data.get("role", "")
        guard_error = reject_unauthorized_super_admin_role(request, role)
        if guard_error:
            return guard_error
        result, status_code = auth_handler.bookmyconsole_register(
            gamertag          = data.get("gamertag", ""),
            email             = data.get("email", ""),
            password          = data.get("password", ""),
            iv                = data.get("iv", ""),
            phone             = data.get("phone", ""),
            is_admin          = check_is_admin(request),
            role              = role,
            razorpay_password = data.get("razorpay_password", ""),
            # Website sends a native JSON boolean; the mobile app's apiPost only accepts
            # Record<string, string> bodies and sends the string "true"/"false" instead —
            # `bool("false")` is truthy in Python, so a plain bool() cast here would silently
            # treat an unchecked box as accepted.
            terms_accepted    = _parse_bool_flag(data.get("terms_accepted", False)),
        )
        return Response(result, status=status_code)


class BookMyConsoleLoginView(APIView):
    """
    POST /auth/login/
    Body: { email, password, iv, role }           — AES-CBC encrypted (except role)
    Returns: encrypted { message, email }   — OTP sent, JWT NOT yet issued
    """
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        data = request.data
        role = data.get("role", "")
        # No role guard here: login can only authenticate an EXISTING super_admin document
        # (find_one + password check) — it can never create one, so there's no escalation
        # risk. Guarding it too would lock out every real super admin, since logging in
        # would then require already having super-admin credentials.
        result, status_code = auth_handler.bookmyconsole_login(
            email    = data.get("email", ""),
            password = data.get("password", ""),
            iv       = data.get("iv", ""),
            is_admin = check_is_admin(request),
            role     = role,
        )
        return Response(result, status=status_code)


class BookMyConsoleVerifyOTPView(APIView):
    """
    POST /auth/verify-otp/
    Body: { email, otp_code, iv, role }           — AES-CBC encrypted (except role)
    Returns: encrypted { token, user }      — JWT issued on success
    """
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        data = request.data
        role = data.get("role", "")
        # No role guard here either — verify-otp only completes auth for an existing
        # pending doc (created by register, which IS guarded, or by login against an
        # existing account); it cannot itself create a super_admin document.
        result, status_code = auth_handler.bookmyconsole_verify_otp(
            email    = data.get("email", ""),
            otp_code = data.get("otp_code", ""),
            iv       = data.get("iv", ""),
            is_admin = check_is_admin(request),
            role     = role,
        )
        response_obj = Response(result, status=status_code)
        if status_code == 200:
            try:
                import json
                decrypted = auth_handler.decrypt_data(result["encrypted_response"], result["iv"])
                parsed = json.loads(decrypted)
                token = parsed.get("token")
                user_role = parsed.get("user", {}).get("role", "")
                
                cookie_key = None
                if user_role == "super_admin" or role == "super_admin":
                    cookie_key = "bmc_super_admin_token"
                elif user_role == "admin" or role == "admin" or check_is_admin(request):
                    cookie_key = "bmc_admin_token"
                elif user_role == "website_user" or role == "website_user":
                    # Must NOT reuse bmc_gamer_token here — that name is also used for the
                    # mobile app's role="user" sessions, and /auth/me/'s cookie-based
                    # lookup-order heuristic uses the cookie's presence/name to decide
                    # which collection to check first. Sharing the name meant a website
                    # session could get misidentified as a mobile session and resolve
                    # against the wrong (db.users) account if one happens to exist under
                    # the same email — silently losing that account's saved phone number.
                    cookie_key = "bmc_website_token"
                else:
                    cookie_key = "bmc_gamer_token"

                if token and cookie_key:
                    response_obj.set_cookie(
                        key=cookie_key,
                        value=token,
                        httponly=True,
                        secure=True,
                        samesite='None',
                        max_age=30 * 24 * 3600,
                    )
            except Exception as e:
                print(f"[COOKIE ERROR] Failed to set auth cookie: {e}")
        return response_obj


class BookMyConsoleResendOTPView(APIView):
    """
    POST /auth/resend-otp/
    Body: { email, iv, role }
    Re-generates and resends OTP. Returns encrypted success message.
    """
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        data     = request.data
        iv       = data.get("iv", "")
        email_enc = data.get("email", "")
        role     = data.get("role", "")
        # No role guard here — resend-otp only re-sends a code for an existing account
        # (404s otherwise); it cannot create or promote one.
        try:
            dec_email = auth_handler.decrypt_data(email_enc, iv).strip().lower()
        except Exception as e:
            return Response({"error": f"Decryption failed: {str(e)}"}, status=400)

        from .Handlers.db_connection import db_main
        from .Handlers.email_handler import send_otp_email
        import random
        from datetime import datetime, timedelta, timezone

        is_admin = check_is_admin(request)
        coll = auth_handler.get_user_collection(is_admin, role)

        if (is_admin or role == "admin") and role != "super_admin":
            cafe_exists = db_main.cafes.find_one({"owner_email": dec_email, "is_deleted": {"$ne": True}})
            if not cafe_exists:
                return Response({"error": "This account is not associated with any registered gaming cafe. Access denied."}, status=403)

        user = coll.find_one({"email": dec_email})
        if not user:
            return Response({"error": "No account found for this email."}, status=404)

        # SECURITY: resending unconditionally clears otp_attempts (below), which otherwise
        # exists specifically to bound OTP-guessing. Without a cooldown, an attacker could
        # dodge that lockout forever by resending just before hitting the attempt cap. A
        # fresh code each time still bounds the practical impact (they can't accumulate
        # guesses against one fixed code), but this closes the gap outright rather than
        # relying on that as the only defense.
        prev_expiry = user.get("otp_expiry")
        if prev_expiry:
            if prev_expiry.tzinfo is None:
                prev_expiry = prev_expiry.replace(tzinfo=timezone.utc).astimezone(auth_handler.IST)
            last_sent = prev_expiry - timedelta(minutes=auth_handler.get_otp_expiry_minutes(role))
            elapsed = (datetime.now(auth_handler.IST) - last_sent).total_seconds()
            if elapsed < auth_handler.OTP_RESEND_COOLDOWN_SECONDS:
                wait_for = int(auth_handler.OTP_RESEND_COOLDOWN_SECONDS - elapsed)
                return Response({"error": f"Please wait {wait_for}s before requesting another code."}, status=429)

        otp_code   = str(random.randint(100000, 999999))
        otp_expiry = datetime.now(auth_handler.IST) + timedelta(minutes=auth_handler.get_otp_expiry_minutes(role))
        coll.update_one(
            {"_id": user["_id"]},
            {"$set": {"otp_code": auth_handler.hash_otp(otp_code), "otp_expiry": otp_expiry}, "$unset": {"otp_attempts": ""}}
        )
        gamertag = user.get("gamertag") or user.get("first_name", "PLAYER")
        send_otp_email(dec_email, otp_code, gamertag=gamertag, purpose="resend")

        enc_resp, new_iv = auth_handler.encrypt_data(
            '{"message": "New OTP sent to your email."}',
            auth_handler.ENCRYPTION_KEY
        )
        return Response({"encrypted_response": enc_resp, "iv": new_iv}, status=200)


class BookMyConsoleForgotPasswordView(APIView):
    """
    POST /auth/forgot-password/
    Body: { email, iv, role }           — AES-CBC encrypted (except role)
    Returns: encrypted { message }       — same generic message whether or not the
    account exists, to avoid email enumeration.
    """
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        data = request.data
        # No role guard here, same reasoning as login: this can only look up an existing
        # account (find_one), never create or promote one.
        result, status_code = auth_handler.bookmyconsole_forgot_password(
            email    = data.get("email", ""),
            iv       = data.get("iv", ""),
            is_admin = check_is_admin(request),
            role     = data.get("role", ""),
        )
        return Response(result, status=status_code)


class BookMyConsoleResetPasswordView(APIView):
    """
    POST /auth/reset-password/
    Body: { email, otp_code, new_password, iv, role }   — AES-CBC encrypted (except role)
    Returns: encrypted { message }
    """
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        data = request.data
        result, status_code = auth_handler.bookmyconsole_reset_password(
            email        = data.get("email", ""),
            otp_code     = data.get("otp_code", ""),
            new_password = data.get("new_password", ""),
            iv           = data.get("iv", ""),
            is_admin     = check_is_admin(request),
            role         = data.get("role", ""),
        )
        return Response(result, status=status_code)


def _is_allowed_oauth_redirect_target(target):
    """
    SECURITY: `state`/`return_url` is unauthenticated, attacker-influenceable input that
    becomes the final redirect target after Google auth completes (carrying the session
    token in the query string). Only our own app schemes and our own frontends' exact
    origins may be used — NOT an arbitrary https:// URL.

    Checks the target's origin against settings.ALLOWED_ORIGINS (the same trusted-web-
    origins list CORS already uses), not the old single FRONTEND_URL value — that env
    var only ever held the mobile app's bookmyconsole:// scheme and was never actually
    a website origin, so EVERY website (bookmyconsole.com, admin.bookmyconsole.com,
    etc.) Google login was being rejected here as "Unauthorized redirect target"
    regardless of how correctly CORS itself was configured for that same domain.
    """
    if not target:
        return False
    if target.startswith('bookmyconsole://') or target.startswith('exp://'):
        return True
    try:
        from urllib.parse import urlparse
        parsed = urlparse(target)
        if parsed.scheme in ('http', 'https') and parsed.netloc:
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin in settings.ALLOWED_ORIGINS:
                return True
        if parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1'):
            return True
    except Exception:
        pass
    return False


class BookMyConsoleGoogleLoginView(APIView):
    """
    GET /auth/google/login/
    Redirects to Google accounts login page.
    """
    def get(self, request):
        try:
            return_url = request.query_params.get('return_url', settings.FRONTEND_URL)
            if not _is_allowed_oauth_redirect_target(return_url):
                return Response({"error": "Unauthorized redirect target."}, status=status.HTTP_400_BAD_REQUEST)
            # Google's `state` param round-trips unchanged to the callback — it's the only
            # channel available to carry whether the user checked the consent box, since the
            # callback only ever receives `code` and `state` back from Google. Must be
            # percent-encoded: an unescaped `&` here would be parsed by Google as a second
            # top-level query param instead of part of state's value, and wouldn't come back.
            terms_accepted = request.query_params.get('terms_accepted', '') in ('1', 'true', 'True')
            state_target = return_url
            if terms_accepted:
                separator = '&' if '?' in return_url else '?'
                state_target = f"{return_url}{separator}terms_accepted=1"
            from urllib.parse import quote
            auth_url = (
                "https://accounts.google.com/o/oauth2/v2/auth"
                f"?client_id={settings.GOOGLE_CLIENT_ID}"
                f"&redirect_uri={settings.BACKEND_URL}/api/v1/main/auth/google/callback/"
                "&response_type=code"
                "&scope=openid%20email%20profile"
                "&access_type=offline"
                "&prompt=select_account"
                f"&state={quote(state_target, safe='')}"
            )
            return redirect(auth_url)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class BookMyConsoleGoogleCallbackView(APIView):
    """
    GET /auth/google/callback/
    Receives auth code from Google, verifies it, and redirects to mobile app schema.
    """
    def get(self, request):
        try:
            code = request.query_params.get('code')
            state = request.query_params.get('state', settings.FRONTEND_URL)
            if not code:
                return Response({"error": "Auth code not provided"}, status=status.HTTP_400_BAD_REQUEST)
            is_mobile = state.startswith('bookmyconsole://') or state.startswith('exp://')
            role = "user" if is_mobile else "website_user"
            from urllib.parse import urlparse, parse_qs
            state_terms_accepted = parse_qs(urlparse(state).query).get('terms_accepted', ['0'])[0] in ('1', 'true', 'True')
            response, status_code = auth_handler.bookmyconsole_google_auth_code_verify(code, role=role, terms_accepted=state_terms_accepted)
            
            if status_code == 200:
                # SECURITY: Only allow redirects to our own app schemes / our own frontend origin —
                # NOT any arbitrary https:// URL (that would leak the session token to attacker sites).
                if not _is_allowed_oauth_redirect_target(state):
                    return Response({"error": "Unauthorized redirect target."}, status=status.HTTP_403_FORBIDDEN)

                # Use a proper URL join if possible, but for now ensure ? or &
                from urllib.parse import quote
                separator = '&' if '?' in state else '?'
                encoded_response = quote(response['encrypted_response'])
                encoded_iv = quote(response['iv'])
                redirect_url = f"{state}{separator}encrypted_response={encoded_response}&iv={encoded_iv}"
                
                # CRITICAL: Django's 'redirect' blocks custom protocols like exp:// or bookmyconsole://
                # We bypass this by using a raw HttpResponse with status 302
                from django.http import HttpResponse
                response_obj = HttpResponse(status=302)
                response_obj['Location'] = redirect_url

                if not is_mobile:
                    try:
                        import json
                        decrypted = auth_handler.decrypt_data(response["encrypted_response"], response["iv"])
                        parsed = json.loads(decrypted)
                        token = parsed.get("token")
                        if token:
                            # role is always "website_user" here (mobile is handled by the
                            # `if is_mobile` branch above) — bmc_website_token, not
                            # bmc_gamer_token, so /auth/me/'s lookup order can tell this
                            # apart from a mobile-app gamer session. See the matching
                            # comment in BookMyConsoleVerifyOTPView for the bug this caused.
                            response_obj.set_cookie(
                                key='bmc_website_token',
                                value=token,
                                httponly=True,
                                secure=True,
                                samesite='None',
                                max_age=30 * 24 * 3600,
                            )
                    except Exception as e:
                        print(f"[COOKIE ERROR] Failed to set Google bmc_website_token cookie: {e}")

                return response_obj
            
            return Response(response, status=status_code)
        except Exception as e:
            import traceback
            print(traceback.format_exc()) # Log to server console
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class BookMyConsoleUpdatePhoneView(APIView):
    """
    POST /auth/update-phone/
    Header: Authorization: Bearer <token>
    Body: { phone, iv, role } (phone is AES-CBC encrypted; role is plaintext, same as register/login)
    Returns: encrypted { message, user }
    """
    def post(self, request):
        email, error_response = auth_middleware.authenticate_request(request)
        if error_response:
            return error_response

        data = request.data
        result, status_code = auth_handler.bookmyconsole_update_phone(
            email           = email,
            phone_encrypted = data.get("phone", ""),
            iv              = data.get("iv", ""),
            is_admin        = check_is_admin(request),
            role            = data.get("role", ""),
        )
        return Response(result, status=status_code)


class BookMyConsoleUpdateProfileView(APIView):
    """
    POST /auth/update-profile/
    Header: Authorization: Bearer <token>
    Body: { gamertag?, city?, gamer_id?, avatar_id? } (plain JSON — none of these are
    sensitive PII, unlike phone, so this skips the AES envelope /auth/update-phone/ uses).
    Returns: { status, user } with the fields as actually persisted.
    """
    def post(self, request):
        email, error_response = auth_middleware.authenticate_request(request)
        if error_response:
            return error_response

        result, status_code = auth_handler.bookmyconsole_update_profile(
            email    = email,
            updates  = request.data,
            is_admin = check_is_admin(request),
            role     = request.data.get("role", ""),
        )
        return Response(result, status=status_code)


class BookMyConsoleUploadAvatarView(APIView):
    """
    POST /auth/upload-avatar/ (multipart/form-data, field name: "image")
    Header: Authorization: Bearer <token>
    Uploads the image to Cloudinary and stores the resulting URL on the user's account.
    Returns: { status, avatar_url }
    """
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        email, error_response = auth_middleware.authenticate_request(request)
        if error_response:
            return error_response

        result, status_code = auth_handler.bookmyconsole_upload_avatar(
            email         = email,
            uploaded_file = request.FILES.get("image"),
            is_admin      = check_is_admin(request),
            role          = request.data.get("role", ""),
        )
        return Response(result, status=status_code)


class BookMyConsoleDeleteAccountView(APIView):
    """
    POST /auth/delete-account/
    Header: Authorization: Bearer <token>
    Permanently deletes the authenticated user's account and personal data. Required by
    Google Play policy for any app that supports account creation.
    Returns: { status, message }
    """
    def post(self, request):
        email, error_response = auth_middleware.authenticate_request(request)
        if error_response:
            return error_response

        result, status_code = auth_handler.bookmyconsole_delete_account(
            email    = email,
            is_admin = check_is_admin(request),
            role     = request.data.get("role", ""),
        )
        return Response(result, status=status_code)


from .Handlers import bookings_handler

class BookedSlotsView(APIView):
    """
    GET /bookings/slots/?cafe_id=...&zone=...&date=...
    Returns list of already reserved slot strings.
    """
    def get(self, request):
        cafe_id = request.query_params.get("cafe_id")
        zone = request.query_params.get("zone")
        date = request.query_params.get("date")
        if not cafe_id or not zone or not date:
            return Response({"error": "Missing parameters"}, status=status.HTTP_400_BAD_REQUEST)
        
        result, status_code = bookings_handler.get_booked_slots_handler(cafe_id, zone, date)
        return Response(result, status=status_code)


class BookingListCreateView(APIView):
    """
    GET /bookings/ — Retrieve user bookings. Requires JWT.
    POST /bookings/create/ — Reserve slots. Requires JWT.
    """
    def get(self, request):
        email, error_response = auth_middleware.authenticate_request(request)
        if error_response:
            return error_response

        cafe_id = request.query_params.get("cafe_id") or request.query_params.get("cafeId")
        date = request.query_params.get("date")

        if cafe_id:
            # SECURITY: cafe_id-scoped queries return every customer's bookings for that
            # cafe — only the cafe's owner or a super admin may request this view.
            _, owner_error = authenticate_admin_owner(request, cafe_id)
            if owner_error:
                return owner_error

        result, status_code = bookings_handler.get_user_bookings_handler(email, cafe_id=cafe_id, date=date)
        return Response(result, status=status_code)

    def post(self, request):
        email, error_response = auth_middleware.authenticate_request(request)
        if error_response:
            return error_response
            
        data = request.data
        print("[DEBUG POST /bookings/] Incoming data:", data)
        
        # Explicit slots parsing to prevent operator precedence bugs
        slots = data.get("slots")
        if not slots:
            slot_str = data.get("slot")
            if slot_str:
                slots = [s.strip() for s in slot_str.split(",") if s.strip()]
            else:
                slots = []

        result, status_code = bookings_handler.create_booking_handler(
            user_email          = email,
            cafe_id             = data.get("cafe_id") or data.get("cafeId"),
            cafe_name           = data.get("cafe_name") or data.get("cafeName"),
            zone                = data.get("zone"),
            date                = data.get("date") or data.get("bookingDate") or data.get("booking_date"),
            slots               = slots,
            rig                 = data.get("rig"),
            user_name           = data.get("customerName") or data.get("customer_name") or data.get("userName") or data.get("user_name"),
            user_phone          = data.get("customerPhone") or data.get("customer_phone") or data.get("userPhone") or data.get("user_phone") or "",
            # SECURITY: price and payment_status are computed/verified server-side (see
            # bookings_handler.create_booking_handler) — a client can no longer dictate them.
            # The client must complete Razorpay checkout first and pass the resulting IDs here.
            razorpay_order_id   = data.get("razorpay_order_id"),
            razorpay_payment_id = data.get("razorpay_payment_id"),
            razorpay_signature  = data.get("razorpay_signature"),
        )
        return Response(result, status=status_code)

# ── Tournaments ────────────────────────────────────────────────────────────────

class TournamentListCreateView(APIView):
    """GET /tournaments/ — List/seed tournaments, POST /tournaments/ — Create a new tournament with image upload"""
    parser_classes = (MultiPartParser, FormParser)

    def get(self, request):
        cafe_id = request.query_params.get("cafe_id") or request.query_params.get("cafeId")
        response = tournaments.get_tournaments_handler(cafe_id=cafe_id)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(response, status=status.HTTP_200_OK)


    def post(self, request):
        # A cafe owner creates tournaments for their own cafe from cafe-command-center —
        # not just super admins from playhub-command. Mirrors TournamentDetailView.get's
        # existing cafe-owner-or-super-admin pattern.
        cafe_id = request.data.get("cafe_id") or request.data.get("cafeId")
        if cafe_id:
            email, error_response = authenticate_admin_owner(request, cafe_id)
        else:
            email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        response = tournaments.create_tournament_handler(request.data, request.FILES)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_201_CREATED)


class TournamentDetailView(APIView):
    """GET /tournaments/<tournament_id>/ — List registrations (admin), PATCH — Update details, DELETE — Delete tournament"""
    parser_classes = (MultiPartParser, FormParser)

    def get(self, request, tournament_id):
        # A cafe-scoped tournament's own owner may view its registrations too, not just
        # super admin — otherwise the cafe-owner dashboard has no legitimate way to show
        # a cafe owner their own tournament's sign-ups.
        from .Handlers.db_connection import get_db
        from .Handlers.tournaments import safe_object_id
        db = get_db()
        if not db:
            return Response({"status": "error", "message": "Database offline"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        tournament = db.tournaments.find_one({"_id": safe_object_id(tournament_id)})
        if not tournament:
            return Response({"status": "error", "message": "Tournament not found."}, status=status.HTTP_404_NOT_FOUND)

        cafe_id = tournament.get("cafe_id")
        if cafe_id:
            email, error_response = authenticate_admin_owner(request, cafe_id)
        else:
            # Platform-wide tournament (no owning cafe) — super admin only.
            email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response

        result, status_code = tournaments.get_tournament_registrations_handler(tournament_id)
        return Response(result, status=status_code)

    def patch(self, request, tournament_id):
        # Same cafe-owner-or-super-admin rule as GET above — a cafe owner must be able to
        # start/end/revert/edit/cancel their own tournament from cafe-command-center.
        from .Handlers.db_connection import get_db
        from .Handlers.tournaments import safe_object_id
        db = get_db()
        if not db:
            return Response({"status": "error", "message": "Database offline"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        tournament = db.tournaments.find_one({"_id": safe_object_id(tournament_id)})
        if not tournament:
            return Response({"status": "error", "message": "Tournament not found."}, status=status.HTTP_404_NOT_FOUND)

        cafe_id = tournament.get("cafe_id")
        if cafe_id:
            email, error_response = authenticate_admin_owner(request, cafe_id)
        else:
            email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response

        response = tournaments.update_tournament_handler(tournament_id, request.data, request.FILES)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)

    def delete(self, request, tournament_id):
        from .Handlers.db_connection import get_db
        from .Handlers.tournaments import safe_object_id
        db = get_db()
        if not db:
            return Response({"status": "error", "message": "Database offline"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        tournament = db.tournaments.find_one({"_id": safe_object_id(tournament_id)})
        if not tournament:
            return Response({"status": "error", "message": "Tournament not found."}, status=status.HTTP_404_NOT_FOUND)

        cafe_id = tournament.get("cafe_id")
        if cafe_id:
            email, error_response = authenticate_admin_owner(request, cafe_id)
        else:
            email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response

        response = tournaments.delete_tournament_handler(tournament_id)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


class TournamentToggleRegistrationView(APIView):
    """POST /tournaments/<tournament_id>/toggle-registration/ — Toggle registration open/closed (Admin action)"""
    def post(self, request, tournament_id):
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        response = tournaments.toggle_registration_handler(tournament_id)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


class TournamentRegisterView(APIView):
    """POST /tournaments/<str:tournament_id>/register/ — Register for a tournament"""
    def post(self, request, tournament_id):
        email, error_response = auth_middleware.authenticate_request(request)
        if error_response:
            return error_response
        response = tournaments.register_tournament_handler(tournament_id, email, request.data)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


class UserTournamentRegistrationsView(APIView):
    """
    GET /tournaments/registrations/ — Fetch the logged-in user's tournament registrations. Requires JWT.
    """
    def get(self, request):
        email, error_response = auth_middleware.authenticate_request(request)
        if error_response:
            return error_response
            
        result, status_code = tournaments.get_user_registrations_handler(email)
        return Response(result, status=status_code)





# ── Hardware Rigs ─────────────────────────────────────────────────────────────

class RigListCreateView(APIView):
    """GET /rigs/ — List/seed rigs, POST /rigs/ — Create a new rig"""
    def get(self, request):
        cafe_id = request.query_params.get("cafe_id") or request.query_params.get("cafeId")
        response = rigs.get_rigs_handler(cafe_id=cafe_id)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(response, status=status.HTTP_200_OK)

    def post(self, request):
        # A cafe owner manages their own systems from cafe-command-center — not just
        # super admins from playhub-command. Same pattern as tournaments above.
        cafe_id = request.data.get("cafe_id") or request.data.get("cafeId")
        if cafe_id:
            email, error_response = authenticate_admin_owner(request, cafe_id)
        else:
            email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        response = rigs.create_rig_handler(request.data)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_201_CREATED)


class RigDetailView(APIView):
    """GET /rigs/<id>/ — Detail, PUT /rigs/<id>/ — Update, DELETE /rigs/<id>/ — Delete"""
    def get(self, request, rig_id):
        response = rigs.get_rig_detail_handler(rig_id)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_404_NOT_FOUND)
        return Response(response, status=status.HTTP_200_OK)

    def _authenticate_for_rig(self, request, rig_id):
        from .Handlers.db_connection import get_db
        from bson import ObjectId
        db = get_db()
        if not db:
            return None, Response({"status": "error", "message": "Database offline"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        rig = db.rigs.find_one({"_id": ObjectId(rig_id)}) if ObjectId.is_valid(rig_id) else None
        if not rig:
            return None, Response({"status": "error", "message": "Rig not found."}, status=status.HTTP_404_NOT_FOUND)
        cafe_id = rig.get("cafe_id")
        if cafe_id:
            return authenticate_admin_owner(request, cafe_id)
        return auth_middleware.authenticate_super_admin_request(request)

    def put(self, request, rig_id):
        email, error_response = self._authenticate_for_rig(request, rig_id)
        if error_response:
            return error_response
        response = rigs.update_rig_handler(rig_id, request.data)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)

    def delete(self, request, rig_id):
        email, error_response = self._authenticate_for_rig(request, rig_id)
        if error_response:
            return error_response
        response = rigs.delete_rig_handler(rig_id)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


class RigReserveView(APIView):
    """POST /rigs/<id>/reserve/ — Create an admin reservation for specific slots."""
    def post(self, request, rig_id):
        from .Handlers.db_connection import get_db
        from bson import ObjectId
        db = get_db()
        if not db:
            return Response({"status": "error", "message": "Database offline"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        rig = db.rigs.find_one({"_id": ObjectId(rig_id)}) if ObjectId.is_valid(rig_id) else None
        if not rig:
            return Response({"status": "error", "message": "Rig not found."}, status=status.HTTP_404_NOT_FOUND)
        cafe_id = rig.get("cafe_id")
        if cafe_id:
            email, error_response = authenticate_admin_owner(request, cafe_id)
        else:
            email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        response = rigs.reserve_rig_slots_handler(rig_id, request.data)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_201_CREATED)


class CafeDetailView(APIView):
    """GET /cafes/<id>/ — Detail, PUT /cafes/<id>/ — Update, DELETE /cafes/<id>/ — Remove"""
    def get(self, request, cafe_id):
        response = cafes.get_cafe_detail_handler(cafe_id)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_404_NOT_FOUND)
        return Response(response, status=status.HTTP_200_OK)

    def put(self, request, cafe_id):
        # A cafe owner edits their own Cafe Profile page from cafe-command-center — not
        # just super admins from playhub-command. Same pattern as tournaments/rigs/offers.
        email, error_response = authenticate_admin_owner(request, cafe_id)
        if error_response:
            return error_response
        response = cafes.update_cafe_handler(cafe_id, request.data)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)

    def delete(self, request, cafe_id):
        # Cafe removal/soft-delete stays super-admin-only — cafe-command-center never
        # calls this; it's a platform lifecycle action, not routine self-service.
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        response = cafes.delete_cafe_handler(cafe_id)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


class CafeRestoreView(APIView):
    """POST /cafes/<cafe_id>/restore/ — Restore a soft-deleted cafe (Super Admin only)"""
    def post(self, request, cafe_id):
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        response = cafes.restore_cafe_handler(cafe_id)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


class CafeSubscriptionDetailView(APIView):
    """GET /cafes/<cafe_id>/subscription/ — Current status + payment history (owner or super admin)."""
    def get(self, request, cafe_id):
        # allow_when_suspended: an owner needs to see their own subscription/lockout
        # state and payment history precisely BECAUSE they're suspended.
        email, error_response = authenticate_admin_owner(request, cafe_id, allow_when_suspended=True)
        if error_response:
            return error_response
        response, status_code = subscriptions.get_cafe_subscription_handler(cafe_id)
        return Response(response, status=status_code)


class CafeSubscriptionOrderView(APIView):
    """POST /cafes/<cafe_id>/subscription/create-order/ — Create a ₹1599 Razorpay order for this cafe's next payment."""
    def post(self, request, cafe_id):
        # allow_when_suspended: this IS the recovery path — a suspended owner must be
        # able to create a payment order to pay their way back in.
        email, error_response = authenticate_admin_owner(request, cafe_id, allow_when_suspended=True)
        if error_response:
            return error_response
        response, status_code = subscriptions.create_subscription_order_handler(cafe_id)
        return Response(response, status=status_code)


class CafeSubscriptionTrialWelcomeShownView(APIView):
    """POST /cafes/<cafe_id>/subscription/trial-welcome-shown/ — Marks the one-time "welcome to your free trial" popup as seen, so it never shows again for this cafe."""
    def post(self, request, cafe_id):
        email, error_response = authenticate_admin_owner(request, cafe_id, allow_when_suspended=True)
        if error_response:
            return error_response
        response, status_code = subscriptions.mark_trial_welcome_shown_handler(cafe_id)
        return Response(response, status=status_code)


class CafeSubscriptionVerifyView(APIView):
    """POST /cafes/<cafe_id>/subscription/verify/ — Verify the Razorpay payment and extend the due date."""
    def post(self, request, cafe_id):
        # allow_when_suspended: verifying the payment that lifts the suspension must
        # itself work while still suspended, or paying could never actually unlock them.
        email, error_response = authenticate_admin_owner(request, cafe_id, allow_when_suspended=True)
        if error_response:
            return error_response
        data = request.data
        response, status_code = subscriptions.verify_subscription_payment_handler(
            cafe_id,
            data.get("razorpay_order_id", ""),
            data.get("razorpay_payment_id", ""),
            data.get("razorpay_signature", ""),
        )
        return Response(response, status=status_code)


class CafeRazorpayCredentialsView(APIView):
    """
    GET/PUT/DELETE /cafes/<cafe_id>/razorpay-credentials/ — the cafe owner's own Razorpay
    account for booking payments (owner or super admin only). PUT never echoes the secret
    back; GET only ever returns whether it's configured + the key_id.
    """
    def get(self, request, cafe_id):
        email, error_response = authenticate_admin_owner(request, cafe_id)
        if error_response:
            return error_response
        response = cafes.get_razorpay_credentials_status_handler(cafe_id)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)

    def put(self, request, cafe_id):
        email, error_response = authenticate_admin_owner(request, cafe_id)
        if error_response:
            return error_response
        data = request.data
        # SECURITY: the "locked until you enter your Razorpay password" gate in
        # cafe-command-center is only real if the save itself re-checks it — otherwise
        # it's a UI nicety a direct API call bypasses entirely. Same password gate as
        # CafeRazorpayPasswordVerifyView below.
        gate = cafes.verify_razorpay_password_handler(cafe_id, (email or "").strip().lower(), data.get("razorpay_password", ""))
        if gate.get("status") == "error":
            return Response(gate, status=status.HTTP_403_FORBIDDEN)
        response = cafes.save_razorpay_credentials_handler(
            cafe_id, data.get("key_id"), data.get("key_secret")
        )
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)

    def delete(self, request, cafe_id):
        email, error_response = authenticate_admin_owner(request, cafe_id)
        if error_response:
            return error_response
        data = request.data
        gate = cafes.verify_razorpay_password_handler(cafe_id, (email or "").strip().lower(), data.get("razorpay_password", ""))
        if gate.get("status") == "error":
            return Response(gate, status=status.HTTP_403_FORBIDDEN)
        response = cafes.delete_razorpay_credentials_handler(cafe_id)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


class CafeRazorpayPasswordStatusView(APIView):
    """GET /cafes/<cafe_id>/razorpay-credentials/password-status/ — whether the owner has set a Razorpay password yet."""
    def get(self, request, cafe_id):
        email, error_response = authenticate_admin_owner(request, cafe_id)
        if error_response:
            return error_response
        response = cafes.get_razorpay_password_status_handler(cafe_id, (email or "").strip().lower())
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


class CafeRazorpayPasswordSetView(APIView):
    """POST /cafes/<cafe_id>/razorpay-credentials/set-password/ — first-time-only setup for an account that predates this feature."""
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request, cafe_id):
        email, error_response = authenticate_admin_owner(request, cafe_id)
        if error_response:
            return error_response
        response = cafes.set_razorpay_password_handler(cafe_id, (email or "").strip().lower(), request.data.get("password", ""))
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


class CafeRazorpayPasswordForgotView(APIView):
    """POST /cafes/<cafe_id>/razorpay-credentials/forgot-password/ — emails a reset OTP to the owner's own account email."""
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request, cafe_id):
        email, error_response = authenticate_admin_owner(request, cafe_id)
        if error_response:
            return error_response
        response = cafes.forgot_razorpay_password_handler(cafe_id, (email or "").strip().lower())
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


class CafeRazorpayPasswordResetView(APIView):
    """POST /cafes/<cafe_id>/razorpay-credentials/reset-password/ — verifies the reset OTP and sets a new Razorpay password."""
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request, cafe_id):
        email, error_response = authenticate_admin_owner(request, cafe_id)
        if error_response:
            return error_response
        response = cafes.reset_razorpay_password_handler(
            cafe_id,
            (email or "").strip().lower(),
            request.data.get("otp_code", ""),
            request.data.get("new_password", ""),
        )
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


class CafeRazorpayPasswordVerifyView(APIView):
    """POST /cafes/<cafe_id>/razorpay-credentials/verify-password/ — unlocks the credential fields in the UI."""
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request, cafe_id):
        email, error_response = authenticate_admin_owner(request, cafe_id)
        if error_response:
            return error_response
        response = cafes.verify_razorpay_password_handler(cafe_id, (email or "").strip().lower(), request.data.get("password", ""))
        if response.get("status") == "error":
            error_status = status.HTTP_400_BAD_REQUEST if response.get("needs_setup") else status.HTTP_403_FORBIDDEN
            return Response(response, status=error_status)
        return Response(response, status=status.HTTP_200_OK)


class CafeBookingOrderCreateView(APIView):
    """
    POST /cafes/<cafe_id>/payments/create-order/ — Create a Razorpay order for any
    customer-facing payment at this specific cafe (slot booking, paid tournament entry),
    routed to the cafe owner's own Razorpay account if they've configured one, or the
    platform account as a fallback. Called by the customer (any logged-in user), not the
    cafe owner — unlike the other cafes/<id>/... endpoints above.
    """
    def post(self, request, cafe_id):
        email, error_response = auth_middleware.authenticate_request(request)
        if error_response:
            return error_response

        amount = request.data.get("amount")
        if amount is None:
            return Response({"status": "error", "message": "Missing 'amount' parameter"}, status=status.HTTP_400_BAD_REQUEST)
        # zone/date/slots/rig are only sent by the slot-booking checkout flow (never
        # tournament entry, which has no slot) — when present they let order creation
        # reject an already-taken slot BEFORE payment instead of after.
        zone = request.data.get("zone")
        date = request.data.get("date")
        slots = request.data.get("slots")
        rig = request.data.get("rig")
        response = payments.create_cafe_booking_order_handler(cafe_id, amount, zone=zone, date=date, slots=slots, rig=rig)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


class CafeBookingReleaseHoldView(APIView):
    """
    POST /cafes/<cafe_id>/payments/release-hold/ — Releases a pre-payment slot hold
    (see create_cafe_booking_order_handler) when the customer cancels or fails checkout,
    so the slot is bookable by someone else immediately instead of waiting out its TTL.
    Only ever releases the hold tied to the given order_id, which the caller already owns
    from their own just-created order — never a confirmed booking or a different hold.
    """
    def post(self, request, cafe_id):
        email, error_response = auth_middleware.authenticate_request(request)
        if error_response:
            return error_response

        order_id = request.data.get("order_id")
        if not order_id:
            return Response({"status": "error", "message": "Missing 'order_id' parameter"}, status=status.HTTP_400_BAD_REQUEST)
        payments.release_cafe_booking_hold(cafe_id, order_id)
        return Response({"status": "success"}, status=status.HTTP_200_OK)


class SubscriptionsListView(APIView):
    """GET /subscriptions/ — Every cafe's subscription status (Super Admin only)."""
    def get(self, request):
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        response, status_code = subscriptions.get_all_subscriptions_handler()
        return Response(response, status=status_code)


class SubscriptionMarkPaidView(APIView):
    """POST /subscriptions/<cafe_id>/mark-paid/ — Manually record a payment collected outside the app (Super Admin only)."""
    def post(self, request, cafe_id):
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        response, status_code = subscriptions.mark_subscription_paid_manually_handler(cafe_id, email or "super_admin")
        return Response(response, status=status_code)


class SubscriptionPaymentsListView(APIView):
    """GET /subscriptions/payments/ — Every subscription payment across every cafe, the
    platform's actual revenue ledger (Super Admin only)."""
    def get(self, request):
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        response, status_code = subscriptions.get_all_subscription_payments_handler()
        return Response(response, status=status_code)


class BookingDetailView(APIView):
    """PUT /bookings/<id>/ — Update a booking. payment_status: cafe admin/super admin
    only. status: admin/super admin may set anything; the booking's own user may only
    self-cancel. See update_booking_handler for why slot/date/rig aren't updatable here."""
    def put(self, request, booking_id):
        email, error_response, is_privileged = authenticate_booking_access(request, booking_id)
        if error_response:
            return error_response

        response = bookings.update_booking_handler(booking_id, request.data, is_privileged=is_privileged)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


class RazorpayOrderCreateView(APIView):
    """POST /payments/create-order/ — Create a Razorpay Order"""
    def post(self, request):
        email, error_response = auth_middleware.authenticate_request(request)
        if error_response:
            return error_response

        amount = request.data.get("amount")
        if amount is None:
            return Response({"status": "error", "message": "Missing 'amount' parameter"}, status=status.HTTP_400_BAD_REQUEST)
        response = payments.create_razorpay_order_handler(amount)
        response["key_id"] = getattr(settings, 'RAZORPAY_KEY_ID', '')
        return Response(response, status=status.HTTP_200_OK)


class UserFavoritesView(APIView):
    """
    GET /users/favorites/ — Retrieve user's favorite cafes. Requires JWT.
    POST /users/favorites/ — Toggle a favorite cafe. Requires JWT.
    """
    def get(self, request):
        email, error_response = auth_middleware.authenticate_request(request)
        if error_response:
            return error_response
            
        result, status_code = favorites.get_favorites_handler(email)
        return Response(result, status=status_code)

    def post(self, request):
        email, error_response = auth_middleware.authenticate_request(request)
        if error_response:
            return error_response
            
        cafe_id = request.data.get("cafe_id")
        result, status_code = favorites.toggle_favorite_handler(email, cafe_id)
        return Response(result, status=status_code)


class RegisterPushTokenView(APIView):
    """POST /push-tokens/register/ — a logged-in gamer registers their device's Expo push
    token so super-admin broadcasts (and future targeted notifications) can reach them."""
    def post(self, request):
        email, error_response = auth_middleware.authenticate_request(request)
        if error_response:
            return error_response

        expo_push_token = request.data.get("expo_push_token") or request.data.get("token")
        platform = request.data.get("platform")
        result, status_code = notifications.register_push_token_handler(email, expo_push_token, platform)
        return Response(result, status=status_code)


class BroadcastNotificationView(APIView):
    """
    GET  /notifications/broadcasts/ — recent broadcast history (super admin only).
    POST /notifications/broadcast/  — send a push notification to an audience (super admin only).
    """
    def get(self, request):
        _, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        result, status_code = notifications.list_broadcasts_handler()
        return Response(result, status=status_code)

    def post(self, request):
        sent_by, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response

        title = request.data.get("title")
        body = request.data.get("body") or request.data.get("message")
        audience = request.data.get("audience", "all_users")
        channel = request.data.get("channel", "push")
        result, status_code = notifications.send_broadcast_notification_handler(title, body, audience, sent_by, channel)
        return Response(result, status=status_code)


class SupportInfoView(APIView):
    """GET /support/info/ — public: the support email/phone shown on the Contact
    Support screen. No auth needed, not sensitive."""
    def get(self, request):
        result, status_code = support.get_support_info_handler()
        return Response(result, status=status_code)


class SupportQueryView(APIView):
    """POST /support/contact/ — forwards a support query to SUPPORT_EMAIL. Used by the
    gamer app, the cafe-owner dashboard, AND the public marketing website — auth is
    optional here (unlike most endpoints): if a valid JWT/cookie is present (gamer or
    cafe-owner, authenticate_request covers both), that verified email is used;
    otherwise this is a public site visitor who isn't logged in at all, and must supply
    their own email in the request body instead."""
    def post(self, request):
        email, _ = auth_middleware.authenticate_request(request)
        if not email:
            email = request.data.get("email")

        name = request.data.get("name")
        message = request.data.get("message")
        source = request.data.get("source", "App")
        result, status_code = support.create_support_query_handler(email, name, message, source)
        return Response(result, status=status_code)


def authenticate_admin_owner(request, cafe_id, allow_when_suspended=False):
    """
    Validates that the request is authenticated by:
    1. A JWT token belonging to the owner of the specified cafe, OR
    2. A super admin token (static or dynamic super_admin JWT).
    Returns (email, None) if successful.
    Returns (None, Response) if unauthorized or forbidden.

    SECURITY: a cafe whose subscription is suspended (past its grace period, unpaid) is
    rejected here too, for every endpoint except the subscription/payment views
    themselves (allow_when_suspended=True) — this was previously enforced ONLY by the
    frontend's own redirect (cafe-command-center's AuthGate/SuspendedLockout), which a
    direct API call bypasses entirely, letting a suspended cafe keep operating exactly
    as if it had paid. Super admins are never blocked by this — they need to be able to
    manage/inspect a suspended cafe regardless.
    """
    from .Handlers.db_connection import get_db
    from bson import ObjectId

    db = get_db()
    if not db:
        return None, Response({"status": "error", "message": "Database offline"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # 1. Try super admin authorization (static OR dynamic super_admin JWT)
    super_email, super_error = auth_middleware.authenticate_super_admin_request(request)
    if not super_error:
        print(f"[DEBUG authenticate_admin_owner] Super Admin access granted for cafe_id: {cafe_id}")
        return super_email, None

    # 2. Try standard JWT authorization
    email, error_response = auth_middleware.authenticate_request(request)
    if not error_response and email:
        # Check ownership
        try:
            cafe = db.cafes.find_one({"_id": ObjectId(cafe_id), "is_deleted": {"$ne": True}})
            if cafe and cafe.get("owner_email") == email:
                if not allow_when_suspended and cafes._effective_cafe_status(cafe) == "suspended":
                    return None, Response(
                        {"status": "error", "message": "Your subscription is past due. Please renew to regain access."},
                        status=status.HTTP_402_PAYMENT_REQUIRED,
                    )
                return email, None
        except Exception:
            pass

    return None, Response({"status": "error", "message": "Unauthorized: You do not own this cafe"}, status=status.HTTP_403_FORBIDDEN)


def authenticate_booking_access(request, booking_id):
    """
    Validates that the request is authenticated by:
    1. A super admin token (static or dynamic super_admin JWT), OR
    2. A JWT belonging to the booking's own user_email, OR
    3. A JWT belonging to the owner of the cafe the booking was made at.
    Returns (email, None, is_privileged) if successful, where is_privileged is True for
    super admins and the owning cafe's admin (as opposed to the booking's own user) —
    only privileged callers may change payment_status.
    Returns (None, Response, False) if unauthorized, forbidden, or the booking doesn't exist.
    """
    from .Handlers.db_connection import get_db
    from bson import ObjectId

    db = get_db()
    if not db:
        return None, Response({"status": "error", "message": "Database offline"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR), False

    try:
        booking = db.bookings.find_one({"_id": ObjectId(booking_id)})
    except Exception:
        booking = None
    if not booking:
        return None, Response({"status": "error", "message": "Booking not found."}, status=status.HTTP_404_NOT_FOUND), False

    # 1. Super admin
    super_email, super_error = auth_middleware.authenticate_super_admin_request(request)
    if not super_error:
        return super_email, None, True

    # 2. Standard JWT — must be the booking's own user or the owning cafe's admin
    email, error_response = auth_middleware.authenticate_request(request)
    if error_response:
        return None, error_response, False

    cafe_id = booking.get("cafe_id")
    if cafe_id:
        try:
            cafe = db.cafes.find_one({"_id": ObjectId(cafe_id), "is_deleted": {"$ne": True}})
            if cafe and cafe.get("owner_email") == email:
                return email, None, True
        except Exception:
            pass

    if booking.get("user_email") == email:
        return email, None, False

    return None, Response({"status": "error", "message": "Unauthorized: You do not have access to this booking."}, status=status.HTTP_403_FORBIDDEN), False


class SessionListCreateView(APIView):
    """
    GET /sessions/?cafe_id=... — Retrieves active/reserved sessions for the cafe.
    POST /sessions/ — Starts a manual walk-in session.
    """
    def get(self, request):
        cafe_id = request.query_params.get("cafe_id")
        if not cafe_id:
            return Response({"status": "error", "message": "Missing 'cafe_id' parameter"}, status=status.HTTP_400_BAD_REQUEST)
        
        email, error_response = authenticate_admin_owner(request, cafe_id)
        if error_response:
            return error_response

        response = sessions.list_sessions_handler(cafe_id)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(response, status=status.HTTP_200_OK)

    def post(self, request):
        system_id = request.data.get("system_id") or request.data.get("systemId")
        if not system_id:
            return Response({"status": "error", "message": "Missing 'system_id' parameter"}, status=status.HTTP_400_BAD_REQUEST)
        
        from .Handlers.db_connection import get_db
        from bson import ObjectId
        db = get_db()
        if db is None:
            return Response({"status": "error", "message": "MongoDB connection is not established."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        try:
            rig = db.rigs.find_one({"_id": ObjectId(system_id)})
        except Exception:
            return Response({"status": "error", "message": "Invalid 'system_id' parameter"}, status=status.HTTP_400_BAD_REQUEST)
        if not rig:
            return Response({"status": "error", "message": "Rig not found"}, status=status.HTTP_404_NOT_FOUND)
        
        cafe_id = rig.get("cafe_id")
        email, error_response = authenticate_admin_owner(request, cafe_id)
        if error_response:
            return error_response

        response = sessions.start_session_handler(data=request.data)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_201_CREATED)


class SessionActionView(APIView):
    """
    Handles active session control:
    POST /sessions/<id>/start/ — Start booking session.
    POST /sessions/<id>/extend/ — Extend session.
    POST /sessions/<id>/end/ — Stop session early.
    """
    def post(self, request, session_id, action):
        from .Handlers.db_connection import get_db
        from bson import ObjectId
        db = get_db()
        booking = db.bookings.find_one({"_id": ObjectId(session_id)})
        if not booking:
            return Response({"status": "error", "message": "Booking not found"}, status=status.HTTP_404_NOT_FOUND)

        cafe_id = booking.get("cafe_id")
        email, error_response = authenticate_admin_owner(request, cafe_id)
        if error_response:
            return error_response

        if action == "start":
            response = sessions.start_session_handler(booking_id=session_id)
        elif action == "extend":
            minutes = request.data.get("minutes", 30)
            response = sessions.extend_session_handler(booking_id=session_id, minutes=minutes)
        elif action == "end":
            response = sessions.end_session_handler(booking_id=session_id)
        else:
            return Response({"status": "error", "message": "Invalid action"}, status=status.HTTP_400_BAD_REQUEST)

        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


# ── Offers ────────────────────────────────────────────────────────────────────

class OfferListCreateView(APIView):
    """GET /offers/?cafe_id=<id> — list (admin), POST /offers/ — create (admin)"""
    def get(self, request):
        # A cafe owner manages their own offers from cafe-command-center — not just
        # super admins from playhub-command. Same pattern as tournaments/rigs above.
        cafe_id = request.query_params.get("cafe_id")
        if cafe_id:
            email, error_response = authenticate_admin_owner(request, cafe_id)
        else:
            email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        result, status_code = offers.get_offers_handler(cafe_id=cafe_id)
        return Response(result, status=status_code)

    def post(self, request):
        cafe_id = request.data.get("cafe_id") or request.data.get("cafeId")
        if cafe_id:
            email, error_response = authenticate_admin_owner(request, cafe_id)
        else:
            email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        result, status_code = offers.create_offer_handler(request.data)
        return Response(result, status=status_code)


class OfferDetailView(APIView):
    """PATCH /offers/<offer_id>/ — toggle/update, DELETE /offers/<offer_id>/ — delete (admin)"""

    def _authenticate_for_offer(self, request, offer_id):
        from .Handlers.db_connection import get_db
        from bson import ObjectId
        db = get_db()
        if not db:
            return None, Response({"status": "error", "message": "Database offline"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        offer = db.offers.find_one({"_id": ObjectId(offer_id)}) if ObjectId.is_valid(offer_id) else None
        if not offer:
            return None, Response({"status": "error", "message": "Offer not found."}, status=status.HTTP_404_NOT_FOUND)
        cafe_id = offer.get("cafe_id")
        if cafe_id:
            return authenticate_admin_owner(request, cafe_id)
        return auth_middleware.authenticate_super_admin_request(request)

    def patch(self, request, offer_id):
        email, error_response = self._authenticate_for_offer(request, offer_id)
        if error_response:
            return error_response
        result, status_code = offers.update_offer_handler(offer_id, request.data)
        return Response(result, status=status_code)

    def delete(self, request, offer_id):
        email, error_response = self._authenticate_for_offer(request, offer_id)
        if error_response:
            return error_response
        result, status_code = offers.delete_offer_handler(offer_id)
        return Response(result, status=status_code)


class ActiveOffersView(APIView):
    """GET /offers/active/?cafe_id=<id> — PUBLIC, returns currently active offers for a cafe"""
    def get(self, request):
        cafe_id = request.query_params.get("cafe_id")
        result, status_code = offers.get_active_offers_handler(cafe_id=cafe_id)
        return Response(result, status=status_code)


# ── Super Admin User Management ────────────────────────────────────────────────

class UserListView(APIView):
    """GET /users/ — Lists all platform users (Super Admin only)"""
    def get(self, request):
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        result, status_code = users.get_users_handler()
        return Response(result, status=status_code)


class UserStatusToggleView(APIView):
    """PUT /users/<str:user_id>/toggle-suspend/ — Toggles active/suspended user status (Super Admin only)"""
    def put(self, request, user_id):
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        result, status_code = users.toggle_user_status_handler(user_id)
        return Response(result, status=status_code)


class BookMyConsoleLogoutView(APIView):
    """
    POST /auth/logout/
    Revokes the current JWT server-side and clears whichever auth cookie was set
    (gamer / admin / super admin).
    """
    def post(self, request):
        # SECURITY: clearing the cookie alone doesn't stop the token being replayed if it
        # was captured elsewhere — revoke it server-side so it stops working immediately.
        auth_header = request.headers.get('Authorization')
        token = None
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1].strip()
        else:
            for cookie_name in ('bmc_gamer_token', 'bmc_website_token', 'bmc_admin_token', 'bmc_super_admin_token'):
                token = request.COOKIES.get(cookie_name)
                if token:
                    break

        if token:
            try:
                auth_handler.revoke_token(token)
            except Exception as e:
                print(f"[LOGOUT] Token revocation failed: {e}")

        response_obj = Response({"message": "Logout successful"}, status=status.HTTP_200_OK)
        for cookie_name in ('bmc_gamer_token', 'bmc_website_token', 'bmc_admin_token', 'bmc_super_admin_token'):
            response_obj.set_cookie(
                key=cookie_name,
                value='',
                max_age=0,
                httponly=True,
                secure=True,
                samesite='None',
            )
        return response_obj


class BookMyConsoleMeView(APIView):
    """
    GET /auth/me/
    Returns the currently authenticated user's profile info based on the HttpOnly cookie/auth header.
    """
    def get(self, request):
        email, error_response = auth_middleware.authenticate_request(request)
        if error_response:
            return error_response

        from .Handlers.db_connection import db_main
        # The JWT payload itself only carries an email, not which account/collection it
        # belongs to — and the SAME email can legitimately exist in more than one
        # collection (e.g. someone who both plays via the mobile app AND owns a cafe).
        # Whichever cookie actually carried this request is an unambiguous signal of which
        # account the frontend making the request means; only fall back to guessing a
        # fixed order when authenticating via a bare Authorization header, which carries
        # no such hint.
        # BUG: this used to have no branch for bmc_super_admin_token at all, so a super
        # admin's session (whose email often ALSO exists as a regular admin/gamer/website
        # account, since the same person tests every role) could silently resolve to the
        # wrong collection here - /auth/me/ returning the wrong "who am I" right as a
        # session was expiring/renewing is exactly what produced the reported "signs out
        # and logs back into the dashboard at the same time" confusion in the super admin
        # panel: the frontend's logged-in check and its logged-out check could each land
        # on a different answer depending on which collection this fell through to.
        if request.COOKIES.get('bmc_super_admin_token'):
            lookup_order = [(db_main.super_admin, "super_admin"), (db_main.admins, "admin"), (db_main.website_users, "website_user"), (db_main.users, "user")]
        elif request.COOKIES.get('bmc_admin_token'):
            lookup_order = [(db_main.admins, "admin"), (db_main.website_users, "website_user"), (db_main.users, "user")]
        elif request.COOKIES.get('bmc_website_token'):
            lookup_order = [(db_main.website_users, "website_user"), (db_main.users, "user"), (db_main.admins, "admin")]
        elif request.COOKIES.get('bmc_gamer_token'):
            lookup_order = [(db_main.users, "user"), (db_main.website_users, "website_user"), (db_main.admins, "admin")]
        else:
            # No recognizable auth cookie arrived at all - the common case for
            # cross-origin/ngrok dev setups where third-party cookies get blocked and only
            # the Authorization: Bearer fallback (see api-client.ts) actually carries the
            # session, which this endpoint can't otherwise tell a role from. Check
            # super_admin first: a collision with a customer/website account under the
            # same email is far less likely than the reverse, and guessing wrong here for
            # an actual super admin is the more damaging failure mode of the two.
            lookup_order = [(db_main.super_admin, "super_admin"), (db_main.website_users, "website_user"), (db_main.users, "user"), (db_main.admins, "admin")]

        user = None
        role = None
        for coll, coll_role in lookup_order:
            user = coll.find_one({"email": email})
            if user:
                role = coll_role
                break

        if not user:
            return Response({"error": "User profile not found"}, status=status.HTTP_404_NOT_FOUND)

        # Return user details
        from .Handlers.auth_handler import decrypt_phone_field
        response_data = {
            "id":             str(user["_id"]),
            "email":          email,
            "gamertag":       user.get("gamertag") or user.get("first_name", "PLAYER"),
            "full_name":      user.get("full_name", ""),
            "rank":           user.get("rank", "Recruit PRO I"),
            "xp":             user.get("xp", 0),
            "role":           user.get("role", role),
            "phone":          decrypt_phone_field(user.get("phone", "")),
            "city":           user.get("city", ""),
            "gamer_id":       user.get("gamer_id", ""),
            "avatar_id":      user.get("avatar_id", "cyber_ghost"),
            "avatar_url":     user.get("avatar_url", ""),
            # Needed so frontends can gate phone-onboarding to Google sign-ups only (a
            # traditional email/password signup already requires a phone at registration
            # time) - this endpoint runs on every session restore, so without this field
            # here the frontend's "is this a Google user" check would always see undefined
            # after a page reload/relogin, even though login/verify-otp responses do
            # include it.
            "auth_provider":  user.get("auth_provider", "traditional"),
        }
        return Response({"user": response_data}, status=status.HTTP_200_OK)


class PlatformStatsView(APIView):
    """
    GET /stats/
    Returns live raw database metrics for the landing page.
    """
    def get(self, request):
        try:
            from .Handlers.db_connection import db_main
            # 1. Total Cafes
            cafe_count = db_main.cafes.count_documents({"is_deleted": {"$ne": True}})
            
            # 2. Total Cities
            cities = db_main.cafes.distinct("city", {"is_deleted": {"$ne": True}})
            city_count = len([c for c in cities if c])
            
            # 3. Total Gamers
            user_count = db_main.users.count_documents({})
            web_user_count = db_main.website_users.count_documents({})
            gamer_count = user_count + web_user_count
            
            # 4. Next Upcoming Tournament (by starts_iso)
            from datetime import datetime, timezone
            now_iso = datetime.now(timezone.utc).isoformat()
            
            upcoming_cursor = db_main.tournaments.find({"starts_iso": {"$gte": now_iso}}).sort("starts_iso", 1).limit(1)
            upcoming_list = list(upcoming_cursor)
            
            if not upcoming_list:
                upcoming_cursor = db_main.tournaments.find({}).sort("starts_iso", -1).limit(1)
                upcoming_list = list(upcoming_cursor)
                
            upcoming_tournament = None
            if upcoming_list:
                t = upcoming_list[0]
                upcoming_tournament = {
                    "title": t.get("title", ""),
                    "starts": t.get("starts", ""),
                    "game": t.get("game", "")
                }
            
            return Response({
                "cafes": cafe_count,
                "cities": city_count,
                "gamers": gamer_count,
                "upcoming_tournament": upcoming_tournament
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class PartnerApplicationListCreateView(APIView):
    """
    POST /partner-applications/ — Submit a new application (Public)
    GET /partner-applications/ — List applications (Super Admin Only)
    """
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        result, status_code = partner_applications.create_partner_application_handler(request.data, request.FILES)
        return Response(result, status=status_code)

    def get(self, request):
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        result, status_code = partner_applications.get_partner_applications_handler()
        return Response(result, status=status_code)


class PartnerApplicationDetailView(APIView):
    """
    PATCH /partner-applications/<str:app_id>/ — Update application status (Super Admin Only)
    """
    def patch(self, request, app_id):
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        status_val = request.data.get("status")
        if not status_val:
            return Response({"error": "Status is required"}, status=status.HTTP_400_BAD_REQUEST)
        result, status_code = partner_applications.update_partner_application_status_handler(app_id, status_val)
        return Response(result, status=status_code)


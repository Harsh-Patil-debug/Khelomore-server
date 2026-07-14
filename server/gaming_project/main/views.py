"""
KheloMore Gaming Hub — API Views
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
from .Handlers import status_check, db_check, cafes, tournaments, bookings, rigs, payments, auth_handler, bookings_handler, auth_middleware, favorites, sessions, offers, users, partner_applications


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
    """GET /cafes/my/ — List cafes owned by the authenticated admin."""
    def get(self, request):
        print("[DEBUG CafeMyListView] Received GET /cafes/my/")
        
        # 1. Try super admin first
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if not error_response:
            response = cafes.get_my_cafes_handler(email, is_super_admin=True)
            if response.get("status") == "error":
                return Response(response, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            return Response(response, status=status.HTTP_200_OK)

        # 2. Fall back to normal admin/owner
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
    """GET /cafes/parse-maps-url/?url=... — Resolves and parses a Google Maps link (CORS safe)"""
    def get(self, request):
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response

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

class KheloMoreRegisterView(APIView):
    """
    POST /auth/register/
    Body: { gamertag, email, password, iv, phone, role }  — all AES-CBC encrypted (except role)
    Returns: encrypted { message, email }   — OTP sent, JWT NOT yet issued
    """
    def post(self, request):
        data = request.data
        role = data.get("role", "")
        guard_error = reject_unauthorized_super_admin_role(request, role)
        if guard_error:
            return guard_error
        result, status_code = auth_handler.khelomore_register(
            gamertag = data.get("gamertag", ""),
            email    = data.get("email", ""),
            password = data.get("password", ""),
            iv       = data.get("iv", ""),
            phone    = data.get("phone", ""),
            is_admin = check_is_admin(request),
            role     = role,
        )
        return Response(result, status=status_code)


class KheloMoreLoginView(APIView):
    """
    POST /auth/login/
    Body: { email, password, iv, role }           — AES-CBC encrypted (except role)
    Returns: encrypted { message, email }   — OTP sent, JWT NOT yet issued
    """
    def post(self, request):
        data = request.data
        role = data.get("role", "")
        # No role guard here: login can only authenticate an EXISTING super_admin document
        # (find_one + password check) — it can never create one, so there's no escalation
        # risk. Guarding it too would lock out every real super admin, since logging in
        # would then require already having super-admin credentials.
        result, status_code = auth_handler.khelomore_login(
            email    = data.get("email", ""),
            password = data.get("password", ""),
            iv       = data.get("iv", ""),
            is_admin = check_is_admin(request),
            role     = role,
        )
        return Response(result, status=status_code)


class KheloMoreVerifyOTPView(APIView):
    """
    POST /auth/verify-otp/
    Body: { email, otp_code, iv, role }           — AES-CBC encrypted (except role)
    Returns: encrypted { token, user }      — JWT issued on success
    """
    def post(self, request):
        data = request.data
        role = data.get("role", "")
        # No role guard here either — verify-otp only completes auth for an existing
        # pending doc (created by register, which IS guarded, or by login against an
        # existing account); it cannot itself create a super_admin document.
        result, status_code = auth_handler.khelomore_verify_otp(
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
                    cookie_key = "km_super_admin_token"
                elif user_role == "admin" or role == "admin" or check_is_admin(request):
                    cookie_key = "km_admin_token"
                else:
                    cookie_key = "km_gamer_token"

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


class KheloMoreGoogleAuthView(APIView):
    """
    POST /auth/google/
    Body: { gmail, gamertag, iv }           — AES-CBC encrypted
    Returns: encrypted { token, user }      — JWT issued directly (no OTP)
    """
    def post(self, request):
        data = request.data
        result, status_code = auth_handler.khelomore_google_auth(
            gmail    = data.get("gmail", ""),
            gamertag = data.get("gamertag", ""),
            iv       = data.get("iv", ""),
            is_admin = check_is_admin(request),
        )
        return Response(result, status=status_code)


class KheloMoreResendOTPView(APIView):
    """
    POST /auth/resend-otp/
    Body: { email, iv, role }
    Re-generates and resends OTP. Returns encrypted success message.
    """
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
        from datetime import datetime, timedelta

        is_admin = check_is_admin(request)
        coll = auth_handler.get_user_collection(is_admin, role)

        if (is_admin or role == "admin") and role != "super_admin":
            cafe_exists = db_main.cafes.find_one({"owner_email": dec_email, "is_deleted": {"$ne": True}})
            if not cafe_exists:
                return Response({"error": "This account is not associated with any registered gaming cafe. Access denied."}, status=403)

        user = coll.find_one({"email": dec_email})
        if not user:
            return Response({"error": "No account found for this email."}, status=404)

        otp_code   = str(random.randint(100000, 999999))
        otp_expiry = datetime.now(auth_handler.IST) + timedelta(minutes=10)
        coll.update_one(
            {"_id": user["_id"]},
            {"$set": {"otp_code": otp_code, "otp_expiry": otp_expiry}, "$unset": {"otp_attempts": ""}}
        )
        gamertag = user.get("gamertag") or user.get("first_name", "PLAYER")
        send_otp_email(dec_email, otp_code, gamertag=gamertag, purpose="resend")

        enc_resp, new_iv = auth_handler.encrypt_data(
            '{"message": "New OTP sent to your email."}',
            auth_handler.ENCRYPTION_KEY
        )
        return Response({"encrypted_response": enc_resp, "iv": new_iv}, status=200)


def _is_allowed_oauth_redirect_target(target):
    """
    SECURITY: `state`/`return_url` is unauthenticated, attacker-influenceable input that
    becomes the final redirect target after Google auth completes (carrying the session
    token in the query string). Only our own app schemes and our own frontend's exact
    origin may be used — NOT an arbitrary https:// URL.
    """
    if not target:
        return False
    if target.startswith('khelomore://') or target.startswith('exp://'):
        return True
    try:
        from urllib.parse import urlparse
        parsed = urlparse(target)
        frontend = urlparse(settings.FRONTEND_URL)
        if parsed.scheme in ('http', 'https') and parsed.netloc and parsed.netloc == frontend.netloc:
            return True
        if parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1'):
            return True
    except Exception:
        pass
    return False


class KheloMoreGoogleLoginView(APIView):
    """
    GET /auth/google/login/
    Redirects to Google accounts login page.
    """
    def get(self, request):
        try:
            return_url = request.query_params.get('return_url', settings.FRONTEND_URL)
            if not _is_allowed_oauth_redirect_target(return_url):
                return Response({"error": "Unauthorized redirect target."}, status=status.HTTP_400_BAD_REQUEST)
            auth_url = (
                "https://accounts.google.com/o/oauth2/v2/auth"
                f"?client_id={settings.GOOGLE_CLIENT_ID}"
                f"&redirect_uri={settings.BACKEND_URL}/api/v1/main/auth/google/callback/"
                "&response_type=code"
                "&scope=openid%20email%20profile"
                "&access_type=offline"
                "&prompt=select_account"
                f"&state={return_url}"
            )
            return redirect(auth_url)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class KheloMoreGoogleCallbackView(APIView):
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
            is_mobile = state.startswith('khelomore://') or state.startswith('exp://')
            role = "user" if is_mobile else "website_user"
            response, status_code = auth_handler.khelomore_google_auth_code_verify(code, role=role)
            
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
                
                # CRITICAL: Django's 'redirect' blocks custom protocols like exp:// or khelomore://
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
                            response_obj.set_cookie(
                                key='km_gamer_token',
                                value=token,
                                httponly=True,
                                secure=True,
                                samesite='None',
                                max_age=30 * 24 * 3600,
                            )
                    except Exception as e:
                        print(f"[COOKIE ERROR] Failed to set Google km_gamer_token cookie: {e}")

                return response_obj
            
            return Response(response, status=status_code)
        except Exception as e:
            import traceback
            print(traceback.format_exc()) # Log to server console
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class KheloMoreUpdatePhoneView(APIView):
    """
    POST /auth/update-phone/
    Header: Authorization: Bearer <token>
    Body: { phone, iv } (AES-CBC encrypted phone)
    Returns: encrypted { message, user }
    """
    def post(self, request):
        email, error_response = auth_middleware.authenticate_request(request)
        if error_response:
            return error_response
            
        data = request.data
        result, status_code = auth_handler.khelomore_update_phone(
            email           = email,
            phone_encrypted = data.get("phone", ""),
            iv              = data.get("iv", ""),
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
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        response = tournaments.update_tournament_handler(tournament_id, request.data, request.FILES)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)

    def delete(self, request, tournament_id):
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

    def put(self, request, rig_id):
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        response = rigs.update_rig_handler(rig_id, request.data)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)

    def delete(self, request, rig_id):
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        response = rigs.delete_rig_handler(rig_id)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


class RigReserveView(APIView):
    """POST /rigs/<id>/reserve/ — Create an admin reservation for specific slots."""
    def post(self, request, rig_id):
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
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        response = cafes.update_cafe_handler(cafe_id, request.data)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)

    def delete(self, request, cafe_id):
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


class BookingDetailView(APIView):
    """PUT /bookings/<id>/ — Update, DELETE /bookings/<id>/ — Cancel/Free slot"""
    def put(self, request, booking_id):
        email, error_response, is_privileged = authenticate_booking_access(request, booking_id)
        if error_response:
            return error_response

        response = bookings.update_booking_handler(booking_id, request.data, allow_payment_status_change=is_privileged)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)

    def delete(self, request, booking_id):
        email, error_response, _ = authenticate_booking_access(request, booking_id)
        if error_response:
            return error_response

        response = bookings.delete_booking_handler(booking_id)
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


def authenticate_admin_owner(request, cafe_id):
    """
    Validates that the request is authenticated by:
    1. A JWT token belonging to the owner of the specified cafe, OR
    2. A super admin token (static or dynamic super_admin JWT).
    Returns (email, None) if successful.
    Returns (None, Response) if unauthorized or forbidden.
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
        rig = db.rigs.find_one({"_id": ObjectId(system_id)})
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
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        cafe_id = request.query_params.get("cafe_id")
        result, status_code = offers.get_offers_handler(cafe_id=cafe_id)
        return Response(result, status=status_code)

    def post(self, request):
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        result, status_code = offers.create_offer_handler(request.data)
        return Response(result, status=status_code)


class OfferDetailView(APIView):
    """PATCH /offers/<offer_id>/ — toggle/update, DELETE /offers/<offer_id>/ — delete (admin)"""
    def patch(self, request, offer_id):
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
        if error_response:
            return error_response
        result, status_code = offers.update_offer_handler(offer_id, request.data)
        return Response(result, status=status_code)

    def delete(self, request, offer_id):
        email, error_response = auth_middleware.authenticate_super_admin_request(request)
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


class KheloMoreLogoutView(APIView):
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
            for cookie_name in ('km_gamer_token', 'km_admin_token', 'km_super_admin_token'):
                token = request.COOKIES.get(cookie_name)
                if token:
                    break

        if token:
            try:
                auth_handler.revoke_token(token)
            except Exception as e:
                print(f"[LOGOUT] Token revocation failed: {e}")

        response_obj = Response({"message": "Logout successful"}, status=status.HTTP_200_OK)
        for cookie_name in ('km_gamer_token', 'km_admin_token', 'km_super_admin_token'):
            response_obj.set_cookie(
                key=cookie_name,
                value='',
                max_age=0,
                httponly=True,
                secure=True,
                samesite='None',
            )
        return response_obj


class KheloMoreMeView(APIView):
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
        if request.COOKIES.get('km_admin_token'):
            lookup_order = [(db_main.admins, "admin"), (db_main.website_users, "website_user"), (db_main.users, "user")]
        elif request.COOKIES.get('km_gamer_token'):
            lookup_order = [(db_main.users, "user"), (db_main.website_users, "website_user"), (db_main.admins, "admin")]
        else:
            lookup_order = [(db_main.website_users, "website_user"), (db_main.users, "user"), (db_main.admins, "admin")]

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
            "rank":           user.get("rank", "Recruit PRO I"),
            "xp":             user.get("xp", 0),
            "role":           user.get("role", role),
            "phone":          decrypt_phone_field(user.get("phone", "")),
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


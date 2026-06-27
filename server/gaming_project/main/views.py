"""
KheloMore Gaming Hub — API Views
─────────────────────────────────────────────────────────────────────────────
All views are thin wrappers. Business logic lives exclusively in Handlers/.
Each View calls a handler function and returns the result as a DRF Response.
─────────────────────────────────────────────────────────────────────────────
"""

from django.shortcuts import redirect
from django.conf import settings
from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings
from .Handlers import status_check, db_check, cafes, tournaments, bookings, rigs, payments, auth_handler, bookings_handler, auth_middleware


# ── Status ─────────────────────────────────────────────────────────────────────

class StatusCheckView(APIView):
    """GET /status/ — Server health check (public)"""
    def get(self, request):
        response = status_check.status_check()
        return Response(response)


# ── Database ───────────────────────────────────────────────────────────────────

class DbCheckView(APIView):
    """GET /db/ — MongoDB read/write connectivity check (public)"""
    def get(self, request):
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
        response = cafes.get_cafes_handler(latitude=latitude, longitude=longitude)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(response, status=status.HTTP_200_OK)

    def post(self, request):
        success, error_response = auth_middleware.authenticate_admin_request(request)
        if error_response:
            return error_response
        response = cafes.create_cafe_handler(request.data, request.FILES)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_201_CREATED)


# ── Auth ───────────────────────────────────────────────────────────────────────

def check_is_admin(request):
    auth_header = request.headers.get('Authorization') or request.META.get('HTTP_AUTHORIZATION')
    expected_token = getattr(settings, 'ADMIN_TOKEN', '')
    print(f"[DEBUG check_is_admin] Received Header: {auth_header}")
    print(f"[DEBUG check_is_admin] Expected Token: {expected_token}")
    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header.split(' ')[1].strip()
        is_match = (token == expected_token)
        print(f"[DEBUG check_is_admin] Extracted Token: {token} | Match: {is_match}")
        return is_match
    return False

class KheloMoreRegisterView(APIView):
    """
    POST /auth/register/
    Body: { gamertag, email, password, iv }  — all AES-CBC encrypted
    Returns: encrypted { message, email }   — OTP sent, JWT NOT yet issued
    """
    def post(self, request):
        data = request.data
        result, status_code = auth_handler.khelomore_register(
            gamertag = data.get("gamertag", ""),
            email    = data.get("email", ""),
            password = data.get("password", ""),
            iv       = data.get("iv", ""),
            is_admin = check_is_admin(request),
        )
        return Response(result, status=status_code)


class KheloMoreLoginView(APIView):
    """
    POST /auth/login/
    Body: { email, password, iv }           — AES-CBC encrypted
    Returns: encrypted { message, email }   — OTP sent, JWT NOT yet issued
    """
    def post(self, request):
        data = request.data
        result, status_code = auth_handler.khelomore_login(
            email    = data.get("email", ""),
            password = data.get("password", ""),
            iv       = data.get("iv", ""),
            is_admin = check_is_admin(request),
        )
        return Response(result, status=status_code)


class KheloMoreVerifyOTPView(APIView):
    """
    POST /auth/verify-otp/
    Body: { email, otp_code, iv }           — AES-CBC encrypted
    Returns: encrypted { token, user }      — JWT issued on success
    """
    def post(self, request):
        data = request.data
        result, status_code = auth_handler.khelomore_verify_otp(
            email    = data.get("email", ""),
            otp_code = data.get("otp_code", ""),
            iv       = data.get("iv", ""),
            is_admin = check_is_admin(request),
        )
        return Response(result, status=status_code)


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
    Body: { email, iv }
    Re-generates and resends OTP. Returns encrypted success message.
    """
    def post(self, request):
        data     = request.data
        iv       = data.get("iv", "")
        email_enc = data.get("email", "")
        try:
            dec_email = auth_handler.decrypt_data(email_enc, iv).strip().lower()
        except Exception as e:
            return Response({"error": f"Decryption failed: {str(e)}"}, status=400)

        from .Handlers.db_connection import db_main
        from .Handlers.email_handler import send_otp_email
        import random
        from datetime import datetime, timedelta

        is_admin = check_is_admin(request)
        coll = db_main.admins if is_admin else db_main.users
        user = coll.find_one({"email": dec_email})
        if not user:
            return Response({"error": "No account found for this email."}, status=404)

        otp_code   = str(random.randint(100000, 999999))
        otp_expiry = datetime.now(auth_handler.IST) + timedelta(minutes=10)
        coll.update_one(
            {"_id": user["_id"]},
            {"$set": {"otp_code": otp_code, "otp_expiry": otp_expiry}}
        )
        gamertag = user.get("gamertag") or user.get("first_name", "PLAYER")
        send_otp_email(dec_email, otp_code, gamertag=gamertag, purpose="resend")

        enc_resp, new_iv = auth_handler.encrypt_data(
            '{"message": "New OTP sent to your email."}',
            auth_handler.ENCRYPTION_KEY
        )
        return Response({"encrypted_response": enc_resp, "iv": new_iv}, status=200)


class KheloMoreGoogleLoginView(APIView):
    """
    GET /auth/google/login/
    Redirects to Google accounts login page.
    """
    def get(self, request):
        try:
            return_url = request.query_params.get('return_url', settings.FRONTEND_URL)
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
            
            response, status_code = auth_handler.khelomore_google_auth_code_verify(code)
            
            if status_code == 200:
                # SECURITY: Only allow redirects to trusted app protocols
                allowed_schemes = ['khelomore://', 'exp://']
                if not any(state.startswith(scheme) for scheme in allowed_schemes):
                    return Response({"error": "Boutique Security: Unauthorized redirect protocol detected."}, status=status.HTTP_403_FORBIDDEN)

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
                return response_obj
            
            return Response(response, status=status_code)
        except Exception as e:
            import traceback
            print(traceback.format_exc()) # Log to server console
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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
            
        result, status_code = bookings_handler.get_user_bookings_handler(email)
        return Response(result, status=status_code)

    def post(self, request):
        email, error_response = auth_middleware.authenticate_request(request)
        if error_response:
            return error_response
            
        data = request.data
        result, status_code = bookings_handler.create_booking_handler(
            user_email = email,
            cafe_id    = data.get("cafe_id"),
            cafe_name  = data.get("cafe_name"),
            zone       = data.get("zone"),
            date       = data.get("date"),
            slots      = data.get("slots", []),
            price      = data.get("price", 0)
        )
        return Response(result, status=status_code)

# ── Tournaments ────────────────────────────────────────────────────────────────

class TournamentListCreateView(APIView):
    """GET /tournaments/ — List/seed tournaments, POST /tournaments/ — Create a new tournament with image upload"""
    parser_classes = (MultiPartParser, FormParser)

    def get(self, request):
        response = tournaments.get_tournaments_handler()
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(response, status=status.HTTP_200_OK)


    def post(self, request):
        success, error_response = auth_middleware.authenticate_admin_request(request)
        if error_response:
            return error_response
        response = tournaments.create_tournament_handler(request.data, request.FILES)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_201_CREATED)


class TournamentToggleRegistrationView(APIView):
    """POST /tournaments/<tournament_id>/toggle-registration/ — Toggle registration open/closed (Admin action)"""
    def post(self, request, tournament_id):
        success, error_response = auth_middleware.authenticate_admin_request(request)
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
        response = tournaments.register_tournament_handler(tournament_id, request.data)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)





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
        success, error_response = auth_middleware.authenticate_admin_request(request)
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
        success, error_response = auth_middleware.authenticate_admin_request(request)
        if error_response:
            return error_response
        response = rigs.update_rig_handler(rig_id, request.data)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)

    def delete(self, request, rig_id):
        success, error_response = auth_middleware.authenticate_admin_request(request)
        if error_response:
            return error_response
        response = rigs.delete_rig_handler(rig_id)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


class CafeDetailView(APIView):
    """GET /cafes/<id>/ — Detail, PUT /cafes/<id>/ — Update, DELETE /cafes/<id>/ — Delete (not used)"""
    def get(self, request, cafe_id):
        response = cafes.get_cafe_detail_handler(cafe_id)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_404_NOT_FOUND)
        return Response(response, status=status.HTTP_200_OK)

    def put(self, request, cafe_id):
        success, error_response = auth_middleware.authenticate_admin_request(request)
        if error_response:
            return error_response
        response = cafes.update_cafe_handler(cafe_id, request.data)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


class BookingDetailView(APIView):
    """PUT /bookings/<id>/ — Update, DELETE /bookings/<id>/ — Cancel/Free slot"""
    def put(self, request, booking_id):
        email, error_response = auth_middleware.authenticate_request(request)
        if error_response:
            return error_response

        response = bookings.update_booking_handler(booking_id, request.data)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)

    def delete(self, request, booking_id):
        email, error_response = auth_middleware.authenticate_request(request)
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






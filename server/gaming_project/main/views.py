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
from .Handlers import status_check, db_check, cafes
from .Handlers import auth_handler


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
        response = cafes.create_cafe_handler(request.data, request.FILES)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_201_CREATED)


# ── Auth ───────────────────────────────────────────────────────────────────────

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

        user = db_main.users.find_one({"email": dec_email})
        if not user:
            return Response({"error": "No account found for this email."}, status=404)

        otp_code   = str(random.randint(100000, 999999))
        otp_expiry = datetime.now(auth_handler.IST) + timedelta(minutes=10)
        db_main.users.update_one(
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
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return Response({"error": "Authorization header missing or invalid"}, status=status.HTTP_401_UNAUTHORIZED)
        token = auth_header.split(' ')[1]
        try:
            email = auth_handler.verify_token(token)
        except Exception as e:
            return Response({"error": f"Invalid token: {str(e)}"}, status=status.HTTP_401_UNAUTHORIZED)
            
        result, status_code = bookings_handler.get_user_bookings_handler(email)
        return Response(result, status=status_code)

    def post(self, request):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return Response({"error": "Authorization header missing or invalid"}, status=status.HTTP_401_UNAUTHORIZED)
        token = auth_header.split(' ')[1]
        try:
            email = auth_handler.verify_token(token)
        except Exception as e:
            return Response({"error": f"Invalid token: {str(e)}"}, status=status.HTTP_401_UNAUTHORIZED)
            
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




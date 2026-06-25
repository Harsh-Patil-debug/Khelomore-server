"""
KheloMore Gaming Hub — API Views
─────────────────────────────────────────────────────────────────────────────
All views are thin wrappers. Business logic lives exclusively in Handlers/.
Each View calls a handler function and returns the result as a DRF Response.
─────────────────────────────────────────────────────────────────────────────
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings
from .Handlers import status_check, db_check, cafes, tournaments, bookings, rigs, payments


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
        response = tournaments.create_tournament_handler(request.data, request.FILES)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_201_CREATED)


class TournamentToggleRegistrationView(APIView):
    """POST /tournaments/<tournament_id>/toggle-registration/ — Toggle registration open/closed (Admin action)"""
    def post(self, request, tournament_id):
        response = tournaments.toggle_registration_handler(tournament_id)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


class TournamentRegisterView(APIView):
    """POST /tournaments/<str:tournament_id>/register/ — Register for a tournament"""
    def post(self, request, tournament_id):
        response = tournaments.register_tournament_handler(tournament_id, request.data)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


# ── Bookings ──────────────────────────────────────────────────────────────────

class BookingListCreateView(APIView):
    """GET /bookings/ — List/seed bookings (with optional ?cafe_id= filter), POST /bookings/ — Create a new booking"""
    def get(self, request):
        cafe_id = request.query_params.get("cafe_id") or request.query_params.get("cafeId")
        response = bookings.get_bookings_handler(cafe_id=cafe_id)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(response, status=status.HTTP_200_OK)

    def post(self, request):
        response = bookings.create_booking_handler(request.data)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_201_CREATED)


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
        response = rigs.update_rig_handler(rig_id, request.data)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)

    def delete(self, request, rig_id):
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
        response = cafes.update_cafe_handler(cafe_id, request.data)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


class BookingDetailView(APIView):
    """PUT /bookings/<id>/ — Update, DELETE /bookings/<id>/ — Cancel/Free slot"""
    def put(self, request, booking_id):
        response = bookings.update_booking_handler(booking_id, request.data)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)

    def delete(self, request, booking_id):
        response = bookings.delete_booking_handler(booking_id)
        if response.get("status") == "error":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)
        return Response(response, status=status.HTTP_200_OK)


class RazorpayOrderCreateView(APIView):
    """POST /payments/create-order/ — Create a Razorpay Order"""
    def post(self, request):
        amount = request.data.get("amount")
        if amount is None:
            return Response({"status": "error", "message": "Missing 'amount' parameter"}, status=status.HTTP_400_BAD_REQUEST)
        response = payments.create_razorpay_order_handler(amount)
        response["key_id"] = getattr(settings, 'RAZORPAY_KEY_ID', '')
        return Response(response, status=status.HTTP_200_OK)





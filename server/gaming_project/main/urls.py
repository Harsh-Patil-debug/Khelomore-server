"""
KheloMore Gaming Hub — URL Routes
All API routes mounted under: /api/v1/main/
"""
from django.urls import path
from .views import *

app_name = 'main'

urlpatterns = [

    # ── Status ────────────────────────────────────────────────────────────────
    path('status/', StatusCheckView.as_view(), name='status'),

    # ── Database ──────────────────────────────────────────────────────────────
    path('db/', DbCheckView.as_view(), name='db_check'),

    # ── Cafes ─────────────────────────────────────────────────────────────────
    path('cafes/', CafeListCreateView.as_view(), name='cafes'),
    path('cafes/my/', CafeMyListView.as_view(), name='my_cafes'),
    path('cafes/<str:cafe_id>/', CafeDetailView.as_view(), name='cafe_detail'),

    # ── Tournaments ───────────────────────────────────────────────────────────
    path('tournaments/', TournamentListCreateView.as_view(), name='tournaments'),
    path('tournaments/registrations/', UserTournamentRegistrationsView.as_view(), name='user_tournament_registrations'),
    path('tournaments/<str:tournament_id>/toggle-registration/', TournamentToggleRegistrationView.as_view(), name='toggle_registration'),
    path('tournaments/<str:tournament_id>/register/', TournamentRegisterView.as_view(), name='register_tournament'),

    # ── Bookings ──────────────────────────────────────────────────────────────
    path('bookings/', BookingListCreateView.as_view(), name='bookings'),
    path('bookings/<str:booking_id>/', BookingDetailView.as_view(), name='booking_detail'),

    # ── Hardware Rigs ─────────────────────────────────────────────────────────
    path('rigs/', RigListCreateView.as_view(), name='rigs'),
    path('rigs/<str:rig_id>/', RigDetailView.as_view(), name='rig_detail'),
    path('rigs/<str:rig_id>/reserve/', RigReserveView.as_view(), name='rig_reserve'),

    # ── Payments ──────────────────────────────────────────────────────────────
    path('payments/create-order/', RazorpayOrderCreateView.as_view(), name='create_razorpay_order'),

    # ── User Favorites ────────────────────────────────────────────────────────
    path('users/favorites/', UserFavoritesView.as_view(), name='user_favorites'),

    # ── Auth (traditional — OTP mandatory) ────────────────────────────────────
    path('auth/register/',   KheloMoreRegisterView.as_view(),  name='auth_register'),
    path('auth/login/',      KheloMoreLoginView.as_view(),     name='auth_login'),
    path('auth/verify-otp/', KheloMoreVerifyOTPView.as_view(), name='auth_verify_otp'),
    path('auth/resend-otp/', KheloMoreResendOTPView.as_view(), name='auth_resend_otp'),

    # ── Auth (Google — JWT direct, no OTP) ────────────────────────────────────
    path('auth/google/',     KheloMoreGoogleAuthView.as_view(), name='auth_google'),
    path('auth/google/login/', KheloMoreGoogleLoginView.as_view(), name='auth_google_login'),
    path('auth/google/callback/', KheloMoreGoogleCallbackView.as_view(), name='auth_google_callback'),

    # ── Bookings ──────────────────────────────────────────────────────────────
    path('bookings/slots/',  BookedSlotsView.as_view(),        name='bookings_slots'),

    # ── Sessions ──────────────────────────────────────────────────────────────
    path('sessions/', SessionListCreateView.as_view(), name='sessions'),
    path('sessions/<str:session_id>/<str:action>/', SessionActionView.as_view(), name='session_action'),
]


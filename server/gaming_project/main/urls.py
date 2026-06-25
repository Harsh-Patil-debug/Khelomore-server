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
    path('bookings/',        BookingListCreateView.as_view(),  name='bookings_list_create'),

]


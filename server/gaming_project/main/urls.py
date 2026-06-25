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
    path('cafes/<str:cafe_id>/', CafeDetailView.as_view(), name='cafe_detail'),

    # ── Tournaments ───────────────────────────────────────────────────────────
    path('tournaments/', TournamentListCreateView.as_view(), name='tournaments'),
    path('tournaments/<str:tournament_id>/toggle-registration/', TournamentToggleRegistrationView.as_view(), name='toggle_registration'),
    path('tournaments/<str:tournament_id>/register/', TournamentRegisterView.as_view(), name='register_tournament'),

    # ── Bookings ──────────────────────────────────────────────────────────────
    path('bookings/', BookingListCreateView.as_view(), name='bookings'),
    path('bookings/<str:booking_id>/', BookingDetailView.as_view(), name='booking_detail'),

    # ── Hardware Rigs ─────────────────────────────────────────────────────────
    path('rigs/', RigListCreateView.as_view(), name='rigs'),
    path('rigs/<str:rig_id>/', RigDetailView.as_view(), name='rig_detail'),

    # ── Payments ──────────────────────────────────────────────────────────────
    path('payments/create-order/', RazorpayOrderCreateView.as_view(), name='create_razorpay_order'),

]


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
from .Handlers import status_check


# ── JWT Auth Guard ─────────────────────────────────────────────────────────────


class StatusCheckView(APIView):
    """GET /status/ — Server health check (public)"""
    def get(self, request):
        response = status_check.status_check()
        return Response(response)


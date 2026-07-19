# middleware.py
# CSRF-style protection for cookie-authenticated requests.
#
# DRF's APIView bypasses Django's built-in CsrfViewMiddleware by design, and this app's
# auth cookies (km_gamer_token / km_website_token / km_admin_token / km_super_admin_token)
# are set with SameSite=None so they are sent on cross-site requests too (required because
# the web frontends live on separate origins from this API). Without this middleware, any
# website could trigger authenticated state-changing requests using a logged-in visitor's
# cookies.
#
# Only enforced once settings.ALLOWED_ORIGINS is configured (see CORS_ALLOWED_ORIGINS env
# var) — requests authenticated purely via an Authorization header are unaffected, since a
# foreign page cannot attach a victim's bearer token to its own request.

from django.conf import settings
from django.http import JsonResponse

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
AUTH_COOKIE_NAMES = ("km_gamer_token", "km_website_token", "km_admin_token", "km_super_admin_token")


class OriginValidationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._is_forged_cross_origin_request(request):
            return JsonResponse({"error": "Cross-origin request rejected."}, status=403)
        return self.get_response(request)

    def _is_forged_cross_origin_request(self, request):
        allowed_origins = getattr(settings, "ALLOWED_ORIGINS", [])
        if not allowed_origins:
            return False  # not configured yet — don't block traffic until it is

        if request.method not in UNSAFE_METHODS:
            return False

        if not any(request.COOKIES.get(name) for name in AUTH_COOKIE_NAMES):
            return False  # not cookie-authenticated — Authorization-header requests are safe

        source = request.headers.get("Origin") or request.headers.get("Referer")
        if not source:
            return True  # cookie-authenticated write with no Origin/Referer at all — reject

        return not any(source == o or source.startswith(o + "/") for o in allowed_origins)

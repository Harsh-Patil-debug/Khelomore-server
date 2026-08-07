# middleware.py
# CSRF-style protection for cookie-authenticated requests.
#
# DRF's APIView bypasses Django's built-in CsrfViewMiddleware by design, and this app's
# auth cookies (bmc_gamer_token / bmc_website_token / bmc_admin_token / bmc_super_admin_token)
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
AUTH_COOKIE_NAMES = ("bmc_gamer_token", "bmc_website_token", "bmc_admin_token", "bmc_super_admin_token")


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

        # BUG FIX: a request can carry an auth cookie without actually being
        # cookie-authenticated. Android's native networking (OkHttp, used under
        # React Native's fetch/XHR) persists any Set-Cookie a server ever sends and
        # silently re-attaches it to later requests to the same host — the mobile app
        # never reads or relies on this cookie, it only uses the Authorization header,
        # but the incidental cookie was enough to trip this check and reject
        # legitimate app requests (e.g. self-service account deletion) with no real
        # forgery involved. A valid Bearer header on its own is already unforgeable by
        # a third-party site, so its presence is checked first and makes the request
        # safe regardless of what cookies happen to be riding along.
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer ") and len(auth_header) > len("Bearer "):
            return False

        if not any(request.COOKIES.get(name) for name in AUTH_COOKIE_NAMES):
            return False  # not cookie-authenticated — Authorization-header requests are safe

        source = request.headers.get("Origin") or request.headers.get("Referer")
        if not source:
            # BUG FIX: this used to reject outright, which - combined with the Android
            # incidental-cookie issue above - blocked ALL native app traffic on ANY
            # unsafe-method endpoint that runs before a Bearer token exists yet (e.g.
            # signup, before the account/token is even created). The "reject when
            # missing" defensiveness doesn't actually buy real protection: a genuine
            # forged cross-origin request from a browser (fetch, XHR, or even a plain
            # HTML form POST) always carries an Origin header - browsers attach it
            # automatically for any cross-origin request and JS cannot suppress it,
            # unlike Referer which a page can hide via referrer-policy. A request with
            # neither header essentially can only be non-browser (native app, direct
            # API call) traffic, which this check was never meant to restrict.
            return False

        return not any(source == o or source.startswith(o + "/") for o in allowed_origins)

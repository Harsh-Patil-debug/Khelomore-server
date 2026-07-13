from django.conf import settings
from rest_framework.response import Response
from rest_framework import status
from . import auth_handler

def authenticate_request(request):
    """
    Validates the authorization token in the request header or HttpOnly cookie.
    Returns (email, None) if successful.
    Returns (None, Response) if validation fails.
    """
    auth_header = request.headers.get('Authorization')
    token = None
    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header.split(' ')[1].strip()
    else:
        # Fallback to HttpOnly cookie for web users / admins
        token = request.COOKIES.get('km_admin_token') or request.COOKIES.get('km_gamer_token')

    print(f"[DEBUG auth_middleware] Incoming Token (Header/Cookie): {token is not None}")
    if not token:
        print("[DEBUG auth_middleware] Authorization token missing.")
        return None, Response({"error": "Authorization token missing or invalid"}, status=status.HTTP_401_UNAUTHORIZED)
    
    try:
        email = auth_handler.verify_token(token)
        print(f"[DEBUG auth_middleware] Token verified successfully for email: {email}")
        return email, None
    except Exception as e:
        print(f"[DEBUG auth_middleware] Token verification failed: {e}")
        return None, Response({"error": f"Invalid token: {str(e)}"}, status=status.HTTP_401_UNAUTHORIZED)

def authenticate_admin_request(request):
    """
    Validates the static admin token in the request header.
    Returns (True, None) if successful.
    Returns (False, Response) if validation fails.
    """
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return False, Response({"error": "Authorization header missing or invalid"}, status=status.HTTP_401_UNAUTHORIZED)
        
    if not auth_header.startswith('Bearer '):
        return False, Response({"error": "Authorization header must use Bearer scheme"}, status=status.HTTP_401_UNAUTHORIZED)

    parts = auth_header.split(' ')
    if len(parts) < 2:
        return False, Response({"error": "Authorization header is malformed"}, status=status.HTTP_401_UNAUTHORIZED)
    token = parts[1].strip()
    
    expected_token = getattr(settings, 'ADMIN_TOKEN', '')
    if not expected_token or token != expected_token:
        return False, Response({"error": "Invalid admin authorization token"}, status=status.HTTP_401_UNAUTHORIZED)
        
    return True, None


def authenticate_super_admin_request(request):
    """
    Validates either the static ADMIN_TOKEN or a dynamic super_admin JWT.
    Returns (email, None) if successful.
    Returns (None, Response) if validation fails.
    """
    auth_header = request.headers.get('Authorization') or request.META.get('HTTP_AUTHORIZATION')
    token = None
    if auth_header and auth_header.startswith('Bearer '):
        parts = auth_header.split(' ')
        if len(parts) >= 2:
            token = parts[1].strip()
    else:
        # Fallback to HttpOnly cookie for super admins
        token = request.COOKIES.get('km_super_admin_token')

    print(f"[DEBUG auth_middleware] Incoming Super Admin Token: {token is not None}")
    if not token:
        return None, Response({"error": "Authorization token missing or invalid"}, status=status.HTTP_401_UNAUTHORIZED)

    # 1. Try static token fallback
    expected_static = getattr(settings, 'ADMIN_TOKEN', '')
    if expected_static and token == expected_static:
        print("[DEBUG auth_middleware] Static Super Admin token matched.")
        return "super_admin_static", None

    # 2. Try dynamic JWT token
    try:
        email = auth_handler.verify_token(token)
        from .db_connection import db_main
        # Look up in the super_admin collection
        super_admin = db_main.super_admin.find_one({"email": email})
        if not super_admin:
            print(f"[DEBUG auth_middleware] User {email} not found in super_admin collection.")
            return None, Response({"error": "Access denied. Not a super admin."}, status=status.HTTP_403_FORBIDDEN)
        
        if super_admin.get("status") != "Active":
            print(f"[DEBUG auth_middleware] Super Admin {email} is not Active.")
            return None, Response({"error": "Super admin account is not active."}, status=status.HTTP_403_FORBIDDEN)

        print(f"[DEBUG auth_middleware] Dynamic Super Admin token verified for: {email}")
        return email, None
    except Exception as e:
        print(f"[DEBUG auth_middleware] Super Admin verification failed: {e}")
        return None, Response({"error": f"Invalid token: {str(e)}"}, status=status.HTTP_401_UNAUTHORIZED)



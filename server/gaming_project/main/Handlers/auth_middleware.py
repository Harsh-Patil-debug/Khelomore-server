from django.conf import settings
from rest_framework.response import Response
from rest_framework import status
from . import auth_handler

def authenticate_request(request):
    """
    Validates the authorization token in the request header.
    Returns (email, None) if successful.
    Returns (None, Response) if validation fails.
    """
    auth_header = request.headers.get('Authorization')
    print(f"[DEBUG auth_middleware] Incoming Authorization Header: {auth_header}")
    if not auth_header:
        print("[DEBUG auth_middleware] Authorization header missing.")
        return None, Response({"error": "Authorization header missing or invalid"}, status=status.HTTP_401_UNAUTHORIZED)
        
    if not auth_header.startswith('Bearer '):
        print("[DEBUG auth_middleware] Authorization header does not use Bearer scheme.")
        return None, Response({"error": "Authorization header must use Bearer scheme"}, status=status.HTTP_401_UNAUTHORIZED)

    token = auth_header.split(' ')[1].strip()
    
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


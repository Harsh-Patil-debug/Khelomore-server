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
    if not auth_header:
        return None, Response({"error": "Authorization header missing or invalid"}, status=status.HTTP_401_UNAUTHORIZED)
        
    if not auth_header.startswith('Bearer '):
        return None, Response({"error": "Authorization header must use Bearer scheme"}, status=status.HTTP_401_UNAUTHORIZED)

    token = auth_header.split(' ')[1].strip()
    
    try:
        email = auth_handler.verify_token(token)
        return email, None
    except Exception as e:
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

    token = auth_header.split(' ')[1].strip()
    
    expected_token = getattr(settings, 'ADMIN_TOKEN', '')
    if not expected_token or token != expected_token:
        return False, Response({"error": "Invalid admin authorization token"}, status=status.HTTP_401_UNAUTHORIZED)
        
    return True, None


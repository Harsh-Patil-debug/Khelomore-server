"""
Django settings for KheloMore Gaming Hub backend.
"""
# Trigger reload

from pathlib import Path
import os
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _require_env(name):
    """
    SECURITY: fail closed instead of silently falling back to a hardcoded default secret.
    A hardcoded fallback here is visible to anyone with repo access and would grant
    authentication/session-forging capability in any deployment that forgets to set it.
    """
    value = os.getenv(name)
    if not value:
        raise ImproperlyConfigured(f"Required environment variable '{name}' is not set.")
    return value


SECRET_KEY = _require_env('DJANGO_SECRET_KEY')

# SECURITY: default to DEBUG=False. Verbose Django error pages leak secrets, environment
# details, and stack traces to whoever triggers a 500 — never let that be the silent
# default. Set DEBUG=True explicitly in .env for local development.
DEBUG = os.getenv('DEBUG', 'False') == 'True'

_allowed_hosts_env = os.getenv('ALLOWED_HOSTS', '')
if _allowed_hosts_env:
    ALLOWED_HOSTS = [h.strip() for h in _allowed_hosts_env.split(',') if h.strip()]
elif DEBUG:
    ALLOWED_HOSTS = ['*']
else:
    raise ImproperlyConfigured("ALLOWED_HOSTS must be set (comma-separated) via env when DEBUG=False.")

NGROK_DOMAIN = os.getenv('NGROK_DOMAIN', 'twisting-stove-chief.ngrok-free.dev')


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'gaming_project.main',
    'rest_framework',
    'corsheaders',
]

REST_FRAMEWORK = {
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '2000/minute',   # Increased from 60 for development
        'user': '10000/minute',  # Increased from 300 for development
        # Deliberately much tighter than the defaults above — applied via ScopedRateThrottle
        # to login/register/verify-otp/resend-otp specifically. Those endpoints are the
        # credential-guessing attack surface; the loose defaults exist for general API
        # traffic (dashboards polling, image-heavy list views), not this.
        'auth': '20/minute',
    }
}

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'gaming_project.main.middleware.OriginValidationMiddleware',
]

# CORS
# SECURITY: auth is cookie-based (km_gamer_token / km_admin_token / km_super_admin_token,
# all SameSite=None so they're sent cross-site) with CORS_ALLOW_CREDENTIALS=True. Combined
# with a wildcard origin, ANY website could read authenticated API responses on behalf of a
# logged-in user/admin/super-admin via a simple cross-origin fetch(). Set CORS_ALLOWED_ORIGINS
# (comma-separated, e.g. https://app.khelomore.com,https://admin.khelomore.com) once real
# frontend domains are known — falls back to wildcard only while DEBUG=True.
_cors_origins_env = os.getenv('CORS_ALLOWED_ORIGINS', '')
CORS_ALLOWED_ORIGINS = []
CORS_ALLOW_ALL_ORIGINS = False
if _cors_origins_env:
    CORS_ALLOWED_ORIGINS = [o.strip() for o in _cors_origins_env.split(',') if o.strip()]
elif DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True
else:
    raise ImproperlyConfigured("CORS_ALLOWED_ORIGINS must be set (comma-separated) via env when DEBUG=False.")
CORS_ALLOW_CREDENTIALS = True

from corsheaders.defaults import default_headers

CORS_ALLOW_HEADERS = list(default_headers) + [
    "ngrok-skip-browser-warning",
]

CORS_ALLOW_METHODS = [
    "DELETE",
    "GET",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
]

CORS_EXPOSE_HEADERS = ["Content-Type", "X-CSRFToken"]

APPEND_SLASH = False

ROOT_URLCONF = 'server.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'server.wsgi.application'


# Database — SQLite for Django internals (sessions, admin)
# MongoDB is used directly via pymongo in Handlers/db_connection.py
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_L10N = True
USE_TZ = True


# Static files
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# App URLs
BACKEND_URL = os.getenv('BACKEND_URL', 'http://localhost:8000')
FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:5173')
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')

# Razorpay Keys
RAZORPAY_KEY_ID = os.getenv('RAZORPAY_KEY_ID', '')
RAZORPAY_KEY_SECRET = os.getenv('RAZORPAY_KEY_SECRET', '')

# Admin Security
ADMIN_TOKEN = _require_env('ADMIN_TOKEN')

# Reused by OriginValidationMiddleware (gaming_project/main/middleware.py) for CSRF-style
# protection on cookie-authenticated state-changing requests. Empty (i.e. not enforced)
# until CORS_ALLOWED_ORIGINS is explicitly configured.
ALLOWED_ORIGINS = CORS_ALLOWED_ORIGINS


# ── Security headers (skipped in DEBUG so local http:// dev keeps working) ─────────────────
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True


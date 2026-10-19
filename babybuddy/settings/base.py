import json
import os
import dj_database_url

from django.utils.translation import gettext_lazy as _
from dotenv import load_dotenv, find_dotenv


# Convert common string values to boolean.
def strtobool(val):
    val = val.lower()
    if val in ("y", "yes", "t", "true", "on", "1"):
        return 1
    elif val in ("n", "no", "f", "false", "off", "0"):
        return 0
    else:
        raise ValueError("invalid truth value {!r}".format(val))


# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Environment variables
# Check for and load environment variables from a .env file.

load_dotenv(find_dotenv())

# Required settings
# No default: list the hostnames it answers to (Baby Buddy security fix after 2.11.0).
ALLOWED_HOSTS = [x.strip() for x in os.environ.get("ALLOWED_HOSTS", "").split(",") if x.strip()]
SECRET_KEY = os.environ.get("SECRET_KEY") or None
DEBUG = bool(strtobool(os.environ.get("DEBUG") or "False"))

# Reminders (browser push): checked by the web server every few minutes. PUSH_CONTACT goes in each push
# message so push services can reach the operator (a mailto: or https: address).
HOUSETRAINED_REMINDERS = bool(strtobool(os.environ.get("REMINDERS") or "True"))
HOUSETRAINED_REMINDER_THREAD = HOUSETRAINED_REMINDERS  # the background checker (off in tests)
HOUSETRAINED_PUSH_CONTACT = os.environ.get("PUSH_CONTACT") or "mailto:noreply@example.com"
# Where people can find Housetrained's source (shown in the user menu when set).
HOUSETRAINED_SOURCE_URL = os.environ.get("HOUSETRAINED_SOURCE_URL", "https://github.com/viciousnemesis/housetrained")

# Applications
# https://docs.djangoproject.com/en/5.0/ref/applications/

INSTALLED_APPS = [
    "babybuddy.apps.BabyBuddyConfig",
    "corsheaders",
    "pets.apps.PetsConfig",
    "axes",
    "django_filters",
    "rest_framework",
    "rest_framework.authtoken",
    "widget_tweaks",
    "imagekit",
    "dbsettings",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
]

# Middleware
# https://docs.djangoproject.com/en/5.0/ref/middleware/

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "babybuddy.middleware.RollingSessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "babybuddy.middleware.UserTimezoneMiddleware",
    "pets.clock.ClockMiddleware",
    "pets.reminders.ReminderMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "babybuddy.middleware.AccessExpiry",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
]


# URL dispatcher
# https://docs.djangoproject.com/en/5.0/topics/http/urls/

ROOT_URLCONF = "babybuddy.urls"


# Templates
# https://docs.djangoproject.com/en/5.0/ref/settings/#std:setting-TEMPLATES

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": ["babybuddy/templates", "babybuddy/templates/error"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "pets.context_processors.pets",
            ],
        },
    },
]


# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases

if os.getenv("DATABASE_URL"):
    DATABASES = {"default": dj_database_url.config()}
else:
    config = {
        "ENGINE": os.getenv("DB_ENGINE") or "django.db.backends.sqlite3",
        "NAME": os.getenv("DB_NAME") or os.path.join(BASE_DIR, "data/db.sqlite3"),
    }
    if os.getenv("DB_USER"):
        config["USER"] = os.getenv("DB_USER")
    if os.environ.get("DB_PASSWORD") or os.environ.get("POSTGRES_PASSWORD"):
        config["PASSWORD"] = os.environ.get("DB_PASSWORD") or os.environ.get(
            "POSTGRES_PASSWORD"
        )
    if os.getenv("DB_HOST"):
        config["HOST"] = os.getenv("DB_HOST")
    if os.getenv("DB_PORT"):
        config["PORT"] = os.getenv("DB_PORT")
    if os.getenv("DB_OPTIONS"):
        config["OPTIONS"] = json.loads(os.getenv("DB_OPTIONS", "{}"))
    DATABASES = {"default": config}


# Cache
# https://docs.djangoproject.com/en/5.0/topics/cache/

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "cache_default",
    }
}


# WGSI
# https://docs.djangoproject.com/en/5.0/howto/deployment/wsgi/

WSGI_APPLICATION = "babybuddy.wsgi.application"


# Authentication
# https://docs.djangoproject.com/en/5.0/topics/auth/default/

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesBackend",
    "django.contrib.auth.backends.ModelBackend",
]

LOGIN_REDIRECT_URL = "babybuddy:root-router"

LOGIN_URL = "babybuddy:login"

LOGOUT_REDIRECT_URL = "babybuddy:login"

REVERSE_PROXY_AUTH = bool(strtobool(os.environ.get("REVERSE_PROXY_AUTH") or "False"))

# Use remote user middleware when reverse proxy auth is enabled.
if REVERSE_PROXY_AUTH:
    # Must appear AFTER AuthenticationMiddleware.
    MIDDLEWARE.append("babybuddy.middleware.CustomRemoteUser")
    AUTHENTICATION_BACKENDS.append("django.contrib.auth.backends.RemoteUserBackend")


# Timezone
# https://docs.djangoproject.com/en/5.0/topics/i18n/timezones/

USE_TZ = True

TIME_ZONE = os.environ.get("TIME_ZONE") or "UTC"

# Internationalization
# https://docs.djangoproject.com/en/5.0/topics/i18n/

USE_I18N = True

LANGUAGE_CODE = "en-US"

LANGUAGES = [("en-US", _("English"))]  # English only for now


# Format localization
# https://docs.djangoproject.com/en/5.0/topics/i18n/formatting/

FORMAT_MODULE_PATH = ["babybuddy.formats"]


# Storage
# https://docs.djangoproject.com/en/5.0/ref/files/storage/
# https://docs.djangoproject.com/en/5.0/ref/settings/#std-setting-STORAGES

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.0/howto/static-files/
# http://whitenoise.evans.io/en/stable/django.html

STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
]

STATIC_URL = os.path.join(os.environ.get("SUB_PATH") or "", "static/")

STATIC_ROOT = os.path.join(BASE_DIR, "static")

WHITENOISE_ROOT = os.path.join(BASE_DIR, "static", "babybuddy", "root")


# Media files (User uploaded content)
# https://docs.djangoproject.com/en/5.0/topics/files/

MEDIA_ROOT = os.environ.get("MEDIA_ROOT") or os.path.join(BASE_DIR, "media")

MEDIA_URL = "media/"


# Email
# https://docs.djangoproject.com/en/5.0/topics/email/

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
EMAIL_SUBJECT_PREFIX = "[Housetrained] "
EMAIL_TIMEOUT = 30
if os.environ.get("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ.get("EMAIL_HOST")
    EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER") or ""
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD") or ""
    EMAIL_PORT = os.environ.get("EMAIL_PORT") or 25
    EMAIL_USE_TLS = bool(strtobool(os.environ.get("EMAIL_USE_TLS") or "False"))
    EMAIL_USE_SSL = bool(strtobool(os.environ.get("EMAIL_USE_SSL") or "False"))
    EMAIL_SSL_KEYFILE = os.environ.get("EMAIL_SSL_KEYFILE") or None
    EMAIL_SSL_CERTFILE = os.environ.get("EMAIL_SSL_CERTFILE") or None
    DEFAULT_FROM_EMAIL = os.environ.get("EMAIL_FROM") or EMAIL_HOST_USER or ""

# Security

# https://docs.djangoproject.com/en/5.0/ref/settings/#secure-proxy-ssl-header
BEHIND_HTTPS_PROXY = bool(strtobool(os.environ.get("SECURE_PROXY_SSL_HEADER") or "False"))
if BEHIND_HTTPS_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# https://docs.djangoproject.com/en/5.0/topics/http/sessions/#settings
SESSION_COOKIE_HTTPONLY = True
# Housetrained: behind an HTTPS proxy, cookies are HTTPS-only unless set otherwise.
SESSION_COOKIE_SECURE = bool(
    strtobool(os.environ.get("SESSION_COOKIE_SECURE") or str(BEHIND_HTTPS_PROXY))
)

# https://docs.djangoproject.com/en/5.0/ref/csrf/#settings
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = bool(strtobool(os.environ.get("CSRF_COOKIE_SECURE") or str(BEHIND_HTTPS_PROXY)))
CSRF_FAILURE_VIEW = "babybuddy.views.csrf_failure"
CSRF_TRUSTED_ORIGINS = list(
    filter(None, os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(","))
)


# https://docs.djangoproject.com/en/5.0/topics/auth/passwords/
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {
            "min_length": 8,
        },
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# https://github.com/adamchainz/django-cors-headers
if os.environ.get("CORS_ALLOWED_ORIGINS"):
    CORS_ALLOWED_ORIGINS = [
        x.strip() for x in os.environ.get("CORS_ALLOWED_ORIGINS").split(",")
    ]

# Django Rest Framework
# https://www.django-rest-framework.org/

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "babybuddy.authentication.TokenAuthentication",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "PAGE_SIZE": 100,
}

# Axes configuration
# See https://django-axes.readthedocs.io/en/latest/4_configuration.html

AXES_COOLOFF_TIME = 1

AXES_FAILURE_LIMIT = 3

# `None` makes Axes use the default precedence order set by django-ipware.
# See: https://github.com/un33k/django-ipware#precedence-order
AXES_IPWARE_META_PRECEDENCE_ORDER = None

AXES_LOCKOUT_TEMPLATE = "error/lockout.html"

AXES_LOCKOUT_URL = "/login/lock"

# Session configuration
# Used by RollingSessionMiddleware to determine how often to reset the session.
# See https://docs.djangoproject.com/en/5.0/topics/http/sessions/

ROLLING_SESSION_REFRESH = 86400

# Set default auto field for models.
# See https://docs.djangoproject.com/en/5.0/releases/3.2/#customizing-type-of-auto-created-primary-keys

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"


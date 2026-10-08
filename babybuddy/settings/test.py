from .base import *

SECRET_KEY = "TESTS"

# Password hasher configuration
# See https://docs.djangoproject.com/en/5.0/ref/settings/#password-hashers
# See https://docs.djangoproject.com/en/5.0/topics/testing/overview/#password-hashing

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Email
# https://docs.djangoproject.com/en/5.0/topics/email/

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Axes configuration
# See https://django-axes.readthedocs.io/en/latest/4_configuration.html

AXES_ENABLED = False

# DBSettings configuration
# See https://github.com/zlorf/django-dbsettings#a-note-about-caching

DBSETTINGS_USE_CACHE = False

# We want to test the home assistant middleware

ENABLE_HOME_ASSISTANT_SUPPORT = True

# Baby Buddy's own tests exercise the baby features, so they run in baby mode; pet tests switch it off.

BABY_MODE = True

# Baby Buddy's tests sign in as admin/admin; real installs get a random first password.

DEFAULT_ADMIN_PASSWORD = "admin"

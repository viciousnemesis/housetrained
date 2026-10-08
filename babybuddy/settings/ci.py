from .base import *

SECRET_KEY = "CISECRETKEYIGUESS"


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.0/howto/static-files/

STORAGES["staticfiles"][
    "BACKEND"
] = "django.contrib.staticfiles.storage.StaticFilesStorage"

DEFAULT_ADMIN_PASSWORD = "admin"  # Baby Buddy's tests sign in as admin/admin

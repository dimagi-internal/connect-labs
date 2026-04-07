"""
Base settings to build other settings files upon.
"""
import os
import sys
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve(strict=True).parent.parent.parent

# GDAL Configuration for GeoDjango
# ------------------------------------------------------------------------------
# On Windows, GDAL must be installed separately (e.g., via OSGeo4W)
# Set GDAL_LIBRARY_PATH to point to the GDAL DLL
# This must be set BEFORE Django tries to import GDAL
GDAL_LIBRARY_PATH = None
GEOS_LIBRARY_PATH = None

if sys.platform == "win32":
    # Common OSGeo4W installation paths
    osgeo4w_paths = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\OSGeo4W\bin"),
        r"C:\OSGeo4W\bin",
        r"C:\OSGeo4W64\bin",
        r"C:\Program Files\GDAL\bin",
    ]
    for osgeo_path in osgeo4w_paths:
        if Path(osgeo_path).exists():
            # Add to PATH so dependent DLLs can be found
            os.environ["PATH"] = osgeo_path + os.pathsep + os.environ.get("PATH", "")

            # Find GDAL DLL
            gdal_dlls = list(Path(osgeo_path).glob("gdal*.dll"))
            main_gdal = next((d for d in gdal_dlls if d.stem.replace("gdal", "").isdigit()), None)
            if main_gdal:
                GDAL_LIBRARY_PATH = str(main_gdal)

            # Find GEOS DLL
            geos_dlls = list(Path(osgeo_path).glob("geos_c.dll"))
            if geos_dlls:
                GEOS_LIBRARY_PATH = str(geos_dlls[0])

            if GDAL_LIBRARY_PATH:
                break
# commcare_connect/
APPS_DIR = BASE_DIR / "commcare_connect"

env = environ.Env()

env.read_env(str(BASE_DIR / ".env"))

# GENERAL
# ------------------------------------------------------------------------------
DEBUG = env.bool("DJANGO_DEBUG", False)
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en-us"
SITE_ID = 1
USE_I18N = True
USE_TZ = True
LOCALE_PATHS = [str(BASE_DIR / "locale")]

# DATABASES
# ------------------------------------------------------------------------------
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres:///commcare_connect",
    ),
}
DATABASES["default"]["ENGINE"] = "django.contrib.gis.db.backends.postgis"

# DATABASES staging/production
# ------------------------------------------------------------------------------
if env("RDS_HOSTNAME", default=None):
    DATABASES = {
        "default": {
            "ENGINE": "django.contrib.gis.db.backends.postgis",
            "NAME": env("RDS_DB_NAME"),
            "USER": env("RDS_USERNAME"),
            "PASSWORD": env("RDS_PASSWORD"),
            "HOST": env("RDS_HOSTNAME"),
            "PORT": env("RDS_PORT"),
        }
    }

SECONDARY_DB_ALIAS = None
if env("SECONDARY_DATABASE_URL", default=None):
    SECONDARY_DB_ALIAS = "secondary"
    DATABASES[SECONDARY_DB_ALIAS] = env.db("SECONDARY_DATABASE_URL")
    DATABASES[SECONDARY_DB_ALIAS]["ENGINE"] = "django.contrib.gis.db.backends.postgis"
    DATABASE_ROUTERS = ["commcare_connect.multidb.db_router.ConnectDatabaseRouter"]

DATABASES["default"]["ATOMIC_REQUESTS"] = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# URLS
# ------------------------------------------------------------------------------
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# APPS
# ------------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.sites",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "django.contrib.humanize",  # Handy template tags
    "django.contrib.admin",
    "django.contrib.gis",
    "django.forms",
]
THIRD_PARTY_APPS = [
    "crispy_forms",
    "crispy_tailwind",
    "django_celery_beat",
    "rest_framework",
    "rest_framework.authtoken",
    "corsheaders",
    "drf_spectacular",
    "oauth2_provider",
    "django_tables2",
    "pghistory",
    "pgtrigger",  # added for pghistory
]

LOCAL_APPS = [
    "commcare_connect.ai",
    "commcare_connect.tasks",
    "commcare_connect.audit",
    "commcare_connect.workflow",
    "commcare_connect.coverage",
    "commcare_connect.commcarehq",  # stub: HQServer model for FK references
    "commcare_connect.labs",
    "commcare_connect.labs.admin_boundaries",
    "commcare_connect.multidb",
    "commcare_connect.opportunity",
    "commcare_connect.organization",
    "commcare_connect.program",
    "commcare_connect.solicitations",
    "commcare_connect.users",
    "commcare_connect.web",
]
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# MIGRATIONS
# ------------------------------------------------------------------------------
MIGRATION_MODULES = {"sites": "commcare_connect.contrib.sites.migrations"}

# AUTHENTICATION
# ------------------------------------------------------------------------------
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
]
AUTH_USER_MODEL = "users.User"
LOGIN_REDIRECT_URL = "/labs/overview/"
LOGIN_URL = "/labs/login/"

# PASSWORDS
# ------------------------------------------------------------------------------
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# MIDDLEWARE
# ------------------------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "commcare_connect.utils.middleware.CustomErrorHandlingMiddleware",
    "commcare_connect.utils.middleware.CurrentVersionMiddleware",
    "commcare_connect.utils.middleware.CustomPGHistoryMiddleware",
]

# STATIC
# ------------------------------------------------------------------------------
STATIC_ROOT = str(BASE_DIR / "staticfiles")
STATIC_URL = "/static/"
STATICFILES_DIRS = [str(APPS_DIR / "static")]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
WHITENOISE_MANIFEST_STRICT = False  # don't 500 on missing staticfiles

# MEDIA
# ------------------------------------------------------------------------------
MEDIA_ROOT = str(APPS_DIR / "media")
MEDIA_URL = "/media/"

# TEMPLATES
# ------------------------------------------------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [str(APPS_DIR / "templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.template.context_processors.i18n",
                "django.template.context_processors.media",
                "django.template.context_processors.static",
                "django.template.context_processors.tz",
                "django.contrib.messages.context_processors.messages",
                "commcare_connect.labs.context.labs_org_data_context",
                "commcare_connect.web.context_processors.page_settings",
                "commcare_connect.web.context_processors.gtm_context",
                "commcare_connect.web.context_processors.chat_widget_context",
            ],
        },
    }
]

FORM_RENDERER = "django.forms.renderers.TemplatesSetting"
CRISPY_TEMPLATE_PACK = "tailwind"
CRISPY_ALLOWED_TEMPLATE_PACKS = "tailwind"

# FIXTURES
# ------------------------------------------------------------------------------
FIXTURE_DIRS = (str(APPS_DIR / "fixtures"),)

# SECURITY
# ------------------------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
CSRF_USE_SESSIONS = True
X_FRAME_OPTIONS = "DENY"

# EMAIL
# ------------------------------------------------------------------------------
EMAIL_BACKEND = env(
    "DJANGO_EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)
EMAIL_TIMEOUT = 5
DEFAULT_FROM_EMAIL = env(
    "DJANGO_DEFAULT_FROM_EMAIL",
    default="CommCare Connect <noreply@commcare-connect.org>",
)
SERVER_EMAIL = env("DJANGO_SERVER_EMAIL", default=DEFAULT_FROM_EMAIL)
EMAIL_SUBJECT_PREFIX = env(
    "DJANGO_EMAIL_SUBJECT_PREFIX",
    default="[CommCare Connect]",
)

# ADMIN
# ------------------------------------------------------------------------------
ADMIN_URL = env("DJANGO_ADMIN_URL", default="admin/")
ADMINS = [("""Dimagi""", "dimagi@commcare-connect.org")]
MANAGERS = ADMINS

# LOGGING
# ------------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(levelname)s %(asctime)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "null": {
            "class": "logging.NullHandler",
        },
    },
    "root": {"level": "INFO", "handlers": ["console"]},
    "django.template": {
        "handlers": ["console"],
        "level": env("DJANGO_TEMPLATE_LOG_LEVEL", default="WARN"),
        "propagate": False,
    },
    "loggers": {
        "django.security.DisallowedHost": {
            "handlers": ["null"],
            "propagate": False,
        },
        "commcare_connect.ai": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        # Suppress GEOS geometry warnings (self-intersection notices from admin boundary data)
        "django.contrib.gis": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}

# Celery
# ------------------------------------------------------------------------------
if USE_TZ:
    CELERY_TIMEZONE = TIME_ZONE
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_RESULT_EXTENDED = True
CELERY_RESULT_BACKEND_ALWAYS_RETRY = True
CELERY_RESULT_BACKEND_MAX_RETRIES = 10
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
# CELERY_TASK_TIME_LIMIT = 5 * 60
# CELERY_TASK_SOFT_TIME_LIMIT = 60
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_WORKER_SEND_TASK_EVENTS = True
CELERY_TASK_SEND_SENT_EVENT = True
CELERY_TASK_TRACK_STARTED = True

# django-rest-framework
# -------------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "oauth2_provider.contrib.rest_framework.OAuth2Authentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.AcceptHeaderVersioning",
    "DEFAULT_VERSION": "1.0",
    "ALLOWED_VERSIONS": ["1.0", "2.0"],
    "EXCEPTION_HANDLER": "commcare_connect.utils.exceptions.drf_permission_denied_handler",
}

CORS_URLS_REGEX = r"^/api/.*$"

SPECTACULAR_SETTINGS = {
    "TITLE": "CommCare Connect API",
    "DESCRIPTION": "Documentation of API endpoints of CommCare Connect",
    "VERSION": "1.0.0",
    "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAdminUser"],
}

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://localhost:6379/0"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "IGNORE_EXCEPTIONS": True,
        },
    }
}

DJANGO_TABLES2_TEMPLATE = "base_table.html"
DJANGO_TABLES2_TABLE_ATTRS = {
    "class": "table table-bordered mb-0",
    "thead": {
        "class": "",
    },
    "tfoot": {
        "class": "table-light fw-bold",
    },
}

# ------------------------------------------------------------------------------
# CommCare Connect Settings...
# ------------------------------------------------------------------------------
# HQ integration settings
COMMCARE_HQ_URL = env("COMMCARE_HQ_URL", default="https://staging.commcarehq.org")
COMMCARE_API_KEY = env("COMMCARE_API_KEY", default="")
COMMCARE_USERNAME = env("COMMCARE_USERNAME", default="")

# ConnectID integration settings
CONNECTID_URL = env("CONNECTID_URL", default="http://localhost:8080")

CONNECTID_CLIENT_ID = env("cid_client_id", default="")
CONNECTID_CLIENT_SECRET = env("cid_client_secret", default="")

# OAuth Settings
CONNECTID_CREDENTIALS_CLIENT_ID = env("CONNECTID_CREDENTIALS_CLIENT_ID", default="")
CONNECTID_CREDENTIALS_CLIENT_SECRET = env("CONNECTID_CREDENTIALS_CLIENT_SECRET", default="")
OAUTH2_PROVIDER = {
    "ACCESS_TOKEN_EXPIRE_SECONDS": 1209600,  # seconds in two weeks
    "RESOURCE_SERVER_INTROSPECTION_URL": f"{CONNECTID_URL}/o/introspect/",
    "RESOURCE_SERVER_INTROSPECTION_CREDENTIALS": (
        CONNECTID_CLIENT_ID,
        CONNECTID_CLIENT_SECRET,
    ),
    "SCOPES": {
        "read": "Read scope",
        "write": "Write scope",
        "export": "Allow exporting data to other platforms using export API's.",
    },
}
OAUTH2_PROVIDER_APPLICATION_MODEL = "oauth2_provider.Application"

# Connect Production OAuth (for audit data extraction)
CONNECT_PRODUCTION_URL = env("CONNECT_PRODUCTION_URL", default="https://connect.dimagi.com")
CONNECT_OAUTH_CLIENT_ID = env("CONNECT_OAUTH_CLIENT_ID", default="")
CONNECT_OAUTH_CLIENT_SECRET = env("CONNECT_OAUTH_CLIENT_SECRET", default="")

# Labs admin allowlist — LOCAL DEV ONLY fallback for Connect test accounts that
# have no email address configured (e.g. username='matt', email='').
# In production, Dimagi staff are identified automatically by their @dimagi.com
# email returned from Connect OAuth introspection — no config needed there.
# Only set this in your local .env if your dev Connect account has no email.
# Example: LABS_ADMIN_USERNAMES=matt
LABS_ADMIN_USERNAMES = env.list("LABS_ADMIN_USERNAMES", default=[])

# S3 bucket for exporting audit/workflow records as CSV backups.
# When None (default), all export calls are silently skipped.
LABS_EXPORTS_BUCKET = env("LABS_EXPORTS_BUCKET", default=None)

# AWS credentials for S3 export. On ECS the task IAM role is used instead
# (leave these unset in production). Set in .env for local testing.
LABS_AWS_ACCESS_KEY_ID = env("LABS_AWS_ACCESS_KEY_ID", default=None)
LABS_AWS_SECRET_ACCESS_KEY = env("LABS_AWS_SECRET_ACCESS_KEY", default=None)
LABS_AWS_SESSION_TOKEN = env("LABS_AWS_SESSION_TOKEN", default=None)
LABS_AWS_DEFAULT_REGION = env("LABS_AWS_DEFAULT_REGION", default="us-east-1")

# Open Chat Studio OAuth (for OCS API access)
OCS_URL = env("OCS_URL", default="https://www.openchatstudio.com")
OCS_OAUTH_CLIENT_ID = env("OCS_OAUTH_CLIENT_ID", default="")
OCS_OAUTH_CLIENT_SECRET = env("OCS_OAUTH_CLIENT_SECRET", default="")


# Twilio settings
TWILIO_ACCOUNT_SID = env("TWILIO_SID", default=None)
TWILIO_AUTH_TOKEN = env("TWILIO_TOKEN", default=None)
TWILIO_MESSAGING_SERVICE = env("TWILIO_MESSAGING_SERVICE", default=None)
MAPBOX_TOKEN = env("MAPBOX_TOKEN", default=None)

OPEN_EXCHANGE_RATES_API_ID = env("OPEN_EXCHANGE_RATES_API_ID", default=None)

# Waffle Settings
WAFFLE_FLAG_MODEL = "flags.Flag"
WAFFLE_CREATE_MISSING_FLAGS = True

WAFFLE_CREATE_MISSING_SWITCHES = True

GTM_ID = env("GTM_ID", default="")
GA_MEASUREMENT_ID = env("GA_MEASUREMENT_ID", default="")
GA_API_SECRET = env("GA_API_SECRET", default="")

# OCS (Open Chat Studio) API Configuration
# ------------------------------------------------------------------------------
OCS_BASE_URL = env("OCS_BASE_URL", default="")
OCS_API_KEY = env("OCS_API_KEY", default="")

# Scale Image Validation API Configuration (KMC weight verification)
# ------------------------------------------------------------------------------
SCALE_VALIDATION_API_URL = env(
    "SCALE_VALIDATION_API_URL",
    default="https://image-pipeline-scale-gw-4pc8jsfa.uc.gateway.dev",
)
SCALE_VALIDATION_API_KEY = env("SCALE_VALIDATION_API_KEY", default="")

# Chatbot Widget Settings
CHATBOT_ID = env("CHATBOT_ID", default="")
CHATBOT_EMBED_KEY = env("CHATBOT_EMBED_KEY", default="")

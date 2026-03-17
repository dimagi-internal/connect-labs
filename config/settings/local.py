from .base import *  # noqa
from .base import env

# GENERAL
# ------------------------------------------------------------------------------
DEBUG = True
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="5xpjGRDKKXRiO2u1AiwUT6fbl5iM89JkQ9lnMCJEhvW1JQvXdNroF2OMSe60KEcR",
)
ALLOWED_HOSTS = ["localhost", "0.0.0.0", "127.0.0.1"] + env.list("DJANGO_ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = ["https://*.127.0.0.1", "https://*.loca.lt"] + env.list("CSRF_TRUSTED_ORIGINS", default=[])

# django-debug-toolbar
# ------------------------------------------------------------------------------
# Disabled — causes cProfile race conditions on concurrent requests
# INSTALLED_APPS += ["debug_toolbar"]  # noqa: F405
# MIDDLEWARE += ["debug_toolbar.middleware.DebugToolbarMiddleware"]  # noqa: F405
# DEBUG_TOOLBAR_CONFIG = {
#     "DISABLE_PANELS": ["debug_toolbar.panels.redirects.RedirectsPanel"],
#     "SHOW_TEMPLATE_CONTEXT": True,
# }
INTERNAL_IPS = ["127.0.0.1", "10.0.2.2"]

# Celery
# ------------------------------------------------------------------------------
# Set CELERY_TASK_ALWAYS_EAGER=False in .env to use real async with a Celery worker
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=True)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_STORE_EAGER_RESULT = True  # Store results even in eager mode so we can retrieve them

# CommCareConnect
# ------------------------------------------------------------------------------

# Labs Mode Configuration
# ------------------------------------------------------------------------------
IS_LABS_ENVIRONMENT = True

# OAuth configuration
LABS_OAUTH_SCOPES = ["export"]

# CLI OAuth Configuration (for get_cli_token management command)
# Register a "Public" OAuth application at your production instance with:
# - Redirect URI: http://localhost:8765/callback
# - Authorization grant type: Authorization code
# - Client type: Public
CLI_OAUTH_CLIENT_ID = env("CLI_OAUTH_CLIENT_ID", default="")
CLI_OAUTH_CLIENT_SECRET = env("CLI_OAUTH_CLIENT_SECRET", default="")  # Not needed for public clients

# CommCare HQ OAuth Configuration (for API access)
COMMCARE_HQ_URL = env("COMMCARE_HQ_URL", default="https://www.commcarehq.org")
COMMCARE_OAUTH_CLIENT_ID = env("COMMCARE_OAUTH_CLIENT_ID", default="")
COMMCARE_OAUTH_CLIENT_SECRET = env("COMMCARE_OAUTH_CLIENT_SECRET", default="")
COMMCARE_OAUTH_CLI_CLIENT_ID = env("COMMCARE_OAUTH_CLI_CLIENT_ID", default="")

# Open Chat Studio OAuth Configuration (for OCS API access)
OCS_URL = env("OCS_URL", default="https://www.openchatstudio.com")
OCS_OAUTH_CLIENT_ID = env("OCS_OAUTH_CLIENT_ID", default="")
OCS_OAUTH_CLIENT_SECRET = env("OCS_OAUTH_CLIENT_SECRET", default="")

# Add labs app to installed apps
INSTALLED_APPS = INSTALLED_APPS + [  # noqa: F405
    "commcare_connect.labs",
    "commcare_connect.custom_analysis.chc_nutrition",
]

# Add labs context middleware after auth
MIDDLEWARE = list(MIDDLEWARE)  # noqa: F405
_auth_idx = MIDDLEWARE.index("django.contrib.auth.middleware.AuthenticationMiddleware")
MIDDLEWARE.insert(_auth_idx + 1, "commcare_connect.labs.context.LabsContextMiddleware")

# Pipeline cache settings for local development
# 24-hour cache TTL for dev (production default: 1 hour)
PIPELINE_CACHE_TTL_HOURS = 24
# Accept cache if it has >= 70% of expected visits (handles production streaming truncation)
PIPELINE_CACHE_TOLERANCE_PCT = 70

# Labs apps configuration
# No longer need hardcoded opportunity_id - API now supports organization_id/program_id

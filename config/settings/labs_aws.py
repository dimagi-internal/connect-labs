"""
Labs AWS environment settings (labs.connect.dimagi.com).

This settings file is ONLY for the AWS ECS Fargate deployment.
For local development (even of labs features), use config.settings.local.

Session-based OAuth authentication with no user database storage.
"""

import logging

from .base import *  # noqa
from .base import INSTALLED_APPS, MIDDLEWARE, STORAGES, env

# GENERAL
# ------------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", default="")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)  # noqa: F405

# SECURITY
# ------------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=False)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True)
SECURE_HSTS_PRELOAD = env.bool("DJANGO_SECURE_HSTS_PRELOAD", default=True)
SECURE_CONTENT_TYPE_NOSNIFF = env.bool("DJANGO_SECURE_CONTENT_TYPE_NOSNIFF", default=True)

# STORAGES (S3)
# ------------------------------------------------------------------------------
INSTALLED_APPS = list(INSTALLED_APPS)
INSTALLED_APPS += ["storages"]
AWS_STORAGE_BUCKET_NAME = env("DJANGO_AWS_STORAGE_BUCKET_NAME", default="commcare-connect-media")
AWS_QUERYSTRING_AUTH = False
_AWS_EXPIRY = 60 * 60 * 24 * 7
AWS_S3_OBJECT_PARAMETERS = {
    "CacheControl": f"max-age={_AWS_EXPIRY}, s-maxage={_AWS_EXPIRY}, must-revalidate",
}
AWS_S3_MAX_MEMORY_SIZE = env.int("DJANGO_AWS_S3_MAX_MEMORY_SIZE", default=100_000_000)
AWS_S3_REGION_NAME = env("AWS_DEFAULT_REGION", default=None)
AWS_S3_CUSTOM_DOMAIN = env("DJANGO_AWS_S3_CUSTOM_DOMAIN", default=None)
aws_s3_domain = AWS_S3_CUSTOM_DOMAIN or f"{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com"
MEDIA_URL = f"https://{aws_s3_domain}/media/"
STORAGES["default"]["BACKEND"] = "commcare_connect.utils.storages.MediaRootS3Boto3Storage"

# EMAIL (SES)
# ------------------------------------------------------------------------------
INSTALLED_APPS += ["anymail"]
ANYMAIL = {}

# SENTRY
# ------------------------------------------------------------------------------
SENTRY_DSN = env("SENTRY_DSN", default="")
APP_RELEASE = env("APP_RELEASE", default=None)
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger
    from sentry_sdk.integrations.redis import RedisIntegration

    ignore_logger("django.security.DisallowedHost")
    sentry_logging = LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[sentry_logging, DjangoIntegration(), CeleryIntegration(), RedisIntegration()],
        environment=env("DEPLOY_ENVIRONMENT", default="labs"),
        release=APP_RELEASE,
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.0),
    )

# CSRF
# ------------------------------------------------------------------------------
CSRF_TRUSTED_ORIGINS = ["https://*.127.0.0.1"] + env.list("CSRF_TRUSTED_ORIGINS", default=[])
# LABS ENVIRONMENT
# ------------------------------------------------------------------------------
IS_LABS_ENVIRONMENT = True
DEPLOY_ENVIRONMENT = "labs"

# OAuth configuration
LABS_OAUTH_SCOPES = ["export"]
LOGIN_URL = "/labs/login/"

# Add labs app and custom_analysis
INSTALLED_APPS.append("commcare_connect.labs")
INSTALLED_APPS.append("commcare_connect.custom_analysis.chc_nutrition")

# Add labs context middleware after auth
MIDDLEWARE = list(MIDDLEWARE)
_auth_idx = MIDDLEWARE.index("django.contrib.auth.middleware.AuthenticationMiddleware")
MIDDLEWARE.insert(_auth_idx + 1, "commcare_connect.labs.context.LabsContextMiddleware")

# CommCare OAuth configuration
COMMCARE_HQ_URL = env("COMMCARE_HQ_URL", default="https://www.commcarehq.org")
COMMCARE_OAUTH_CLIENT_ID = env("COMMCARE_OAUTH_CLIENT_ID", default="")
COMMCARE_OAUTH_CLIENT_SECRET = env("COMMCARE_OAUTH_CLIENT_SECRET", default="")
COMMCARE_OAUTH_CLI_CLIENT_ID = env("COMMCARE_OAUTH_CLI_CLIENT_ID", default="")

# Open Chat Studio OAuth configuration
OCS_URL = env("OCS_URL", default="https://www.openchatstudio.com")
OCS_OAUTH_CLIENT_ID = env("OCS_OAUTH_CLIENT_ID", default="")
OCS_OAUTH_CLIENT_SECRET = env("OCS_OAUTH_CLIENT_SECRET", default="")

# Allow large POST bodies for snapshot save
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024  # 50 MB

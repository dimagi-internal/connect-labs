"""
With these settings, tests run faster.
"""

import platform

from .base import *  # noqa
from .base import env

if platform.system() == "Darwin":
    GDAL_LIBRARY_PATH = env("GDAL_LIBRARY_PATH")
    GEOS_LIBRARY_PATH = env("GEOS_LIBRARY_PATH")

# GENERAL
# ------------------------------------------------------------------------------
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="G11wtQ0L0YWp13SJhMLlKlFrCsTuNm6s5Q6Q2o0U2E75hf0kRoV5hiK86yye0Tar",
)
TEST_RUNNER = "django.test.runner.DiscoverRunner"

# PASSWORDS
# ------------------------------------------------------------------------------
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# DEBUGGING FOR TEMPLATES
# ------------------------------------------------------------------------------
TEMPLATES[0]["OPTIONS"]["debug"] = True  # type: ignore # noqa: F405

STORAGES["staticfiles"]["BACKEND"] = "django.contrib.staticfiles.storage.StaticFilesStorage"  # noqa: F405

# Register the labs app so its models (RawVisitCache, ComputedVisitCache, etc.)
# get migrations applied to the test database. Mirrors the registration in
# local.py and labs_aws.py — base.py does not include it because those two
# files already opt in and double-registration raises ImproperlyConfigured.
INSTALLED_APPS = INSTALLED_APPS + ["commcare_connect.labs", "commcare_connect.campaign"]  # noqa: F405

# Install the campaign OAuth-session middleware under test settings so its
# behavior is exercised here (local.py/labs_aws.py add it for runtime; base/test
# do not include the session middlewares).
MIDDLEWARE = list(MIDDLEWARE)  # noqa: F405
_auth_idx = MIDDLEWARE.index("django.contrib.auth.middleware.AuthenticationMiddleware")
MIDDLEWARE.insert(_auth_idx + 1, "commcare_connect.campaign.middleware.CampaignOAuthSessionMiddleware")

# CommCareConnect
# ------------------------------------------------------------------------------

"""Canonical labs test settings shared across test modules.

Import LABS_MIDDLEWARE and LABS_SETTINGS from here instead of redefining
them in each test file.
"""

LABS_MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "commcare_connect.labs.context.LabsContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "commcare_connect.utils.middleware.CustomErrorHandlingMiddleware",
    "commcare_connect.utils.middleware.CurrentVersionMiddleware",
    "commcare_connect.utils.middleware.CustomPGHistoryMiddleware",
]

LABS_SETTINGS = dict(
    IS_LABS_ENVIRONMENT=True,
    MIDDLEWARE=LABS_MIDDLEWARE,
    LOGIN_URL="/labs/login/",
    CONNECT_PRODUCTION_URL="https://connect.example.com",
)

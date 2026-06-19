from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views import defaults as default_views
from django.views.generic import RedirectView, TemplateView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.authtoken.views import obtain_auth_token

from commcare_connect.mcp.admin_views import create_token_browser

from . import views

urlpatterns = [
    # MCP token creation — registered here at the path Django sees after Starlette's
    # Mount("/mcp/admin", ...) strips the prefix, i.e. /create-token/ → this view.
    path("create-token/", create_token_browser, name="mcp_admin_create_token"),
    path("", include("commcare_connect.prelogin.urls")),
    # The ACE Web SPA is served by a separate nginx container; the ALB only
    # routes `/ace/*` to it. A bare `/ace` would fall through here and 404 —
    # catch it and redirect to the slash variant so typed URLs work.
    path("ace", RedirectView.as_view(url="/ace/", permanent=True), name="ace_slash_redirect"),
    path("about/", TemplateView.as_view(template_name="pages/about.html"), name="about"),
    path("health/", views.health_check, name="health_check"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path(".well-known/assetlinks.json", views.assetlinks_json, name="assetlinks_json"),
    path("o/", include("oauth2_provider.urls", namespace="oauth2_provider")),
    # Labs apps
    path("solicitations/", include("commcare_connect.solicitations.urls", namespace="solicitations")),
    path("funder/", include("commcare_connect.funder_dashboard.urls", namespace="funder_dashboard")),
    path("tasks/", include("commcare_connect.tasks.urls", namespace="tasks")),
    path("audit/", include("commcare_connect.audit.urls", namespace="audit")),
    path("coverage/", include("commcare_connect.coverage.urls", namespace="coverage")),
    path("microplans/", include("commcare_connect.microplans.urls", namespace="microplans")),
    # Back-compat: the app was first shipped at /rooftop-surveys/ (a deployed opp may
    # reference it). Redirect the old prefix to the renamed /microplans/.
    path(
        "rooftop-surveys/<path:subpath>",
        RedirectView.as_view(url="/microplans/%(subpath)s", query_string=True, permanent=False),
        name="rooftop_surveys_legacy_redirect",
    ),
    path("mcp/", include("commcare_connect.mcp.urls", namespace="mcp")),
    path("labs/explorer/", include("commcare_connect.labs.explorer.urls", namespace="explorer")),
    path("labs/", include("commcare_connect.labs.urls", namespace="labs")),
    path(
        "custom_analysis/chc_nutrition/",
        include("commcare_connect.custom_analysis.chc_nutrition.urls", namespace="chc_nutrition"),
    ),
    path(
        "custom_analysis/kmc/",
        include("commcare_connect.custom_analysis.kmc.urls", namespace="kmc"),
    ),
    path(
        "custom_analysis/mbw_monitoring/",
        include("commcare_connect.workflow.templates.mbw_monitoring.urls", namespace="mbw"),
    ),
    path(
        "custom_analysis/rutf/",
        include("commcare_connect.custom_analysis.rutf.urls", namespace="rutf"),
    ),
    path(
        "custom_analysis/audit_of_audits/",
        include("commcare_connect.custom_analysis.audit_of_audits.urls", namespace="audit_of_audits"),
    ),
    path(
        "custom_analysis/exports/",
        include("commcare_connect.custom_analysis.exports.urls", namespace="exports"),
    ),
    path("ai/", include("commcare_connect.ai.urls", namespace="ai")),
    path("campaign/", include("commcare_connect.campaign.urls", namespace="campaign")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Django Admin (conditionally include if admin app is installed)
if "django.contrib.admin" in settings.INSTALLED_APPS:
    urlpatterns.insert(0, path(settings.ADMIN_URL, admin.site.urls))

# API URLS
urlpatterns += [
    # Synthetic-opportunity export API — must precede the "api/" router include
    # below, since include() does not backtrack to later patterns on a miss.
    path("api/export/", include("commcare_connect.labs.export_api.urls")),
    # API base url
    path("api/", include("config.api_router")),
    # DRF auth token
    path("auth-token/", obtain_auth_token),
    path("api/schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="api-schema"),
        name="api-docs",
    ),
]

if settings.DEBUG:
    # This allows the error pages to be debugged during development, just visit
    # these url in browser to see how these error pages look like.
    urlpatterns += [
        path(
            "400/",
            default_views.bad_request,
            kwargs={"exception": Exception("Bad Request!")},
        ),
        path(
            "403/",
            default_views.permission_denied,
            kwargs={"exception": Exception("Permission Denied")},
        ),
        path(
            "404/",
            default_views.page_not_found,
            kwargs={"exception": Exception("Page not Found")},
        ),
        path("500/", default_views.server_error),
    ]
    if "debug_toolbar" in settings.INSTALLED_APPS:
        import debug_toolbar

        urlpatterns = [path("__debug__/", include(debug_toolbar.urls))] + urlpatterns

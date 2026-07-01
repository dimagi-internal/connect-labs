from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views import defaults as default_views
from django.views.generic import RedirectView, TemplateView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.authtoken.views import obtain_auth_token

from connect_labs.mcp.admin_views import create_token_browser

from . import views

urlpatterns = [
    # MCP token creation — registered here at the path Django sees after Starlette's
    # Mount("/mcp/admin", ...) strips the prefix, i.e. /create-token/ → this view.
    path("create-token/", create_token_browser, name="mcp_admin_create_token"),
    path("", include("connect_labs.prelogin.urls")),
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
    path("solicitations/", include("connect_labs.solicitations.urls", namespace="solicitations")),
    path("funder/", include("connect_labs.funder_dashboard.urls", namespace="funder_dashboard")),
    path("tasks/", include("connect_labs.tasks.urls", namespace="tasks")),
    path("audit/", include("connect_labs.audit.urls", namespace="audit")),
    path("coverage/", include("connect_labs.coverage.urls", namespace="coverage")),
    path("microplans/", include("connect_labs.microplans.urls", namespace="microplans")),
    # Back-compat: the app was first shipped at /rooftop-surveys/ (a deployed opp may
    # reference it). Redirect the old prefix to the renamed /microplans/.
    path(
        "rooftop-surveys/<path:subpath>",
        RedirectView.as_view(url="/microplans/%(subpath)s", query_string=True, permanent=False),
        name="rooftop_surveys_legacy_redirect",
    ),
    path("mcp/", include("connect_labs.mcp.urls", namespace="mcp")),
    path("labs/explorer/", include("connect_labs.labs.explorer.urls", namespace="explorer")),
    path("labs/", include("connect_labs.labs.urls", namespace="labs")),
    path(
        "custom_analysis/chc_nutrition/",
        include("connect_labs.custom_analysis.chc_nutrition.urls", namespace="chc_nutrition"),
    ),
    path(
        "custom_analysis/kmc/",
        include("connect_labs.custom_analysis.kmc.urls", namespace="kmc"),
    ),
    path(
        "custom_analysis/mbw_monitoring/",
        include("connect_labs.workflow.templates.mbw_monitoring.urls", namespace="mbw"),
    ),
    path(
        "custom_analysis/rutf/",
        include("connect_labs.custom_analysis.rutf.urls", namespace="rutf"),
    ),
    path(
        "custom_analysis/audit_of_audits/",
        include("connect_labs.custom_analysis.audit_of_audits.urls", namespace="audit_of_audits"),
    ),
    path(
        "custom_analysis/exports/",
        include("connect_labs.custom_analysis.exports.urls", namespace="exports"),
    ),
    path("ai/", include("connect_labs.ai.urls", namespace="ai")),
    path("campaign/", include("connect_labs.campaign.urls", namespace="campaign")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Django Admin (conditionally include if admin app is installed)
if "django.contrib.admin" in settings.INSTALLED_APPS:
    urlpatterns.insert(0, path(settings.ADMIN_URL, admin.site.urls))

# API URLS
urlpatterns += [
    # Synthetic-opportunity export API — must precede the "api/" router include
    # below, since include() does not backtrack to later patterns on a miss.
    path("api/export/", include("connect_labs.labs.export_api.urls")),
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

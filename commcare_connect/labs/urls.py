from django.urls import include, path

from commcare_connect.labs import views, views_test_auth
from commcare_connect.labs.analysis import views as analysis_views
from commcare_connect.labs.integrations.commcare import oauth_views as commcare_oauth_views
from commcare_connect.labs.integrations.connect import oauth_views as connect_oauth_views
from commcare_connect.labs.integrations.ocs import oauth_views as ocs_oauth_views

app_name = "labs"

urlpatterns = [
    # Context management
    path("clear-context/", views.clear_context, name="clear_context"),
    path("refresh-org-data/", views.refresh_org_data, name="refresh_org_data"),
    # Connect OAuth (for labs authentication)
    path("login/", connect_oauth_views.labs_login_page, name="login"),
    path("initiate/", connect_oauth_views.labs_oauth_login, name="oauth_initiate"),
    path("callback/", connect_oauth_views.labs_oauth_callback, name="oauth_callback"),
    path("logout/", connect_oauth_views.labs_logout, name="logout"),
    path("dashboard/", connect_oauth_views.labs_dashboard, name="dashboard"),
    # E2E test auth (DEBUG only)
    path("test-auth/", views_test_auth.test_auth_view, name="test_auth"),
    # Labs Overview
    path("overview/", views.LabsOverviewView.as_view(), name="overview"),
    # Status page
    path("status/", views.StatusView.as_view(), name="status"),
    # Scout data agent
    path("scout/", views.ScoutEmbedView.as_view(), name="scout_embed"),
    # CommCare OAuth (for API access)
    path("commcare/initiate/", commcare_oauth_views.labs_commcare_initiate, name="commcare_initiate"),
    path("commcare/callback/", commcare_oauth_views.labs_commcare_callback, name="commcare_callback"),
    path("commcare/logout/", commcare_oauth_views.labs_commcare_logout, name="commcare_logout"),
    # Open Chat Studio OAuth (for OCS API access)
    path("ocs/initiate/", ocs_oauth_views.labs_ocs_initiate, name="ocs_initiate"),
    path("ocs/callback/", ocs_oauth_views.labs_ocs_callback, name="ocs_callback"),
    path("ocs/logout/", ocs_oauth_views.labs_ocs_logout, name="ocs_logout"),
    # Analysis API
    path("api/analysis/flw/", analysis_views.FLWAnalysisAPIView.as_view(), name="api_flw_analysis"),
    # Workflow (includes pipeline functionality)
    path("workflow/", include("commcare_connect.workflow.urls", namespace="workflow")),
    # Dashboard Prototypes
    path("", include("commcare_connect.labs.dashboards.urls")),
]

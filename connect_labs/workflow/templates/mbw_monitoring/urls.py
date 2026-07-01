from django.urls import path

from connect_labs.workflow.templates.mbw_monitoring import views
from connect_labs.workflow.views import OpportunityFLWListAPIView  # canonical home: workflow app

app_name = "mbw"

urlpatterns = [
    # Main dashboard (Overview tab is default)
    path("", views.MBWMonitoringDashboardView.as_view(), name="dashboard"),
    # Tab aliases (render same dashboard with tab param)
    path("gps/", views.MBWMonitoringDashboardView.as_view(), {"default_tab": "gps"}, name="gps"),
    path("followup/", views.MBWMonitoringDashboardView.as_view(), {"default_tab": "followup"}, name="followup"),
    # SSE streaming endpoint for data loading
    path("stream/", views.MBWMonitoringStreamView.as_view(), name="stream"),
    # JSON API endpoints for drill-down
    path("api/gps/<str:username>/", views.MBWGPSDetailView.as_view(), name="gps_detail"),
    # Action endpoints
    path("api/suspend-user/", views.MBWSuspendUserView.as_view(), name="suspend_user"),
    # Monitoring session endpoints
    path("api/session/save-flw-result/", views.MBWSaveFlwResultView.as_view(), name="save_flw_result"),
    path("api/session/complete/", views.MBWCompleteSessionView.as_view(), name="complete_session"),
    # Dashboard snapshot retrieval and saving
    path("api/snapshot/", views.MBWSnapshotView.as_view(), name="snapshot"),
    path("api/save-snapshot/", views.MBWSaveSnapshotView.as_view(), name="save_snapshot"),
    # OAuth token status check
    path("api/oauth-status/", views.MBWOAuthStatusView.as_view(), name="oauth_status"),
    # FLW API for workflow template render_code
    path("api/opportunity-flws/", OpportunityFLWListAPIView.as_view(), name="opportunity_flws"),
]

from django.urls import path

from connect_labs.audit_trail.views import AuditTrailDashboardView, SessionTimelineView

app_name = "audit_trail"

urlpatterns = [
    path("", AuditTrailDashboardView.as_view(), name="dashboard"),
    path("session/", SessionTimelineView.as_view(), name="session_timeline"),
]

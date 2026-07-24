from django.urls import path

from connect_labs.audit_trail.views import AuditTrailDashboardView

app_name = "audit_trail"

urlpatterns = [
    path("", AuditTrailDashboardView.as_view(), name="dashboard"),
]

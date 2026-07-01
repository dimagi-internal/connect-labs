"""URL configuration for the Audit of Audits admin report."""

from django.urls import path

from connect_labs.custom_analysis.audit_of_audits import views

app_name = "audit_of_audits"

urlpatterns = [
    path("", views.AuditOfAuditsView.as_view(), name="report"),
]

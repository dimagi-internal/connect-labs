"""
Labs Dashboard Prototype URLs
"""
from django.urls import path

from connect_labs.labs.dashboards import views

app_name = "dashboards"

urlpatterns = [
    path("dashboard-2/", views.dashboard_2, name="dashboard_2"),
    path("dashboard-3/", views.dashboard_3, name="dashboard_3"),
    path("dashboard-4/", views.dashboard_4, name="dashboard_4"),
    path("api/opportunity/<int:opp_id>/flws/", views.fetch_flws, name="fetch_flws"),
]

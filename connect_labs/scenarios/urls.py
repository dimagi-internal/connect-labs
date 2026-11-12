"""URLs for market sizing, mounted at /labs/scenarios/."""

from django.urls import path

from connect_labs.scenarios import views

app_name = "scenarios"

urlpatterns = [
    path("api/interventions/", views.InterventionsView.as_view(), name="interventions"),
    path("api/scenario/", views.ScenarioView.as_view(), name="scenario"),
]

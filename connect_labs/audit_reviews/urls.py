from django.urls import path

from connect_labs.audit_reviews import api_views

app_name = "audit_reviews"

urlpatterns = [
    path("api/<int:workflow_id>/world/", api_views.world_api, name="api_world"),
    path("api/<int:workflow_id>/decisions/", api_views.decisions_api, name="api_decisions"),
    path("api/<int:workflow_id>/events/", api_views.events_api, name="api_events"),
]

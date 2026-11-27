from django.urls import path

from connect_labs.audit_reviews import api_views

app_name = "audit_reviews"

urlpatterns = [
    path("api/<int:workflow_id>/world/", api_views.world_api, name="api_world"),
    path("api/<int:workflow_id>/decisions/", api_views.decisions_api, name="api_decisions"),
    path("api/<int:workflow_id>/events/", api_views.events_api, name="api_events"),
    path("api/<int:workflow_id>/link-preview/", api_views.link_preview_api, name="api_link_preview"),
    path("api/<int:workflow_id>/ocs-bots/", api_views.ocs_bots_api, name="api_ocs_bots"),
    path("api/<int:workflow_id>/files/", api_views.files_api, name="api_files"),
    path("api/<int:workflow_id>/files/<int:attachment_id>/", api_views.file_api, name="api_file"),
    path("api/<int:workflow_id>/settings/", api_views.settings_save_api, name="api_settings"),
    path("api/<int:workflow_id>/settings/preview/", api_views.settings_preview_api, name="api_settings_preview"),
    path("api/<int:workflow_id>/settings/undo/", api_views.settings_undo_api, name="api_settings_undo"),
]

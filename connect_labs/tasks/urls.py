from django.urls import path

from . import views

app_name = "tasks"

urlpatterns = [
    # Main views
    path("", views.TaskListView.as_view(), name="list"),
    path("create/", views.TaskCreationWizardView.as_view(), name="create"),  # Bulk creation wizard
    path("new/", views.TaskCreateEditView.as_view(), name="new"),  # Single task create mode
    path("<int:task_id>/edit/", views.TaskCreateEditView.as_view(), name="edit"),  # Edit mode
    path("bulk-create/", views.task_bulk_create, name="bulk_create"),
    path("api/single-create/", views.task_single_create, name="single_create"),  # Single task API
    path("api/<int:task_id>/", views.task_detail_api, name="task_detail"),  # Task detail JSON API
    path("api/<int:task_id>/update/", views.task_update, name="task_update"),  # Task update API
    path("api/<int:task_id>/comment/", views.task_add_comment, name="add_comment"),  # Add comment API
    # Removed TaskDetailView - using TaskCreateEditView as the main interface
    # path("<int:task_id>/", views.TaskDetailView.as_view(), name="detail"),
    # AI Assistant API
    path("<int:task_id>/ai/initiate/", views.task_initiate_ai, name="ai_initiate"),
    path("<int:task_id>/ai/sessions/", views.task_ai_sessions, name="ai_sessions"),
    path("<int:task_id>/ai/add-session/", views.task_add_ai_session, name="add_ai_session"),
    path("<int:task_id>/ai/transcript/", views.task_ai_transcript, name="ai_transcript"),
    path("<int:task_id>/ai/save-transcript/", views.task_ai_save_transcript, name="ai_save_transcript"),
    # Opportunity API (used by creation wizard)
    path("opportunities/search/", views.OpportunitySearchAPIView.as_view(), name="opportunity_search"),
    path(
        "opportunities/<int:opportunity_id>/workers/",
        views.OpportunityWorkersAPIView.as_view(),
        name="opportunity_workers",
    ),
    path(
        "opportunities/<int:opportunity_id>/learning-modules/",
        views.OpportunityLearningModulesAPIView.as_view(),
        name="opportunity_learning_modules",
    ),
    path(
        "opportunities/<int:opportunity_id>/completed-modules/",
        views.CompletedModulesAPIView.as_view(),
        name="completed_modules",
    ),
    # OCS Integration API
    path("api/ocs/bots/", views.OCSBotsListAPIView.as_view(), name="ocs_bots"),
]

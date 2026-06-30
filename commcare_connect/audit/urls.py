from django.urls import path
from django.views.generic import RedirectView

from commcare_connect.audit import views

app_name = "audit"

urlpatterns = [
    # Experiment-based audit routes (ExperimentRecord implementation)
    path("", views.ExperimentAuditListView.as_view(), name="session_list"),
    path("create/", views.ExperimentAuditCreateView.as_view(), name="creation_wizard"),
    # session_detail redirects to bulk_assessment (single view removed)
    path("<int:pk>/", RedirectView.as_view(pattern_name="audit:bulk_assessment"), name="session_detail"),
    path("<int:pk>/bulk/", views.ExperimentBulkAssessmentView.as_view(), name="bulk_assessment"),
    # API endpoints
    path(
        "api/opportunity/<int:opp_id>/image-questions/",
        views.OpportunityImageTypesAPIView.as_view(),
        name="opportunity_image_questions",
    ),
    path(
        "api/opportunity/<int:opp_id>/field-questions/",
        views.OpportunityFieldQuestionsAPIView.as_view(),
        name="opportunity_field_questions",
    ),
    path(
        "api/opportunities/search/",
        views.ExperimentOpportunitySearchAPIView.as_view(),
        name="program_search",
    ),
    path("api/audit/create/", views.ExperimentAuditCreateAPIView.as_view(), name="create_session"),
    path("api/audit/create-async/", views.ExperimentAuditCreateAsyncAPIView.as_view(), name="create_session_async"),
    path("api/audit/preview/", views.ExperimentAuditPreviewAPIView.as_view(), name="preview_audit"),
    path("api/audit/progress/", views.ExperimentAuditProgressAPIView.as_view(), name="audit_progress"),
    # Async creation progress endpoints
    path(
        "api/audit/jobs/",
        views.AuditCreationJobsAPIView.as_view(),
        name="audit_jobs_list",
    ),
    path(
        "api/audit/jobs/<int:job_id>/cancel/",
        views.AuditCreationJobCancelAPIView.as_view(),
        name="audit_job_cancel",
    ),
    path(
        "api/audit/task/<str:task_id>/status/",
        views.AuditCreationStatusAPIView.as_view(),
        name="audit_task_status",
    ),
    path(
        "api/audit/task/<str:task_id>/stream/",
        views.AuditCreationProgressStreamView.as_view(),
        name="audit_task_stream",
    ),
    path(
        "api/audit/task/<str:task_id>/cancel/",
        views.AuditCreationTaskCancelAPIView.as_view(),
        name="audit_task_cancel",
    ),
    # API endpoints for session interaction during auditing (bulk assessment)
    path(
        "api/<int:session_id>/save/",
        views.ExperimentSaveAuditView.as_view(),
        name="audit_save",
    ),
    path(
        "api/<int:session_id>/complete/",
        views.ExperimentAuditCompleteView.as_view(),
        name="audit_complete",
    ),
    path(
        "api/<int:session_id>/uncomplete/",
        views.ExperimentAuditUncompleteView.as_view(),
        name="audit_uncomplete",
    ),
    path(
        "api/<int:session_id>/apply-results/",
        views.ExperimentApplyAssessmentResultsView.as_view(),
        name="apply_assessment_results",
    ),
    path(
        "api/<int:session_id>/bulk-data/",
        views.ExperimentBulkAssessmentDataView.as_view(),
        name="bulk_assessment_data",
    ),
    path(
        "image/<int:opp_id>/<str:blob_id>/",
        views.ExperimentAuditImageConnectView.as_view(),
        name="audit_image_connect",
    ),
    # Workflow integration endpoints
    path(
        "api/workflow/<int:workflow_run_id>/sessions/",
        views.WorkflowSessionsAPIView.as_view(),
        name="workflow_sessions",
    ),
    # AI Review endpoints
    # List available agents (no session required)
    path(
        "api/ai-agents/",
        views.AIAgentsListAPIView.as_view(),
        name="ai_agents_list",
    ),
    # Session-specific AI review (run agents on assessments)
    path(
        "api/<int:session_id>/ai-review/",
        views.AIReviewAPIView.as_view(),
        name="ai_review",
    ),
    # Visit detail from production
    path("visits/<int:visit_id>/", views.VisitDetailFromProductionView.as_view(), name="visit_detail_from_production"),
]

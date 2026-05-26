from django.urls import include, path

from . import views

app_name = "workflow"

urlpatterns = [
    # Opportunity summary (all objects for current opportunity)
    path("summary/", views.OpportunitySummaryView.as_view(), name="summary"),
    # Pipeline list
    path("pipelines/", views.PipelineListView.as_view(), name="pipeline_list"),
    # Pipeline editor (standalone)
    path("pipeline/<int:definition_id>/edit/", views.PipelineEditView.as_view(), name="pipeline_edit"),
    # List all workflow definitions
    path("", views.WorkflowListView.as_view(), name="list"),
    # Create workflow from template
    path("create/", views.create_workflow_from_template_view, name="create_from_template"),
    # API endpoint - List available templates
    path("api/templates/", views.WorkflowTemplateListAPIView.as_view(), name="api_templates"),
    # Legacy: Create example workflow (redirects to create_from_template)
    path("create-example/", views.create_example_workflow, name="create_example"),
    # View workflow definition details (JSON view)
    path("<int:definition_id>/", views.WorkflowDefinitionView.as_view(), name="detail"),
    # Run/execute a workflow (main UI) - also handles edit mode via ?edit=true
    path("<int:definition_id>/run/", views.WorkflowRunView.as_view(), name="run"),
    # View specific workflow run
    path("run/<int:run_id>/", views.WorkflowRunDetailView.as_view(), name="run_detail"),
    # API endpoints - Workers
    path("api/workers/", views.get_workers_api, name="api_workers"),
    # Framework: per-template OAuth dependency check (drives the "Authorize X"
    # gate in the workflow runner before any template-specific render fires).
    path("api/auth-status/", views.workflow_auth_status_api, name="api_auth_status"),
    # API endpoints - Workflow runs
    path("api/run/<int:run_id>/state/", views.update_state_api, name="api_update_state"),
    path("api/run/<int:run_id>/worker-result/", views.save_worker_result_api, name="api_save_worker_result"),
    # Single terminal-transition verb. Atomic: build snapshot via the template's
    # `build_snapshot` hook (or the snapshot_inputs fallback), then flip status to
    # completed in one LabsRecord write. See WORKFLOW_REFERENCE.md §"Saved-runs templates".
    path("api/run/<int:run_id>/complete/", views.complete_run_api, name="api_complete_run"),
    # Read-only snapshot inspection (debug/admin). Render code reads
    # `instance.snapshot` from props via the `useRunView` helper, not this URL.
    path("api/run/<int:run_id>/snapshot/", views.get_snapshot_api, name="api_get_snapshot"),
    # Explicit run creation — replaces the implicit auto-create that fired on every
    # visit to /workflow/<def>/run/ with no run_id.
    path("api/<int:definition_id>/run/start/", views.start_run_api, name="api_start_run"),
    path("api/run/<int:run_id>/", views.get_run_api, name="api_get_run"),
    # API endpoints - Chat history
    path("api/<int:definition_id>/chat/history/", views.get_chat_history_api, name="api_chat_history"),
    path("api/<int:definition_id>/chat/message/", views.add_chat_message_api, name="api_chat_message"),
    path("api/<int:definition_id>/chat/clear/", views.clear_chat_history_api, name="api_chat_clear"),
    # API endpoints - Render code
    path("api/<int:definition_id>/render-code/", views.save_render_code_api, name="api_save_render_code"),
    path("api/<int:definition_id>/sync-template/", views.sync_template_render_code_api, name="api_sync_template"),
    # API endpoints - OCS integration
    path("api/ocs/status/", views.ocs_status_api, name="api_ocs_status"),
    path("api/ocs/bots/", views.ocs_bots_api, name="api_ocs_bots"),
    # API endpoints - Pipeline data
    path("api/<int:definition_id>/pipeline-data/", views.get_pipeline_data_api, name="api_pipeline_data"),
    path(
        "api/<int:definition_id>/pipeline-data/stream/",
        views.PipelineDataStreamView.as_view(),
        name="api_pipeline_data_stream",
    ),
    path(
        "api/<int:definition_id>/pipeline-sources/add/", views.add_pipeline_source_api, name="api_add_pipeline_source"
    ),
    path(
        "api/<int:definition_id>/pipeline-sources/remove/",
        views.remove_pipeline_source_api,
        name="api_remove_pipeline_source",
    ),
    path("api/available-pipelines/", views.list_available_pipelines_api, name="api_available_pipelines"),
    # API endpoints - Pipeline editor
    path("api/pipeline/<int:definition_id>/", views.get_pipeline_definition_api, name="api_pipeline_definition"),
    path("api/pipeline/<int:definition_id>/schema/", views.update_pipeline_schema_api, name="api_pipeline_schema"),
    path("api/pipeline/<int:definition_id>/preview/", views.execute_pipeline_preview_api, name="api_pipeline_preview"),
    path("api/pipeline/<int:definition_id>/sql/", views.get_pipeline_sql_preview_api, name="api_pipeline_sql"),
    path(
        "api/pipeline/<int:definition_id>/chat/history/",
        views.get_pipeline_chat_history_api,
        name="api_pipeline_chat_history",
    ),
    path(
        "api/pipeline/<int:definition_id>/chat/clear/",
        views.clear_pipeline_chat_history_api,
        name="api_pipeline_chat_clear",
    ),
    # API endpoints - Workflow Sharing
    path("api/<int:definition_id>/share/", views.share_workflow_api, name="api_share"),
    path("api/<int:definition_id>/unshare/", views.unshare_workflow_api, name="api_unshare"),
    path("api/<int:definition_id>/copy/", views.copy_workflow_api, name="api_copy"),
    path("api/shared/", views.list_shared_workflows_api, name="api_list_shared"),
    # API endpoints - Pipeline Sharing
    path("api/pipeline/<int:definition_id>/share/", views.share_pipeline_api, name="api_pipeline_share"),
    path("api/pipeline/<int:definition_id>/unshare/", views.unshare_pipeline_api, name="api_pipeline_unshare"),
    path("api/pipeline/<int:definition_id>/copy/", views.copy_pipeline_api, name="api_pipeline_copy"),
    path("api/pipeline/shared/", views.list_shared_pipelines_api, name="api_pipeline_list_shared"),
    # API endpoints - Workflow management
    path("api/<int:definition_id>/delete/", views.delete_workflow_api, name="api_delete"),
    path("api/<int:definition_id>/rename/", views.rename_workflow_api, name="api_rename"),
    path(
        "api/<int:definition_id>/opportunity-ids/",
        views.UpdateOpportunityIdsView.as_view(),
        name="api_update_opportunity_ids",
    ),
    # API endpoints - Pipeline management
    path("api/pipeline/<int:definition_id>/delete/", views.delete_pipeline_api, name="api_pipeline_delete"),
    # API endpoints - Workflow Jobs
    path("api/run/<int:run_id>/job/start/", views.start_job_api, name="api_start_job"),
    path("api/job/<str:task_id>/status/", views.JobStatusStreamView.as_view(), name="api_job_status"),
    path("api/job/<str:task_id>/cancel/", views.cancel_job_api, name="api_cancel_job"),
    path("api/run/<int:run_id>/delete/", views.delete_run_api, name="api_delete_run"),
    path("api/open-tasks/", views.open_tasks_api, name="api_open_tasks"),
    path("api/prev-categories/", views.prev_categories_api, name="api_prev_categories"),
    # Image proxy and visit images API
    path("api/image/<int:opp_id>/<str:blob_id>/", views.WorkflowImageProxyView.as_view(), name="api_image_proxy"),
    path("api/<int:opp_id>/visit-images/", views.visit_images_api, name="api_visit_images"),
    # Decisions scoped to a workflow run — implemented in commcare_connect/decisions
    path("api/run/<int:workflow_run_id>/decisions/", include("commcare_connect.decisions.urls")),
]

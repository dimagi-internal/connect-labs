from django.urls import path

from commcare_connect.microplans import views

app_name = "microplans"

urlpatterns = [
    path("<int:opp_id>/preview_frame/", views.PreviewFrameView.as_view(), name="preview_frame"),
    path("<int:opp_id>/preview_coverage/", views.PreviewCoverageView.as_view(), name="preview_coverage"),
    path("<int:opp_id>/preview_footprints/", views.PreviewFootprintsView.as_view(), name="preview_footprints"),
    # Poll a queued preview/generation task (frame / coverage / footprints).
    path("preview_status/<str:task_id>/", views.PreviewStatusView.as_view(), name="preview_status"),
    path(
        "<int:opp_id>/preview_service_delivery/",
        views.PreviewServiceDeliveryView.as_view(),
        name="preview_service_delivery",
    ),
    path(
        "<int:opp_id>/service_delivery_pipelines/",
        views.ServiceDeliveryPipelinesView.as_view(),
        name="service_delivery_pipelines",
    ),
    path("<int:opp_id>/derive_boundary/", views.DeriveBoundaryView.as_view(), name="derive_boundary"),
    path("<int:opp_id>/arm_comparability/", views.ArmComparabilityView.as_view(), name="arm_comparability"),
    path("<int:opp_id>/boundaries/areas/", views.AdminAreasView.as_view(), name="admin_areas"),
    path("<int:opp_id>/boundaries/geometry/", views.AdminAreaGeometryView.as_view(), name="admin_area_geometry"),
    path("boundaries/countries/", views.CountriesView.as_view(), name="countries"),
    path("boundaries/viewport/", views.BoundaryViewportView.as_view(), name="boundary_viewport"),
    # Program layer: a program owns a portfolio of candidate plans + plan groups.
    path("program/<int:program_id>/", views.ProgramWorkspaceView.as_view(), name="program_workspace"),
    path("program/<int:program_id>/plans.json", views.ProgramPlansAPIView.as_view(), name="program_plans"),
    path("program/<int:program_id>/compare/", views.ProgramComparePageView.as_view(), name="program_compare_page"),
    # Plan-quality metric glossary — definitions of every column shown on the
    # compare page. Program-scope-agnostic; one page covers the whole vocabulary.
    path("glossary/", views.MetricGlossaryView.as_view(), name="metric_glossary"),
    path(
        "program/<int:program_id>/plan/compare/", views.ProgramComparePlansView.as_view(), name="program_plan_compare"
    ),
    path("program/<int:program_id>/new/", views.ProgramSetupView.as_view(), name="program_create_plan_page"),
    path("program/<int:program_id>/plan/create/", views.ProgramCreatePlanView.as_view(), name="program_create_plan"),
    # Bulk-create: paste a ward list, resolve against admin_boundaries,
    # preview matched + unresolved rows, confirm, then materialize one plan per ward
    # in one server call.
    path(
        "program/<int:program_id>/bulk_create/",
        views.ProgramBulkCreatePlanPageView.as_view(),
        name="program_bulk_create_page",
    ),
    path(
        "program/<int:program_id>/plan/bulk_create/",
        views.ProgramBulkCreatePlansView.as_view(),
        name="program_bulk_create",
    ),
    # Poll a queued bulk-create batch (incremental per-ward results).
    path(
        "bulk_create_status/<str:task_id>/",
        views.ProgramBulkCreateStatusView.as_view(),
        name="bulk_create_status",
    ),
    path("program/<int:program_id>/plan/<int:plan_id>/", views.ProgramPlanView.as_view(), name="program_plan"),
    path(
        "program/<int:program_id>/plan/<int:plan_id>/edit/",
        views.ProgramPlanEditView.as_view(),
        name="program_plan_edit",
    ),
    path(
        "program/<int:program_id>/plan/<int:plan_id>/work_areas.csv",
        views.ProgramPlanCSVView.as_view(),
        name="program_plan_csv",
    ),
    path(
        "program/<int:program_id>/plan/<int:plan_id>/footprints/",
        views.ProgramPlanFootprintsView.as_view(),
        name="program_plan_footprints",
    ),
    path(
        "program/<int:program_id>/plan/<int:plan_id>/footprints/refresh/",
        views.ProgramPlanFootprintsRefreshView.as_view(),
        name="program_plan_footprints_refresh",
    ),
    path(
        "program/<int:program_id>/plan/<int:plan_id>/transition/",
        views.ProgramPlanTransitionView.as_view(),
        name="program_plan_transition",
    ),
    path(
        "program/<int:program_id>/plan/<int:plan_id>/regroup/",
        views.ProgramPlanRegroupView.as_view(),
        name="program_plan_regroup",
    ),
    path(
        "program/<int:program_id>/plan/<int:plan_id>/regenerate/",
        views.ProgramPlanRegenerateView.as_view(),
        name="program_plan_regenerate",
    ),
    path(
        "program/<int:program_id>/plan/<int:plan_id>/reassign/",
        views.ProgramPlanReassignView.as_view(),
        name="program_plan_reassign",
    ),
    path(
        "program/<int:program_id>/plan/<int:plan_id>/delete/",
        views.ProgramPlanDeleteView.as_view(),
        name="program_plan_delete",
    ),
    path(
        "program/<int:program_id>/group/<int:group_id>/delete/",
        views.ProgramGroupDeleteView.as_view(),
        name="program_group_delete",
    ),
    path(
        "program/<int:program_id>/plan/<int:plan_id>/review/", views.ProgramReviewView.as_view(), name="program_review"
    ),
    path("program/<int:program_id>/groups/create/", views.ProgramGroupsAPIView.as_view(), name="program_group_create"),
    path(
        "program/<int:program_id>/group/<int:group_id>/",
        views.ProgramGroupUpdateView.as_view(),
        name="program_group_update",
    ),
    path(
        "program/<int:program_id>/group/<int:group_id>/share/",
        views.ProgramGroupShareView.as_view(),
        name="program_group_share",
    ),
    path(
        "program/<int:program_id>/group/<int:group_id>/manage/",
        views.ProgramGroupPageView.as_view(),
        name="program_group_page",
    ),
    path(
        "program/<int:program_id>/group/<int:group_id>/map/",
        views.ProgramGroupMapView.as_view(),
        name="program_group_map",
    ),
    path(
        "program/<int:program_id>/group/<int:group_id>/generate/",
        views.ProgramGroupGenerateView.as_view(),
        name="program_group_generate",
    ),
    # Map-based "Add wards from map": full-page boundary-picker surface + its
    # bulk create-into-study endpoint (one boundary-only plan per selected boundary).
    path(
        "program/<int:program_id>/group/<int:group_id>/add-from-map/",
        views.ProgramGroupAddFromMapView.as_view(),
        name="program_group_add_from_map",
    ),
    path(
        "program/<int:program_id>/group/<int:group_id>/bulk_create_from_boundaries/",
        views.ProgramGroupBulkCreateFromBoundariesView.as_view(),
        name="program_group_bulk_create_from_boundaries",
    ),
]

from django.urls import path

from connect_labs.mopup import views

app_name = "mopup"

urlpatterns = [
    path("program/<int:program_id>/", views.MopupProgramHomeView.as_view(), name="program_home"),
    path("program/<int:program_id>/setup/", views.MopupSetupView.as_view(), name="setup"),
    path("program/<int:program_id>/ward_list/", views.MopupWardListView.as_view(), name="ward_list"),
    path("program/<int:program_id>/create_run/", views.MopupCreateRunView.as_view(), name="create_run"),
    path(
        "program/<int:program_id>/run/<int:run_id>/analysis/",
        views.MopupAnalysisView.as_view(),
        name="analysis",
    ),
    path(
        "program/<int:program_id>/run/<int:run_id>/candidates/",
        views.MopupCandidatesView.as_view(),
        name="candidates",
    ),
    path(
        "program/<int:program_id>/run/<int:run_id>/lock/",
        views.MopupLockView.as_view(),
        name="lock",
    ),
    path(
        "program/<int:program_id>/run/<int:run_id>/create_plan/",
        views.MopupCreatePlanView.as_view(),
        name="create_plan",
    ),
    # TEMPORARY — diagnostic only, remove after the entity_id-null investigation
    # is resolved. Calls fetch_cchq_cases_as_visit_dicts directly, bypassing the
    # SQL cache/query layers entirely, so we can see the raw normalized case
    # dict exactly as this app's own Celery task would receive it.
    path(
        "program/<int:program_id>/debug_raw_case/",
        views.MopupDebugRawCaseView.as_view(),
        name="debug_raw_case",
    ),
]

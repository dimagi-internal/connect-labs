from django.urls import path

from connect_labs.mopup import views

app_name = "mopup"

urlpatterns = [
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
]

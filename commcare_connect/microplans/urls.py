from django.urls import path

from commcare_connect.microplans import views

app_name = "microplans"

urlpatterns = [
    path("<int:opp_id>/setup/", views.SetupView.as_view(), name="setup"),
    path("<int:opp_id>/preview_frame/", views.PreviewFrameView.as_view(), name="preview_frame"),
    path("<int:opp_id>/preview_coverage/", views.PreviewCoverageView.as_view(), name="preview_coverage"),
    path("<int:opp_id>/save_frame/", views.SaveFrameView.as_view(), name="save_frame"),
    path("<int:opp_id>/work_areas.csv", views.DownloadWorkAreaCSVView.as_view(), name="work_areas_csv"),
    path("<int:opp_id>/boundaries/areas/", views.AdminAreasView.as_view(), name="admin_areas"),
    path("<int:opp_id>/boundaries/geometry/", views.AdminAreaGeometryView.as_view(), name="admin_area_geometry"),
    path("boundaries/countries/", views.CountriesView.as_view(), name="countries"),
    # Planning-phase plan review/edit (LLO validation, pre-upload)
    path("<int:opp_id>/review/<int:plan_id>/", views.ReviewView.as_view(), name="review"),
    path("<int:opp_id>/compare/", views.ComparePageView.as_view(), name="compare"),
    path("<int:opp_id>/plans/", views.PlanListView.as_view(), name="plan_list"),
    path("<int:opp_id>/plan/compare/", views.ComparePlansView.as_view(), name="plan_compare"),
    path("<int:opp_id>/plan/materialize/", views.MaterializePlanView.as_view(), name="plan_materialize"),
    path("<int:opp_id>/plan/<int:plan_id>/", views.PlanView.as_view(), name="plan"),
    path("<int:opp_id>/plan/<int:plan_id>/edit/", views.PlanEditView.as_view(), name="plan_edit"),
    path("<int:opp_id>/plan/<int:plan_id>/work_areas.csv", views.PlanCSVView.as_view(), name="plan_csv"),
]

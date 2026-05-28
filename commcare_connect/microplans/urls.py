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
]

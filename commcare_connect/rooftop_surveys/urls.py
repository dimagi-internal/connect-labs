from django.urls import path

from commcare_connect.rooftop_surveys import views

app_name = "rooftop_surveys"

urlpatterns = [
    path("<int:opp_id>/setup/", views.SetupView.as_view(), name="setup"),
    path("<int:opp_id>/preview_frame/", views.PreviewFrameView.as_view(), name="preview_frame"),
    path("<int:opp_id>/save_frame/", views.SaveFrameView.as_view(), name="save_frame"),
]

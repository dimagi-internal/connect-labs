from django.urls import path

from . import views

app_name = "embed"

urlpatterns = [
    path("token/", views.token_view, name="token"),
    path("session/", views.session_view, name="session"),
    path("c/<slug:client_id>/<slug:kind>/<slug:resource_id>/", views.frame_view, name="frame"),
]

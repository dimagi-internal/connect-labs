from django.urls import path

from connect_labs.scope_config import views

# Mounted inside the labs namespace (labs/urls.py), so its name is `labs:settings`.
urlpatterns = [
    path("settings/<str:scope_type>/<str:scope_key>/", views.SettingsView.as_view(), name="settings"),
]

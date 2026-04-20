from django.urls import path

from . import views
from .admin_views import create_token_browser

app_name = "mcp"

urlpatterns = [
    path("", views.mcp_endpoint, name="endpoint"),
    path("admin/create-token/", create_token_browser, name="admin_create_token"),
]

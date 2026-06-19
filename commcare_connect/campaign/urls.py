from django.urls import path

from . import views
from .auth import oauth_views

app_name = "campaign"

urlpatterns = [
    path("ping/", views.ping, name="ping"),
    path("login/", oauth_views.login_page, name="login"),
    path("login/initiate/", oauth_views.oauth_initiate, name="oauth_initiate"),
    path("login/callback/", oauth_views.oauth_callback, name="oauth_callback"),
    path("logout/", oauth_views.logout_view, name="logout"),
    path("", views.AppView.as_view(), name="app"),
]

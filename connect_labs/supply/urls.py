from django.urls import path

from . import views

app_name = "supply"

urlpatterns = [
    path("ping/", views.ping, name="ping"),
    path("login/", views.login_view, name="login"),
    path("signup/", views.signup_view, name="signup"),
    path("logout/", views.logout_view, name="logout"),
    path("", views.app_view, name="app"),
]

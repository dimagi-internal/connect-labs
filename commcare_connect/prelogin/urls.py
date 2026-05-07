from django.urls import path

from . import views

app_name = "prelogin"

urlpatterns = [
    path("", views.home, name="home"),
]

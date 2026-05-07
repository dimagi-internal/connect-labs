from django.urls import path

from . import views

app_name = "prelogin_website"

urlpatterns = [
    path("", views.home, name="home"),
]

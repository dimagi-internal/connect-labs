from django.urls import path

from . import views

app_name = "supply"

urlpatterns = [
    path("ping/", views.ping, name="ping"),
]

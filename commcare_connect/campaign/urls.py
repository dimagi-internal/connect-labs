from django.urls import path

from . import views

app_name = "campaign"

urlpatterns = [
    path("ping/", views.ping, name="ping"),
]

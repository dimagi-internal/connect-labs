from django.urls import path

from connect_labs.marketplace import views

app_name = "marketplace"

urlpatterns = [
    path("", views.directory, name="directory"),
    path("unmatched/", views.unmatched, name="unmatched"),
    path("<slug:slug>/", views.organisation, name="organisation"),
]

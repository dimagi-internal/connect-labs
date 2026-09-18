from django.urls import path

from connect_labs.marketplace import views

app_name = "marketplace"

urlpatterns = [
    path("", views.home, name="home"),
    path("programmes/", views.programmes_page, name="programmes"),
    path("network/", views.network, name="network"),
    path("network/points/", views.network_points, name="network_points"),
    path("rounds/", views.rounds, name="rounds"),
    path("rounds/<slug:slug>/", views.round_detail, name="round"),
    path("unmatched/", views.unmatched, name="unmatched"),
    path("org/<slug:slug>/", views.organisation, name="organisation"),
]

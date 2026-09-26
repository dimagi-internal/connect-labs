from django.urls import path
from django.views.generic import RedirectView

from connect_labs.marketplace import views

app_name = "marketplace"

urlpatterns = [
    path("", views.home, name="home"),
    # The programs page IS the marketplace now, so both of its former
    # addresses land on it rather than each serving their own copy.
    path("programs/", RedirectView.as_view(pattern_name="marketplace:home", permanent=True), name="programs"),
    path("programmes/", RedirectView.as_view(pattern_name="marketplace:home", permanent=True)),
    path("fonts/", views.fonts, name="fonts"),
    path("network/", views.network, name="network"),
    path("network/points/", views.network_points, name="network_points"),
    path("rounds/", views.rounds, name="rounds"),
    path("rounds/<slug:slug>/", views.round_detail, name="round"),
    path("unmatched/", views.unmatched, name="unmatched"),
    path("org/<slug:slug>/", views.organisation, name="organisation"),
]

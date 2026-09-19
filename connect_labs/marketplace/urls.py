from django.urls import path
from django.views.generic import RedirectView

from connect_labs.marketplace import views

app_name = "marketplace"

urlpatterns = [
    path("", views.home, name="home"),
    path("programs/", views.programs_page, name="programs"),
    # The page's first address, already shared before the rename.
    path("programmes/", RedirectView.as_view(pattern_name="marketplace:programs", permanent=True)),
    path("network/", views.network, name="network"),
    path("network/points/", views.network_points, name="network_points"),
    path("rounds/", views.rounds, name="rounds"),
    path("rounds/<slug:slug>/", views.round_detail, name="round"),
    path("unmatched/", views.unmatched, name="unmatched"),
    path("org/<slug:slug>/", views.organisation, name="organisation"),
]

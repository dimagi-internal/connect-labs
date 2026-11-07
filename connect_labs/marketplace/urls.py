from django.urls import path
from django.views.generic import RedirectView

from connect_labs.marketplace import views

app_name = "marketplace"

urlpatterns = [
    path("", views.home, name="home"),
    # The programs page IS the marketplace now, so both of its former
    # addresses land on it rather than each serving their own copy.
    path(
        "programs/",
        RedirectView.as_view(pattern_name="marketplace:home", permanent=True),
        name="programs",
    ),
    path(
        "programmes/",
        RedirectView.as_view(pattern_name="marketplace:home", permanent=True),
    ),
    path("fonts/", views.fonts, name="fonts"),
    path("network/", views.network, name="network"),
    path("network/points/", views.network_points, name="network_points"),
    path("rounds/", views.rounds, name="rounds"),
    path("rounds/<slug:slug>/", views.round_detail, name="round"),
    # The Review tab: one URL per step (`marketplace/review_steps.py`); a bare
    # /review/ opens on the step with something still to do.
    path("rounds/<slug:slug>/review/", views.round_review, name="round_review"),
    path(
        "rounds/<slug:slug>/review/<slug:step>/",
        views.round_review,
        name="round_review_step",
    ),
    # The old Scores page, redirected into Review so sent links keep working.
    path("rounds/<slug:slug>/scores/", views.round_scores, name="round_scores"),
    path("rounds/<slug:slug>/summary/", views.round_summary, name="round_summary"),
    path(
        "rounds/<slug:slug>/versions/<str:version>/delete/",
        views.round_version_delete,
        name="round_version_delete",
    ),
    path("unmatched/", views.unmatched, name="unmatched"),
    path("org/<slug:slug>/", views.organisation, name="organisation"),
]

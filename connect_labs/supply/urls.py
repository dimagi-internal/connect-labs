from django.urls import path

from . import views
from .api import eoi as eoi_api
from .api import orgs as orgs_api

app_name = "supply"

urlpatterns = [
    path("ping/", views.ping, name="ping"),
    path("login/", views.login_view, name="login"),
    path("signup/", views.signup_view, name="signup"),
    path("logout/", views.logout_view, name="logout"),
    # --- org profile ---
    path("api/org/profile/", orgs_api.profile, name="api_org_profile"),
    path("api/org/certifications/", orgs_api.certifications, name="api_org_certifications"),
    path(
        "api/org/certifications/<int:cert_id>/delete/",
        orgs_api.delete_certification,
        name="api_org_certification_delete",
    ),
    # --- EOI ---
    path("api/eoi/rounds/", eoi_api.rounds, name="api_eoi_rounds"),
    path("api/eoi/rounds/<int:round_id>/transition/", eoi_api.transition_round, name="api_eoi_round_transition"),
    path("api/eoi/submissions/", eoi_api.submissions, name="api_eoi_submissions"),
    path(
        "api/eoi/submissions/<int:submission_id>/submit/",
        eoi_api.submit_submission,
        name="api_eoi_submission_submit",
    ),
    path(
        "api/eoi/submissions/<int:submission_id>/review/",
        eoi_api.review_submission,
        name="api_eoi_submission_review",
    ),
    path("api/eoi/review-queue/", eoi_api.review_queue, name="api_eoi_review_queue"),
    path("api/registry/", eoi_api.registry, name="api_registry"),
    path("", views.app_view, name="app"),
]

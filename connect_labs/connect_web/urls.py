from django.db import transaction
from django.urls import path

from . import views

app_name = "connect_web"


def _view(view_class):
    # Labs runs with ATOMIC_REQUESTS. A sign-in must keep what it learned (a rotated PersonalID
    # password, a pause after a rejected backup code) even when the request then fails, so these
    # views manage their own transactions (service.ensure_signed_in).
    return transaction.non_atomic_requests(view_class.as_view())


urlpatterns = [
    path("", _view(views.HomeView), name="home"),
    path("workers/", _view(views.WorkerPickerView), name="workers"),
    path("workers/release/", _view(views.ReleaseView), name="release"),
    path("opportunities/", _view(views.OpportunityListView), name="opportunities"),
    path("opportunities/<str:opportunity_id>/", _view(views.OpportunityDetailView), name="opportunity"),
    path(
        "opportunities/<str:opportunity_id>/start-learning/",
        _view(views.StartLearningView),
        name="start_learning",
    ),
    path("opportunities/<str:opportunity_id>/claim/", _view(views.ClaimView), name="claim"),
    path("opportunities/<str:opportunity_id>/payments/", _view(views.ConfirmPaymentView), name="confirm_payment"),
    path(
        "opportunities/<str:opportunity_id>/form/<str:app_kind>/",
        _view(views.FormEntryView),
        name="form_entry",
    ),
]

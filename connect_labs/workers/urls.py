from django.urls import path

from . import views

app_name = "workers"

urlpatterns = [
    # One Connect Worker: profile, memberships, timeline, facts, staff notes, coverage.
    path("<str:username>/", views.WorkerPageView.as_view(), name="worker"),
]

from django.urls import path

from commcare_connect.labs.synthetic import views

app_name = "synthetic"

urlpatterns = [
    path("", views.SyntheticListView.as_view(), name="list"),
    path("new/", views.SyntheticCreateView.as_view(), name="new"),
    path("<int:pk>/edit/", views.SyntheticUpdateView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.SyntheticDeleteView.as_view(), name="delete"),
    path("<int:pk>/reload/", views.reload_fixtures_view, name="reload"),
    path("dump/stream/", views.DumpStreamView.as_view(), name="dump_stream"),
    path("refresh/", views.refresh_cache_view, name="refresh"),
    path("test-access/", views.test_access_view, name="test_access"),
]

"""URLs for the SQL explorer, mounted at /labs/explorer/ (namespace ``explorer``)."""

from django.urls import path

from connect_labs.explorer import views

app_name = "explorer"

urlpatterns = [
    path("", views.ExplorerView.as_view(), name="index"),
    path("api/describe/", views.DescribeView.as_view(), name="describe"),
    path("api/query/", views.QueryView.as_view(), name="query"),
]

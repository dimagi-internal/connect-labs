"""URLs for labs data, mounted at /labs/data/ (namespace ``labsdata``)."""

from django.urls import path

from connect_labs.labsdata import views

app_name = "labsdata"

urlpatterns = [
    path("", views.DataView.as_view(), name="index"),
    path("api/describe/", views.DescribeView.as_view(), name="describe"),
    path("api/query/", views.QueryView.as_view(), name="query"),
    path("api/visits/", views.VisitsView.as_view(), name="visits"),
    path("api/workflows/", views.WorkflowsView.as_view(), name="workflows"),
    path("api/drill/", views.DrillView.as_view(), name="drill"),
    path("api/saved/", views.SavedQueriesView.as_view(), name="saved"),
    path("api/saved/<int:pk>/delete/", views.SavedQueryDeleteView.as_view(), name="saved_delete"),
]

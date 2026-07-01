"""
URL routing for Labs Explorer
"""

from django.urls import include, path

from . import views

app_name = "explorer"

urlpatterns = [
    # Explorer Landing Page
    path("", views.ExplorerIndexView.as_view(), name="index"),
    # Labs Record
    path("records/", views.RecordListView.as_view(), name="list"),
    path("records/<int:pk>/edit/", views.RecordEditView.as_view(), name="edit"),
    path("records/download/", views.DownloadRecordsView.as_view(), name="download"),
    path("records/upload/", views.UploadRecordsView.as_view(), name="upload"),
    path("records/delete/", views.DeleteRecordsView.as_view(), name="delete"),
    # Visit Inspector
    path("visit-inspector/", views.VisitInspectorView.as_view(), name="visit_inspector"),
    path("visit-inspector/stream/", views.VisitInspectorStreamView.as_view(), name="visit_inspector_stream"),
    path("visit-inspector/query/", views.VisitInspectorQueryView.as_view(), name="visit_inspector_query"),
    path("visit-inspector/view/<int:visit_id>/", views.VisitViewView.as_view(), name="view_visit"),
    path("visit-inspector/download/<int:visit_id>/", views.DownloadVisitView.as_view(), name="download_visit"),
    # Cache Manager
    path("cache/", views.CacheManagerView.as_view(), name="cache_manager"),
    path("cache/delete/", views.CacheDeleteView.as_view(), name="cache_delete"),
    path("cache/stats/", views.CacheStatsAPIView.as_view(), name="cache_stats"),
    # Task Manager
    path("tasks/", views.TaskManagerView.as_view(), name="task_manager"),
    path("tasks/kill/", views.TaskKillView.as_view(), name="task_kill"),
    path("tasks/status/<str:task_id>/", views.TaskStatusAPIView.as_view(), name="task_status"),
    # App Downloader
    path("app-downloader/", views.AppDownloaderView.as_view(), name="app_downloader"),
    path("app-downloader/download/<int:opp_id>/<str:app_type>/", views.DownloadAppView.as_view(), name="download_app"),
    path("app-downloader/bulk-download/", views.BulkDownloadAppsView.as_view(), name="bulk_download_apps"),
    # Admin Boundaries
    path("boundaries/", include("connect_labs.labs.admin_boundaries.urls")),
]

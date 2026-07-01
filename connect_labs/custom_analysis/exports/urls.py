from django.urls import path

from . import views

app_name = "exports"

urlpatterns = [
    path("", views.ExportsIndexView.as_view(), name="index"),
    path("download/", views.DownloadExportView.as_view(), name="download"),
]

from django.urls import path

from connect_labs.benchmarks import api_views

app_name = "benchmarks"

urlpatterns = [
    path("api/<int:opportunity_id>/", api_views.benchmarks_api, name="api_benchmarks"),
]

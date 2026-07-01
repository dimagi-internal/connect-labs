"""
URL configuration for CHC Nutrition analysis.
"""

from django.urls import path

from connect_labs.custom_analysis.chc_nutrition import views

app_name = "chc_nutrition"

urlpatterns = [
    path("", views.CHCNutritionAnalysisView.as_view(), name="analysis"),
    path("api/data/", views.CHCNutritionDataView.as_view(), name="api_data"),
    path("api/stream/", views.CHCNutritionStreamView.as_view(), name="api_stream"),
]

from django.urls import path

from . import api_views, views

app_name = "funder_dashboard"

urlpatterns = [
    # Dashboard views
    path("", views.PortfolioDashboardView.as_view(), name="portfolio"),
    path("fund/<int:pk>/", views.FundDetailView.as_view(), name="fund_detail"),
    # Fund CRUD
    path("fund/create/", views.FundCreateView.as_view(), name="fund_create"),
    path("fund/<int:pk>/edit/", views.FundEditView.as_view(), name="fund_edit"),
    # JSON API
    path("api/funds/", api_views.api_funds_list, name="api_funds_list"),
    path("api/funds/<int:pk>/", api_views.api_fund_detail, name="api_fund_detail"),
]

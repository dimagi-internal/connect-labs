from django.urls import path

from . import api_views, views

app_name = "solicitations"

urlpatterns = [
    # Public (no login required)
    path("", views.PublicSolicitationListView.as_view(), name="public_list"),
    path("<int:pk>/", views.PublicSolicitationDetailView.as_view(), name="public_detail"),
    # Manager views (login required)
    path("manage/", views.ManageSolicitationsView.as_view(), name="manage_list"),
    path("create/", views.SolicitationCreateView.as_view(), name="create"),
    path("<int:pk>/edit/", views.SolicitationEditView.as_view(), name="edit"),
    path("<int:pk>/responses/", views.ResponsesListView.as_view(), name="responses_list"),
    # Response (login required)
    path("<int:pk>/respond/", views.RespondView.as_view(), name="respond"),
    path("response/<int:pk>/", views.ResponseDetailView.as_view(), name="response_detail"),
    # Award
    path("response/<int:pk>/award/", views.AwardView.as_view(), name="award"),
    # Review (manager required)
    path("response/<int:pk>/review/", views.ReviewView.as_view(), name="review"),
    # JSON API
    path("api/solicitations/", api_views.api_solicitations_list, name="api_solicitations_list"),
    path("api/solicitations/<int:pk>/", api_views.api_solicitation_detail, name="api_solicitation_detail"),
    path("api/responses/", api_views.api_responses_list, name="api_responses_list"),
    path("api/responses/<int:pk>/", api_views.api_response_detail, name="api_response_detail"),
    path("api/reviews/", api_views.api_reviews_create, name="api_reviews_create"),
    path("api/reviews/<int:pk>/", api_views.api_review_detail, name="api_review_detail"),
]

from django.urls import path

from . import views

app_name = "solicitations"

urlpatterns = [
    # Readable without a labs login: the directory, a call, an organisation's calls.
    path("", views.PublicSolicitationListView.as_view(), name="public_list"),
    path("<int:pk>/", views.PublicSolicitationDetailView.as_view(), name="public_detail"),
    path("o/<str:org_slug>/", views.OrgSolicitationsView.as_view(), name="org_page"),
    # A past call: one of the marketplace's historical rounds, read-only (slug or id).
    path("past/<str:slug>/", views.PastCallDetailView.as_view(), name="past_detail"),
    # An owner's workspace (login; members of the owning organisation, or staff).
    path("manage/", views.ManageSolicitationsView.as_view(), name="manage_list"),
    path("o/<str:org_slug>/manage/", views.OrgManageView.as_view(), name="org_manage"),
    path("create/", views.SolicitationCreateView.as_view(), name="create"),
    path("<int:pk>/edit/", views.SolicitationEditView.as_view(), name="edit"),
    path("<int:pk>/responses/", views.ResponsesListView.as_view(), name="responses_list"),
    # Responding (login).
    path("<int:pk>/respond/", views.RespondView.as_view(), name="respond"),
    path("response/<int:pk>/", views.ResponseDetailView.as_view(), name="response_detail"),
    # Award and blind review (owner or staff, never on your own side).
    path("response/<int:pk>/award/", views.AwardView.as_view(), name="award"),
    path("response/<int:pk>/review/", views.ReviewView.as_view(), name="review"),
    path("review/<int:pk>/delete/", views.DeleteReviewView.as_view(), name="review_delete"),
    # AI-assisted criteria generation for the create form.
    path("api/generate-criteria/", views.generate_criteria_api, name="generate_criteria"),
]

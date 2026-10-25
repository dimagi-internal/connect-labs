"""URLs for labs data, mounted at /labs/data/ (namespace ``labsdata``)."""

from django.urls import path

from connect_labs.labsdata import views

app_name = "labsdata"

urlpatterns = [
    path("", views.DataView.as_view(), name="index"),
    # A data space's page: the Data page on the space's sources (spaces.open, else 404).
    path("space/<int:space_id>/", views.DataView.as_view(), name="space"),
    path("api/spaces/", views.SpacesView.as_view(), name="spaces"),
    path("api/spaces/<int:space_id>/", views.SpaceView.as_view(), name="space_detail"),
    path("api/spaces/<int:space_id>/sources/", views.SpaceSourcesView.as_view(), name="space_sources"),
    path("api/spaces/<int:space_id>/archive/", views.SpaceArchiveView.as_view(), name="space_archive"),
    path("api/spaces/<int:space_id>/audience/", views.SpaceAudienceView.as_view(), name="space_audience"),
    path("api/spaces/<int:space_id>/managers/", views.SpaceManagersView.as_view(), name="space_managers"),
    path(
        "api/spaces/<int:space_id>/managers/<int:user_id>/remove/",
        views.SpaceManagerRemoveView.as_view(),
        name="space_manager_remove",
    ),
    # Labs admin (Dimagi staff): make someone who can open the space a manager.
    path(
        "api/spaces/<int:space_id>/admin/managers/",
        views.SpaceAdminManagerView.as_view(),
        name="space_admin_managers",
    ),
    path("api/spaces/<int:space_id>/items/", views.SpaceItemsView.as_view(), name="space_items"),
    path(
        "api/spaces/<int:space_id>/items/<int:pk>/delete/",
        views.SpaceItemDeleteView.as_view(),
        name="space_item_delete",
    ),
    # Artifacts: render code and its named queries, drawn only in the workflow sandbox.
    path("api/spaces/<int:space_id>/artifacts/", views.SpaceArtifactsView.as_view(), name="space_artifacts"),
    path(
        "api/spaces/<int:space_id>/artifacts/<int:pk>/run/",
        views.SpaceArtifactRunView.as_view(),
        name="space_artifact_run",
    ),
    path(
        "api/spaces/<int:space_id>/artifacts/<int:pk>/delete/",
        views.SpaceArtifactDeleteView.as_view(),
        name="space_artifact_delete",
    ),
    # Saved conversations: pointers to canopy chat sessions (never a transcript).
    path(
        "api/spaces/<int:space_id>/conversations/", views.SpaceConversationsView.as_view(), name="space_conversations"
    ),
    path(
        "api/spaces/<int:space_id>/conversations/<int:pk>/delete/",
        views.SpaceConversationDeleteView.as_view(),
        name="space_conversation_delete",
    ),
    path("api/describe/", views.DescribeView.as_view(), name="describe"),
    # The plain page's selection, as the person changes it: what its agent may read.
    path("api/page-holds/", views.PageHoldsView.as_view(), name="page_holds"),
    path("api/query/", views.QueryView.as_view(), name="query"),
    # A draft artifact's named queries, run as the person for the page's sandboxed frame.
    path("api/artifact-draft/run/", views.ArtifactDraftRunView.as_view(), name="artifact_draft_run"),
    path("api/visits/", views.VisitsView.as_view(), name="visits"),
    path("api/workflows/", views.WorkflowsView.as_view(), name="workflows"),
    path("api/drill/", views.DrillView.as_view(), name="drill"),
    path("api/saved/", views.SavedQueriesView.as_view(), name="saved"),
    path("api/saved/<int:pk>/delete/", views.SavedQueryDeleteView.as_view(), name="saved_delete"),
]

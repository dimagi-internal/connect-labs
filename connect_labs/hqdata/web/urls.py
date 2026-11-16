from django.urls import path

from connect_labs.hqdata.web import auth_views, views, workflow_views

app_name = "hq"

urlpatterns = [
    path("login/", auth_views.login_page, name="login"),
    path("login/initiate/", auth_views.login_initiate, name="login_initiate"),
    path("login/callback/", auth_views.login_callback, name="login_callback"),
    path("logout/", auth_views.logout_view, name="logout"),
    path("projects/new/", views.project_create, name="project_create"),
    path("projects/<int:project_id>/", views.project_detail, name="project_detail"),
    path("projects/<int:project_id>/act/", views.project_action, name="project_action"),
    path("projects/<int:project_id>/pipelines/", views.pipeline_list, name="pipeline_list"),
    path("projects/<int:project_id>/pipelines/<int:pipeline_id>/", views.pipeline_detail, name="pipeline_detail"),
    path("projects/<int:project_id>/workflows/", workflow_views.workflow_list, name="workflow_list"),
    path(
        "projects/<int:project_id>/workflows/<int:definition_id>/",
        workflow_views.workflow_page,
        name="workflow_page",
    ),
    path(
        "projects/<int:project_id>/workflows/<int:definition_id>/pipeline-data/",
        workflow_views.workflow_pipeline_data,
        name="workflow_pipeline_data",
    ),
    path("tokens/", views.tokens, name="tokens"),
    path("", views.home, name="home"),
]

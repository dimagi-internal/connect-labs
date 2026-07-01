"""URL patterns for the Flags app.

These patterns are mounted under a workflow-run path — see
connect_labs/workflow/urls.py for where they're included
(/labs/workflow/api/<int:workflow_run_id>/flags/).

Django's path() doesn't route on HTTP method alone, so we split create
(POST at the empty subpath) from list (GET at /list/). The
@require_http_methods decorators in views.py enforce the verb.
"""

from django.urls import path

from connect_labs.flags import views

app_name = "flags"

urlpatterns = [
    path("", views.create_flag_for_run, name="create_for_run"),
    path("list/", views.list_flags_for_run, name="list_for_run"),
]

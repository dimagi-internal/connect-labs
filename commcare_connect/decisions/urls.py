"""URL patterns for the Decisions app.

These patterns are mounted under a workflow-run path — see
commcare_connect/workflow/urls.py for where they're included
(/labs/workflow/api/<int:workflow_run_id>/decisions/).

Django's path() doesn't route on HTTP method alone, so we split create
(POST at the empty subpath) from list (GET at /list/). The
@require_http_methods decorators in views.py enforce the verb.
"""

from django.urls import path

from commcare_connect.decisions import views

app_name = "decisions"

urlpatterns = [
    path("", views.create_decision_for_run, name="create_for_run"),
    path("list/", views.list_decisions_for_run, name="list_for_run"),
]

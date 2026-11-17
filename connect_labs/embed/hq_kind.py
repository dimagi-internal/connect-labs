"""A CommCare project's workflow page, shown inside CommCare HQ to a person who has no Connect login.

HQ (an embed client with a ``commcare_host``) signs an assertion naming the person
(``cchq:<host>:<username>``), and labs draws one of the project's pages for them: the same sandboxed,
read-only frame as ``/commcare/``, with one data read. Differences from the Connect-shaped ``workflow`` kind:

* no Connect token is stored, asked for or used (``needs_connect_token = False``);
* the resource names a PROJECT as well as a page (``{"kind": "commcare_workflow", "id": <page>, "project_id": <n>}``),
  and the page is read in that project's scope, so another project's page id is not found;
* labs' own record of membership is re-checked on EVERY request (``still_allowed``), so a session ends as
  soon as that record is deleted or lapses (24 hours), not when the session would. It does not ask HQ:
  for a person HQ vouches for, "removed in HQ" reaches labs only as HQ ceasing to assert them, so the
  longest a removal can go unnoticed is the client's session length. Keep that short;
* the only route a session may reach is this page's one data read.

Who may be shown it is ``hqdata.access.may_use_project``, labs' own revocable record of membership.
Whether HQ's word can CREATE that record is the client's ``vouches_for_membership`` (see ``grant.py``).
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError

from connect_labs.embed.kinds import EmbedKind, register

#: The one route a session of this kind reads, beside its frame.
DATA_ROUTE = re.compile(r"^/embed/commcare/(?P<project>\d+)/(?P<page>\d+)/pipeline-data/$")


def data_path(project_id, definition_id) -> str:
    return f"/embed/commcare/{int(project_id)}/{int(definition_id)}/pipeline-data/"


def _int(value, what: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{what} must be a number.") from None
    if number <= 0:
        raise ValidationError(f"{what} must be a positive number.")
    return number


class HQWorkflowKind(EmbedKind):
    name = "commcare_workflow"
    needs_connect_token = False
    commcare_subjects = True

    def validate_entry(self, entry):
        _int(entry.get("id"), "A page's id")
        _int(entry.get("project_id"), "project_id")
        extra = set(entry) - {"kind", "id", "project_id"}
        if extra:
            raise ValidationError(f"An commcare_workflow resource names only id and project_id, not {sorted(extra)}.")

    def validate_for_client(self, client, entry):
        project = self.project_for(entry)
        if project is None:
            raise ValidationError(f"No CommCare project {entry.get('project_id')}.")
        if client.commcare_host and project.hq_host != client.commcare_host:
            raise ValidationError(f"Project {project.pk} is on {project.hq_host}, not {client.commcare_host}.")

    def scope(self, entry):
        # Only for the audit trail: the project's own scope id, which meets no opportunity or programme.
        from connect_labs.hqdata.scope import hq_scope_id

        return {"opportunity_id": hq_scope_id(_int(entry.get("project_id"), "project_id"))}

    def context(self, entry):
        # Nothing reads a labs context here: the project comes from the session's entry.
        return {}

    def project_for(self, entry):
        from connect_labs.hqdata.models import HQProject

        return HQProject.objects.filter(pk=_int(entry.get("project_id"), "project_id")).first()

    def check_access(self, user, access_token, entry):
        from connect_labs.hqdata.access import may_use_project
        from connect_labs.hqdata.workflows import ProjectWorkflowsUnavailable, load_page

        project = self.project_for(entry)
        if project is None or not may_use_project(user, project):
            return "This person cannot open that page."
        try:
            loaded = load_page(user, project, entry["id"])
        except ProjectWorkflowsUnavailable:
            return "Workflows are not available for that project."
        return None if loaded is not None else "This person cannot open that page."

    def still_allowed(self, session) -> bool:
        from connect_labs.hqdata.access import may_use_project

        project = self.project_for(session.entry)
        return project is not None and may_use_project(session.user, project)

    def render(self, request, session):
        from connect_labs.embed.views import render_hq_workflow

        return render_hq_workflow(request, session)

    def allowed(self, session, method, path):
        found = DATA_ROUTE.match(path)
        if method != "GET" or found is None:
            return False
        entry = session.entry or {}
        return found["project"] == str(entry.get("project_id")) and found["page"] == str(session.resource_id)

    def writes(self, method, path):
        # Nothing here writes, so every write from this frame is refused outright by the middleware --
        # except the frame trading its bootstrap token for its session (its own POST, which is the
        # credential; embed/views.py session_view).
        return method not in ("GET", "HEAD", "OPTIONS") and not (method == "POST" and path == "/embed/session/")


register(HQWorkflowKind())

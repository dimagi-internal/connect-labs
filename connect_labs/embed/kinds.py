"""What can be embedded: a registry of kinds.

A kind is anything labs can draw without chrome for one person. It supplies five
things and nothing else; the token endpoint, the session, the cookie, the framing
headers, the CSRF rule and the audit trail are the embed app's and a kind cannot
widen them:

* ``validate_entry(entry)`` -- is a client's resource entry well formed (admin save);
* ``scope(entry)`` -- the labs context the resource is read in;
* ``check_access(user, access_token, entry)`` -- at token time, as the person: may
  they open it? ``None`` if so, else a reason the embedder is told;
* ``render(request, session)`` -- the chromeless view drawn in the frame;
* ``allowed(session, method, path)`` -- the routes a session for this resource may
  reach. The frame's own address is always allowed and is not listed here.

A new embeddable thing is one subclass and one ``register`` call.
"""

from __future__ import annotations

import logging
import re

from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

SCOPE_KEYS = ("opportunity_id", "program_id")


class EmbedKind:
    name = ""

    def validate_entry(self, entry: dict) -> None:
        raise NotImplementedError

    def scope(self, entry: dict) -> dict:
        raise NotImplementedError

    def check_access(self, user, access_token: str, entry: dict) -> str | None:
        raise NotImplementedError

    def render(self, request, session):
        raise NotImplementedError

    def allowed(self, session, method: str, path: str) -> bool:
        raise NotImplementedError


_KINDS: dict[str, EmbedKind] = {}


def register(kind: EmbedKind) -> EmbedKind:
    _KINDS[kind.name] = kind
    return kind


def get_kind(name) -> EmbedKind | None:
    return _KINDS.get(name) if isinstance(name, str) else None


def _one_scope(entry: dict) -> dict:
    """Exactly one of opportunity_id / program_id, as an int."""
    present = {k: entry.get(k) for k in SCOPE_KEYS if entry.get(k) not in (None, "")}
    if len(present) != 1:
        raise ValidationError("A resource names exactly one of opportunity_id or program_id.")
    ((key, value),) = present.items()
    try:
        return {key: int(value)}
    except (TypeError, ValueError):
        raise ValidationError(f"{key} must be a number.") from None


# --- workflow (and page: a page is a workflow definition) --------------------------------

#: The runner's READ endpoints for one definition (workflow/urls.py), by method. Anything
#: that writes -- run state, worker results, completing a run, actions, render code,
#: pipeline sources, chat -- is absent, so an embed is read-only.
_DEF_GET = (
    "pipeline-data/",
    "pipeline-data/stream/",
    "pipeline-rows/",
    "pipeline-rows/stream/",
    "case-states/",
    "supply-data/",
    "workflow-data/",
    "semantic/",
    "indicator-definitions/",
    "runs/history/",
)
#: Reads that are POSTs because they carry a query body.
_DEF_POST = ("pipeline-query/", "supply-query/", "workflow-query/")
_RUN_GET = ("", "snapshot/", "snapshot/preview/")
#: Not tied to one definition; each reads as the person, so prod decides what comes back.
_GLOBAL_GET = ("api/workers/", "api/opportunity-flws/", "api/auth-status/")

_WORKFLOW_PREFIX = "/labs/workflow/"
_DEF_PATH = re.compile(r"^api/(?P<id>\d+)/(?P<rest>.*)$")
_RUN_PATH = re.compile(r"^api/run/(?P<id>\d+)/(?P<rest>.*)$")


class WorkflowKind(EmbedKind):
    name = "workflow"

    def validate_entry(self, entry):
        try:
            int(entry.get("id"))
        except (TypeError, ValueError):
            raise ValidationError("A workflow resource's id is its definition id.") from None
        _one_scope(entry)

    def scope(self, entry):
        return _one_scope(entry)

    def check_access(self, user, access_token, entry):
        from connect_labs.labs.access.scopes import Caller, may_use
        from connect_labs.workflow.data_access import WorkflowDataAccess

        # Labs' own answer first: a labs-only (synthetic) opportunity's records are read
        # with no permission check at all (labs/synthetic/local_records_backend.py), so
        # reading the definition below proves nothing for one. Unknowable is a refusal.
        if may_use(Caller(user=user, access_token=access_token), **self.scope(entry)):
            return "This person cannot open that workflow."
        access = None
        try:
            access = WorkflowDataAccess(access_token=access_token, user=user, **self.scope(entry))
            definition = access.get_definition(int(entry["id"]))
        except Exception:
            logger.info("embed: definition %s could not be read for %s", entry.get("id"), user.pk, exc_info=True)
            definition = None
        finally:
            if access is not None:
                access.close()
        if definition is None:
            return "This person cannot open that workflow."
        return None

    def render(self, request, session):
        from connect_labs.embed.views import EmbedWorkflowView

        return EmbedWorkflowView.as_view()(request, definition_id=int(session.resource_id))

    def allowed(self, session, method, path):
        if not path.startswith(_WORKFLOW_PREFIX):
            return False
        rest = path[len(_WORKFLOW_PREFIX) :]
        if method in ("GET", "HEAD") and rest in _GLOBAL_GET:
            return True
        match = _DEF_PATH.match(rest)
        if match and match["id"] == str(session.resource_id):
            return (method in ("GET", "HEAD") and match["rest"] in _DEF_GET) or (
                method == "POST" and match["rest"] in _DEF_POST
            )
        match = _RUN_PATH.match(rest)
        if match and method in ("GET", "HEAD"):
            # Only the run this frame was drawn with (views.EmbedWorkflowView records it).
            run_id = (session.state or {}).get("run_id")
            return run_id is not None and match["id"] == str(run_id) and match["rest"] in _RUN_GET
        return False


register(WorkflowKind())

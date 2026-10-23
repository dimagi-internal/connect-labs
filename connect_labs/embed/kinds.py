"""What can be embedded: a registry of kinds.

A kind is anything labs can draw without chrome for one person. It supplies seven
things and nothing else; the token endpoint, the session, the cookie, the framing
headers, the CSRF rule and the audit trail are the embed app's and a kind cannot
widen them:

* ``validate_entry(entry)`` -- is a client's resource entry well formed (admin save);
* ``scope(entry)`` -- the labs context the resource is read in;
* ``check_access(user, access_token, entry)`` -- at token time, as the person: may
  they open it? ``None`` if so, else a reason the embedder is told;
* ``reads_only_generated(user, entry)`` -- whether everything the resource reads is
  generated data (``labs/synthetic/provenance.py``), for a client limited to it. Fails
  closed: ``False`` when it cannot tell;
* ``render(request, session)`` -- the chromeless view drawn in the frame;
* ``allowed(session, method, path)`` -- the routes a session for this resource may
  reach. The frame's own address is always allowed and is not listed here;
* ``writes(method, path)`` -- whether a request is a write this kind's frame might make,
  which the middleware refuses outright (an embed is read-only) rather than letting it
  fall through to whatever else the request carries.

A new embeddable thing is one subclass and one ``register`` call.
"""

from __future__ import annotations

import logging

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

    def reads_only_generated(self, user, entry: dict) -> bool:
        return False

    def render(self, request, session):
        raise NotImplementedError

    def allowed(self, session, method: str, path: str) -> bool:
        raise NotImplementedError

    def writes(self, method: str, path: str) -> bool:
        return False


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

#: The reads a drawn workflow makes all go through the sandbox host's route table
#: (components/workflow/sandbox/routes.ts, mirrored in workflow/sandbox_routes.py and kept
#: equal by CI), so the embed follows that table rather than a list of its own that drifts:
#: a session may reach a route the table gates "read", never "own" or "effect" (an embed is
#: read-only). Every id such a route names is bound to what the frame was drawn for (its
#: session's ``state``, recorded by views.frame_reach) -- or refused, if it is a kind of id
#: this code does not know yet.
#:
#: Route groups bound by name: ``def`` (this definition), ``rel`` (a definition it declares
#: it reads), ``run`` (its runs), ``opp`` / ``prog`` (the opportunities and programmes it
#: covers). ``pipe`` and ``job`` are read as the person, who may read them anyway. A few
#: routes carry an unnamed id; those that name a run or a workflow are bound here too.
_UNNAMED = {
    r"^/labs/workflow/api/run/(\d+)/$": "run",
    r"^/labs/workflow/api/run/(\d+)/snapshot/(preview/)?$": "run",
    r"^/audit/api/workflow/(\d+)/sessions/$": "rel",
}
_AS_THE_PERSON = frozenset({"pipe", "job"})


def _bound(session, name: str, value: str) -> bool:
    state = session.state or {}

    def held(key):
        return value in {str(v) for v in state.get(key) or []}

    if name == "def":
        return value == str(session.resource_id)
    if name == "rel":
        return value == str(session.resource_id) or held("readable")
    if name == "run":
        return held("runs")
    if name == "opp":
        return held("opps")
    if name == "prog":
        return held("programs")
    return name in _AS_THE_PERSON


def sandbox_route(method: str, path: str):
    """``(route, match)`` for the sandbox route that answers ``method path``, else ``None``."""
    from connect_labs.workflow.sandbox_routes import FETCH_DENIED, compiled

    if any(path.startswith(denied) for denied in FETCH_DENIED):
        return None
    for route, pattern in compiled():
        match = pattern.match(path)
        if match and method in route.methods:
            return route, match
    return None


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

    def reads_only_generated(self, user, entry):
        """Every opportunity the workflow reads is generated: its scope, and the definition's
        own opportunities (the MCP's "no user visit data" resolver). A workflow that reads
        other workflows' runs (``workflow_sources``) is refused: those runs are computed
        from visits this check does not see."""
        from connect_labs.labs.synthetic.provenance import all_generated
        from connect_labs.mcp.visit_access import workflow_reads

        try:
            reads = workflow_reads(user, int(entry["id"]), **self.scope(entry))
            if not reads or not all_generated(reads):
                return False
            from connect_labs.labs.connect_tokens import get_valid_access_token
            from connect_labs.workflow.data_access import WorkflowDataAccess

            access = WorkflowDataAccess(access_token=get_valid_access_token(user), user=user, **self.scope(entry))
            try:
                definition = access.get_definition(int(entry["id"]))
            finally:
                access.close()
        except Exception:  # noqa: BLE001 -- cannot tell is not generated
            logger.info("embed: could not establish what workflow %s reads", entry.get("id"), exc_info=True)
            return False
        return definition is not None and not (definition.data or {}).get("workflow_sources")

    def render(self, request, session):
        from connect_labs.embed.views import EmbedWorkflowView

        return EmbedWorkflowView.as_view()(request, definition_id=int(session.resource_id))

    def allowed(self, session, method, path):
        found = sandbox_route(method, path)
        if found is None:
            return False
        route, match = found
        if route.gate != "read":
            return False
        if not all(_bound(session, name, value) for name, value in match.groupdict().items() if value is not None):
            return False
        unnamed = _UNNAMED.get(route.path)
        return unnamed is None or _bound(session, unnamed, match.group(1))

    def writes(self, method, path):
        found = sandbox_route(method, path)
        return found is not None and found[0].gate != "read"


register(WorkflowKind())

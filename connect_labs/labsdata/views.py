"""The Data page at /labs/data/ and its JSON endpoints.

The endpoints are thin: they build a ``Caller`` from the request and call the
same ``service`` functions the MCP tools call, so the page, the in-page agent and
an MCP client can never disagree about what a person may read.
"""

from __future__ import annotations

import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views import View
from django.views.generic import TemplateView

from connect_labs.labs.access.scopes import Caller

from . import display, scope, service
from .engine import QueryError
from .scope import ScopeError
from .validator import DEFAULT_MAX_ROWS, HARD_MAX_ROWS

EXAMPLE_SQL = """SELECT llo, opportunity_name, COUNT(*) AS visits, COUNT(DISTINCT entity_id) AS cases
FROM visits
WHERE status <> 'rejected'
GROUP BY llo, opportunity_name
ORDER BY visits DESC"""


def _caller(request) -> Caller:
    return Caller(user=request.user, request=request)


def _ids(raw: str | None) -> list[int]:
    return [int(x) for x in (raw or "").split(",") if x.strip().isdigit()]


def _selected(request) -> list[int]:
    return _ids(request.GET.get("opps"))


def _workflow_param(raw: str | None) -> dict | None:
    """``?workflow=<id>:opp:<opportunity_id>`` or ``<id>:program:<program_id>`` -- the scope it is read in."""
    parts = (raw or "").split(":")
    if len(parts) != 3 or not parts[0].isdigit() or not parts[2].isdigit() or parts[1] not in {"opp", "program"}:
        return None
    key = "opportunity_id" if parts[1] == "opp" else "program_id"
    return {"id": int(parts[0]), key: int(parts[2])}


def initial_selection(request) -> dict:
    """What the page opens on: the URL's selection, else the labs context (an opportunity
    or a program), else nothing. Only ids -- every one is checked when it is read."""
    workflow = _workflow_param(request.GET.get("workflow"))
    opps, programs = _selected(request), _ids(request.GET.get("programs"))
    if workflow or opps or programs:
        return {
            "workflow": workflow,
            "opportunity_ids": [] if workflow else opps,
            "program_ids": [] if workflow else programs,
        }
    context = getattr(request, "labs_context", None) or {}
    if context.get("opportunity_id"):
        return {"workflow": None, "opportunity_ids": [int(context["opportunity_id"])], "program_ids": []}
    if context.get("program_id"):
        return {"workflow": None, "opportunity_ids": [], "program_ids": [int(context["program_id"])]}
    return {"workflow": None, "opportunity_ids": [], "program_ids": []}


def _max_rows(request) -> int:
    """The row cap a link asks for (``service.open_on_page_url``), within the hard cap."""
    try:
        return max(1, min(int(request.GET.get("max_rows") or DEFAULT_MAX_ROWS), HARD_MAX_ROWS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_ROWS


def _display(raw) -> dict | None:
    """A ``display`` from a link (JSON), checked by ``display.validate``; None when it is
    missing or breaks a rule (a link is not where the person learns of a bad display)."""
    if not raw:
        return None
    try:
        return display.validate(json.loads(raw[:20000]))
    except (ValueError, display.DisplayError):
        return None


class DataView(LoginRequiredMixin, TemplateView):
    template_name = "labsdata/index.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        caller = _caller(self.request)
        opps = sorted(scope.directory(caller).values(), key=lambda o: (o.llo.lower(), o.name.lower()))
        initial = initial_selection(self.request)
        selected = initial["opportunity_ids"]
        ctx.update(
            {
                "labsdata_options": {
                    "opportunities": [
                        {"id": o.id, "name": o.name, "llo": o.llo, "program_id": o.program_id} for o in opps
                    ],
                    "programs": scope.programs(caller),
                    "initial": initial,
                },
                # A link from the agent's result View opens the page on its query; it is
                # put in the editor, never run on load.
                "example_sql": (self.request.GET.get("sql") or "")[:20000] or EXAMPLE_SQL,
                "default_max_rows": _max_rows(self.request),
                "hard_max_rows": HARD_MAX_ROWS,
                # A card's "Download CSV" opens the page on its query to run it again here,
                # as the person, and offer the download: `run` runs it once the page is up
                # (a read, in the person's own session, shown only to them); `download`
                # highlights the Download CSV button. Nothing downloads without a click.
                "open_intent": {
                    "run": self.request.GET.get("run") == "1" and bool(self.request.GET.get("sql")),
                    "download": self.request.GET.get("download") == "1",
                    # A card's Save: the Save button is highlighted; nothing is saved until
                    # the person names it and clicks.
                    "save": self.request.GET.get("save") == "1" and bool(self.request.GET.get("sql")),
                    # The card's labels and formats, checked as the agent's were.
                    "display": _display(self.request.GET.get("display")),
                },
            }
        )
        from connect_labs.labs import canopy_panel

        # Ids, never rows: the agent reads data itself through the data_* tools, as
        # the visitor, and those tools decide whether it may see production rows
        # (service.agent_sees_sensitive_rows). Until labs clears the agent for sensitive data
        # it gets structure and hands SQL back to this page, so the page shows it no
        # visit data by itself.
        ctx["canopy_panel"] = canopy_panel.panel(
            self.request,
            reads=canopy_panel.NO_VISIT_DATA,
            # The agent for data work (labsdata), read-only on sensitive-data runners; else the default agent.
            available=canopy_panel.data_page_agent(),
            resource="labs-data://opportunities/" + ",".join(map(str, selected)) if selected else "labs-data://",
            backing_tool="data_describe",
            visible_ids=selected,
            filters={"opportunity_ids": selected},
            path=self.request.path,
        )
        return ctx


class _Invalid(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class _JsonView(LoginRequiredMixin, View):
    def handle(self, request, body: dict) -> dict:  # pragma: no cover - abstract
        raise NotImplementedError

    def post(self, request, **kwargs):
        try:
            body = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "invalid_json", "message": "Body must be JSON."}, status=400)
        try:
            return JsonResponse(self.handle(request, body))
        except ScopeError as e:
            status = 503 if e.code == "scopes_unavailable" else 403 if e.code == "not_held" else 400
            return JsonResponse({"error": e.code, "message": e.message}, status=status)
        except QueryError as e:
            return JsonResponse(e.as_dict(), status=400)
        except _Invalid as e:
            return JsonResponse({"error": e.code, "message": e.message}, status=e.status)


def _selection(body: dict) -> dict:
    return {"program_ids": body.get("program_ids") or None, "workflow": body.get("workflow") or None}


class DescribeView(_JsonView):
    def handle(self, request, body):
        return service.describe(
            _caller(request),
            body.get("opportunity_ids"),
            load_missing=bool(body.get("load_missing")),
            # The page shows no registries and no form-field index; skip both. The
            # agent's data_describe keeps them -- they are how it finds fields and
            # governed measures.
            include_registries=False,
            include_fields=False,
            **_selection(body),
        )


class QueryView(_JsonView):
    def handle(self, request, body):
        return service.query(
            _caller(request),
            body.get("opportunity_ids"),
            body.get("sql") or "",
            int(body.get("max_rows") or 500),
            **_selection(body),
        )


class DrillView(_JsonView):
    """The rows behind one cell of a result: their SQL, and how many there are."""

    def handle(self, request, body):
        return service.drill_into(
            _caller(request),
            body.get("opportunity_ids"),
            body.get("sql") or "",
            body.get("row") or [],
            int(body.get("column") or 0),
            **_selection(body),
        )


class WorkflowsView(_JsonView):
    """Workflows to choose from: those readable, as the person, in the selection's scopes."""

    def handle(self, request, body):
        return service.workflows(
            _caller(request), opportunity_ids=body.get("opportunity_ids"), program_ids=body.get("program_ids")
        )


class VisitsView(_JsonView):
    def handle(self, request, body):
        return service.visits(_caller(request), body.get("opportunity_ids"), body.get("visit_ids") or [])


# --- saved queries (models.SavedQuery) ---------------------------------------------------


class SavedQueriesView(_JsonView):
    """GET: the person's saved queries, the latest version of each name. POST: save one.

    A save holds the query, never rows. It is checked as a run would be -- the person must
    hold every opportunity now (``scope.resolve``) and the SQL must pass the same checks
    over zero rows (``service.validate``) -- and its ``display`` as the agent's was. It is
    the person's own write, in their own session, behind Django's CSRF check."""

    def get(self, request):
        from .models import SavedQuery

        latest = {}
        for q in SavedQuery.objects.filter(owner=request.user).order_by("name", "-version"):
            latest.setdefault(q.name, q)
        rows = sorted(latest.values(), key=lambda q: q.created_at, reverse=True)
        return JsonResponse({"saved": [q.as_dict() for q in rows]})

    def handle(self, request, body):
        from django.db import IntegrityError, transaction

        from connect_labs.audit_trail.models import Action
        from connect_labs.audit_trail.service import record

        from .models import SavedQuery

        name = display.clean_text(body.get("name"), 200)
        sql = str(body.get("sql") or "")
        if not name:
            raise _Invalid("name_required", "Give the query a name.")
        if not sql.strip() or len(sql) > 20000:
            raise _Invalid("sql_required", "There is no query to save (or it is longer than 20,000 characters).")
        # The selection as a run resolves it (programs and opportunities, or one workflow
        # with its pipelines), refusing anything the person does not hold now; the SQL
        # checked as it would run over that selection, over zero rows.
        selection = service.select(_caller(request), body.get("opportunity_ids"), **_selection(body))
        opps = selection.opportunities
        service.validate_selected(sql, selection)
        try:
            shown = display.validate(body.get("display"))
        except display.DisplayError as e:
            raise _Invalid("bad_display", str(e)) from e
        try:
            max_rows = max(1, min(int(body.get("max_rows") or DEFAULT_MAX_ROWS), HARD_MAX_ROWS))
        except (TypeError, ValueError):
            max_rows = DEFAULT_MAX_ROWS
        with transaction.atomic():
            previous = (
                SavedQuery.objects.select_for_update()
                .filter(owner=request.user, name=name)
                .order_by("-version")
                .first()
            )
            try:
                saved = SavedQuery.objects.create(
                    owner=request.user,
                    name=name,
                    sql=sql,
                    opportunity_ids=[] if selection.workflow else _ints(body.get("opportunity_ids")),
                    program_ids=[] if selection.workflow else _ints(body.get("program_ids")),
                    workflow=_workflow_of(body) if selection.workflow else {},
                    max_rows=max_rows,
                    display=shown or {},
                    version=(previous.version + 1) if previous else 1,
                    parent=previous,
                )
            except IntegrityError as e:  # a save of the same name in the same instant
                raise _Invalid("conflict", "That name was just saved; save again.") from e
        for o in opps:
            record(
                Action.CREATE,
                resource_type="labsdata_saved_query",
                resource_id=saved.pk,
                opportunity_id=o.id,
                metadata={"version": saved.version, "sql_length": len(sql)},
                user=request.user,
            )
        return {"saved": saved.as_dict()}


def _ints(values) -> list[int]:
    try:
        return sorted({int(v) for v in values or []})
    except (TypeError, ValueError):
        return []


def _workflow_of(body: dict) -> dict:
    """The workflow the page chose (``{id, opportunity_id | program_id}``), once
    ``service.select`` has resolved it as the person."""
    w = body.get("workflow") or {}
    out = {"id": int(w["id"])}
    for key in ("opportunity_id", "program_id"):
        if w.get(key):
            out[key] = int(w[key])
    return out


class SavedQueryDeleteView(_JsonView):
    """POST: delete one of the person's saved queries, every version of its name."""

    def handle(self, request, body):
        from connect_labs.audit_trail.models import Action
        from connect_labs.audit_trail.service import record

        from .models import SavedQuery

        saved = SavedQuery.objects.filter(owner=request.user, pk=self.kwargs.get("pk")).first()
        if saved is None:
            raise _Invalid("not_found", "There is no such saved query of yours.", status=404)
        deleted, _ = SavedQuery.objects.filter(owner=request.user, name=saved.name).delete()
        record(Action.DELETE, resource_type="labsdata_saved_query", resource_id=saved.pk, user=request.user)
        return {"deleted": deleted}

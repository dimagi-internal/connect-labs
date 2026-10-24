"""The Data page at /labs/data/ and its JSON endpoints.

The endpoints are thin: they build a ``Caller`` from the request, get the data space the
call reads from ``spaces.from_request`` -- the saved space it names, or one generated from
its selection -- and call the same ``service`` functions the MCP tools call, so the page,
the in-page agent and an MCP client can never disagree about what a person may read.
"""

from __future__ import annotations

import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponse, JsonResponse
from django.views import View
from django.views.generic import TemplateView

from connect_labs.labs.access.scopes import Caller

from . import display, page_holds, scope, service, spaces
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


def _is_labs_admin(user) -> bool:
    """Dimagi staff, as Labs Admin (``canopy_settings.can_change`` uses the same test)."""
    from connect_labs.utils.dimagi_user import is_dimagi_staff

    return bool(user and user.is_authenticated and is_dimagi_staff(user))


class DataView(LoginRequiredMixin, TemplateView):
    template_name = "labsdata/index.html"

    def get(self, request, *args, **kwargs):
        try:
            return super().get(request, *args, **kwargs)
        except ScopeError as e:  # what the person holds cannot be established right now
            return HttpResponse(e.message, status=503, content_type="text/plain")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        caller = _caller(self.request)
        # On /labs/data/space/<id>/ the page is the space: opened through the one gate, and
        # a space the person may not open is the same 404 as one that does not exist.
        opened = None
        if self.kwargs.get("space_id") is not None:
            try:
                opened = spaces.open(caller, self.kwargs["space_id"], allow_changed=True)
            except spaces.SpaceNotFound as e:
                raise Http404(e.message) from e
        opps = sorted(scope.directory(caller).values(), key=lambda o: (o.llo.lower(), o.name.lower()))
        initial = initial_selection(self.request)
        if opened is not None:
            initial = {"workflow": None, "opportunity_ids": [], "program_ids": []}
        selected = opened.selection.ids if opened is not None else initial["opportunity_ids"]
        ctx.update(
            {
                "labsdata_options": {
                    "opportunities": [
                        {"id": o.id, "name": o.name, "llo": o.llo, "program_id": o.program_id} for o in opps
                    ],
                    "programs": scope.programs(caller),
                    "initial": initial,
                    # The space this page is (ids and names only; its queries load by API),
                    # with its audience, its managers and whether this person is one.
                    "space": opened.as_dict() if opened is not None else None,
                    # Dimagi staff may make someone a manager of a space (spaces.admin_add_manager).
                    "can_admin_assign": _is_labs_admin(self.request.user),
                },
                "space": opened.space if opened is not None else None,
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
        if opened is not None:
            # A space's page names the space: the agent passes its id to every data_* call,
            # and those calls read the space's sources through spaces.open and nothing else.
            resource = f"labs-data-space://{opened.id}"
            filters = {"space_id": opened.id}
            holds = page_holds.for_space(opened)
        else:
            resource = "labs-data://opportunities/" + ",".join(map(str, selected)) if selected else "labs-data://"
            filters = {"opportunity_ids": selected}
            # What the page opens on; the page reports each change the person makes (PageHoldsView).
            holds = page_holds.for_selection(initial, {o.id: o for o in opps})
        ctx["canopy_panel"] = canopy_panel.panel(
            self.request,
            reads=canopy_panel.NO_VISIT_DATA,
            # The agent for data work (labsdata), read-only on sensitive-data runners; else the default agent.
            available=canopy_panel.data_page_agent(),
            # The agent's data reads are held to what this page shows (labsdata/page_holds.py).
            holds=holds,
            resource=resource,
            backing_tool="data_describe",
            visible_ids=selected,
            filters=filters,
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

    def read(self, request) -> dict:  # pragma: no cover - for views with a GET
        raise _Invalid("method_not_allowed", "GET is not supported here.", status=405)

    def get(self, request, **kwargs):
        return self._answer(lambda: self.read(request))

    def post(self, request, **kwargs):
        try:
            body = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "invalid_json", "message": "Body must be JSON."}, status=400)
        if not isinstance(body, dict):
            return JsonResponse({"error": "invalid_json", "message": "Body must be a JSON object."}, status=400)
        return self._answer(lambda: self.handle(request, body))

    def _answer(self, fn):
        try:
            return JsonResponse(fn())
        except ScopeError as e:
            status = 503 if e.code == "scopes_unavailable" else 403 if e.code == "not_held" else 400
            return JsonResponse({"error": e.code, "message": e.message}, status=status)
        except spaces.SpaceNotFound as e:
            return JsonResponse({"error": e.code, "message": e.message}, status=404)
        except spaces.OutsideSpace as e:
            return JsonResponse({"error": e.code, "message": e.message}, status=400)
        except spaces.NotManager as e:
            return JsonResponse({"error": e.code, "message": e.message}, status=403)
        except spaces.LastManager as e:
            return JsonResponse({"error": e.code, "message": e.message}, status=409)
        except (spaces.NotEligible, spaces.BadAudience) as e:
            return JsonResponse({"error": e.code, "message": e.message}, status=400)
        except spaces.SpaceChanged as e:
            return JsonResponse(
                {"error": e.code, "message": e.message, "added": e.added, "removed": e.removed}, status=409
            )
        except QueryError as e:
            return JsonResponse(e.as_dict(), status=400)
        except _Invalid as e:
            return JsonResponse({"error": e.code, "message": e.message}, status=e.status)


def _space(request, body: dict) -> spaces.OpenSpace:
    """The space a call reads: the saved space the body names (``space_id``), opened as the
    person -- with any selection beside it the space's own, else ``outside_space`` -- or one
    generated from the body's selection."""
    return spaces.from_request(
        _caller(request),
        space_id=body.get("space_id"),
        opportunity_ids=body.get("opportunity_ids"),
        program_ids=body.get("program_ids") or None,
        workflow=body.get("workflow") or None,
    )


class DescribeView(_JsonView):
    def handle(self, request, body):
        space = _space(request, body)
        out = service.describe(
            _caller(request),
            space,
            load_missing=bool(body.get("load_missing")),
            # The page shows no registries and no form-field index; skip both. The
            # agent's data_describe keeps them -- they are how it finds fields and
            # governed measures.
            include_registries=False,
            include_fields=False,
        )
        if space.saved:
            out["space"] = {"id": space.id, "name": space.space.name}
        return out


class PageHoldsView(_JsonView):
    """The person changed the plain Data page's selection: hold the page's agent to it.

    In the person's own session (CSRF), for their own Data page token only, with every
    source resolved as them (``spaces.generate``, which refuses anything not held). The
    agent has no tool or page action that reaches this (``page_holds``)."""

    def handle(self, request, body):
        page_token = body.get("page_token") or ""
        workflow = body.get("workflow") or None
        opportunity_ids = body.get("opportunity_ids") or []
        program_ids = body.get("program_ids") or []
        ids, chosen_workflow = [], None
        if workflow or opportunity_ids or program_ids:
            generated = spaces.generate(_caller(request), opportunity_ids, program_ids=program_ids, workflow=workflow)
            ids, chosen_workflow = generated.selection.ids, generated.requested.get("workflow") or None
        # Nothing chosen: the agent reads nothing until the person chooses.
        try:
            return page_holds.update_from_page(
                request.user, page_token, body.get("page_holds") or "", ids, chosen_workflow
            )
        except ValueError as e:
            raise _Invalid("not_this_page", "This is not your Data page's panel.", status=403) from e


class QueryView(_JsonView):
    def handle(self, request, body):
        return service.query(
            _caller(request), _space(request, body), body.get("sql") or "", int(body.get("max_rows") or 500)
        )


class DrillView(_JsonView):
    """The rows behind one cell of a result: their SQL, and how many there are."""

    def handle(self, request, body):
        return service.drill_into(
            _caller(request),
            _space(request, body),
            body.get("sql") or "",
            body.get("row") or [],
            int(body.get("column") or 0),
        )


class WorkflowsView(_JsonView):
    """Workflows to choose from: those readable, as the person, in the selection's scopes."""

    def handle(self, request, body):
        # The programs and opportunities chosen, as a space generated for this call (a
        # workflow is what is being chosen, so none is passed).
        caller = _caller(request)
        space = spaces.generate(caller, body.get("opportunity_ids"), program_ids=body.get("program_ids") or None)
        return service.workflows(caller, space)


class VisitsView(_JsonView):
    def handle(self, request, body):
        return service.visits(_caller(request), _space(request, body), body.get("visit_ids") or [])


# --- saved queries (models.SavedQuery) ---------------------------------------------------


class SavedQueriesView(_JsonView):
    """GET: the person's saved queries, the latest version of each name. POST: save one.

    A save holds the query, never rows: its SQL and the selection it reads, which opening
    it generates a space from again, as whoever opens it. It is checked as a run would be --
    over a space generated now (refusing anything the person does not hold), the SQL through
    the same checks over zero rows -- and its ``display`` as the agent's was. It is the
    person's own write, in their own session, behind Django's CSRF check."""

    def read(self, request):
        from .models import SavedQuery

        # Personal queries only ("My queries"): a space's queries are the space's.
        return {"saved": _latest(SavedQuery.objects.filter(owner=request.user, space__isnull=True))}

    def handle(self, request, body):
        from django.db import IntegrityError, transaction

        from connect_labs.audit_trail.models import Action
        from connect_labs.audit_trail.service import record

        from .models import SavedQuery

        name, sql, shown, max_rows = _query_fields(body)
        # The space a run would read (programs and opportunities, or one workflow with its
        # pipelines), generated as the person now; the SQL checked over it, over zero rows.
        # A personal query keeps the selection as asked, not the space's freeze.
        space = spaces.generate(
            _caller(request),
            body.get("opportunity_ids"),
            program_ids=body.get("program_ids") or None,
            workflow=body.get("workflow") or None,
        )
        opps = space.selection.opportunities
        service.validate_selected(sql, space)
        with transaction.atomic():
            previous = (
                SavedQuery.objects.select_for_update()
                .filter(owner=request.user, name=name, space__isnull=True)
                .order_by("-version")
                .first()
            )
            try:
                saved = SavedQuery.objects.create(
                    owner=request.user,
                    name=name,
                    sql=sql,
                    opportunity_ids=space.requested["opportunity_ids"],
                    program_ids=space.requested["program_ids"],
                    workflow=space.requested["workflow"],
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


def _query_fields(body: dict) -> tuple[str, str, dict | None, int]:
    """A save's name, SQL, display and row cap, each checked (the selection is the caller's)."""
    name = display.clean_text(body.get("name"), 200)
    sql = str(body.get("sql") or "")
    if not name:
        raise _Invalid("name_required", "Give the query a name.")
    if not sql.strip() or len(sql) > 20000:
        raise _Invalid("sql_required", "There is no query to save (or it is longer than 20,000 characters).")
    try:
        shown = display.validate(body.get("display"))
    except display.DisplayError as e:
        raise _Invalid("bad_display", str(e)) from e
    try:
        max_rows = max(1, min(int(body.get("max_rows") or DEFAULT_MAX_ROWS), HARD_MAX_ROWS))
    except (TypeError, ValueError):
        max_rows = DEFAULT_MAX_ROWS
    return name, sql, shown, max_rows


def _latest(queryset, *, versions: bool = False) -> list[dict]:
    """The latest version of each name, newest first; with ``versions``, each carries the
    list of its versions (id, version, saved by, when)."""
    by_name: dict[str, list] = {}
    for q in queryset.select_related("owner").order_by("name", "-version"):
        by_name.setdefault(q.name, []).append(q)
    rows = sorted(by_name.values(), key=lambda qs: qs[0].created_at, reverse=True)
    out = []
    for qs in rows:
        item = qs[0].as_dict()
        if versions:
            item["versions"] = [
                {
                    "id": q.pk,
                    "version": q.version,
                    "sql": q.sql,
                    "max_rows": q.max_rows,
                    "display": q.display or None,
                    "saved_by": getattr(q.owner, "username", ""),
                    "created_at": q.created_at.isoformat(),
                }
                for q in qs
            ]
        out.append(item)
    return out


class SavedQueryDeleteView(_JsonView):
    """POST: delete one of the person's saved queries, every version of its name."""

    def handle(self, request, body):
        from connect_labs.audit_trail.models import Action
        from connect_labs.audit_trail.service import record

        from .models import SavedQuery

        saved = SavedQuery.objects.filter(owner=request.user, space__isnull=True, pk=self.kwargs.get("pk")).first()
        if saved is None:
            raise _Invalid("not_found", "There is no such saved query of yours.", status=404)
        deleted, _ = SavedQuery.objects.filter(owner=request.user, name=saved.name, space__isnull=True).delete()
        record(Action.DELETE, resource_type="labsdata_saved_query", resource_id=saved.pk, user=request.user)
        return {"deleted": deleted}


# --- data spaces (models.DataSpace; every read through spaces.open) ----------------------


def _audit_space(action: str, opened_or_ids, resource_type: str, resource_id, user, metadata: dict) -> None:
    """One audit event per opportunity the space reads, as saves are recorded."""
    from connect_labs.audit_trail.service import record

    ids = opened_or_ids if isinstance(opened_or_ids, list) else list(opened_or_ids.space.opportunity_ids)
    for oid in ids:
        record(
            action,
            resource_type=resource_type,
            resource_id=resource_id,
            opportunity_id=int(oid),
            metadata=metadata,
            user=user,
        )


def _space_name(body: dict) -> tuple[str, str]:
    name = display.clean_text(body.get("name"), 200)
    if not name:
        raise _Invalid("name_required", "Give the space a name.")
    return name, display.clean_text(body.get("description"), 2000) or ""


def _source_counts(space) -> dict:
    return {
        "opportunities": len(space.opportunity_ids),
        "programs": len(space.program_ids),
        "workflow": bool(space.workflow),
    }


class SpacesView(_JsonView):
    """GET: the spaces the person may open now, grouped by program or opportunity.
    POST: save a space from a selection (``{name, description, opportunity_ids, program_ids}``
    or ``{name, workflow}``): the space that selection generates, kept -- each source
    resolved as the person and frozen."""

    def read(self, request):
        return {"groups": spaces.openable(_caller(request))}

    def handle(self, request, body):
        from connect_labs.audit_trail.models import Action

        name, description = _space_name(body)
        caller = _caller(request)
        generated = spaces.generate(
            caller,
            body.get("opportunity_ids"),
            program_ids=body.get("program_ids") or None,
            workflow=body.get("workflow") or None,
        )
        space = spaces.save(caller, generated, name, description)
        opened = spaces.open(caller, space.pk, allow_changed=True)
        _audit_space(
            Action.CREATE, opened, "labsdata_space", space.pk, request.user, {"sources": _source_counts(space)}
        )
        return {"space": opened.as_dict()}


class SpaceView(_JsonView):
    """GET: one space -- its sources (and any opportunities its programs have gained that
    the person holds), as the person may open it now."""

    def read(self, request):
        return {"space": spaces.open(_caller(request), self.kwargs["space_id"], allow_changed=True).as_dict()}


class SpaceSourcesView(_JsonView):
    """POST: change a space's sources -- the one dangerous write. A manager, in their own
    session only (no MCP tool, no page action), behind CSRF and an explicit ``confirm: true``
    the page asks for after warning that anyone who does not hold a new source loses the
    space, and that who that is cannot be known here. Audited with the sources before and after."""

    def handle(self, request, body):
        from connect_labs.audit_trail.models import Action

        if body.get("confirm") is not True:
            raise _Invalid(
                "confirm_required",
                "Changing a space's sources locks out everyone who does not hold every new source. Confirm it.",
            )
        caller = _caller(request)
        opened = spaces.open(caller, self.kwargs["space_id"], allow_changed=True)
        spaces.require_manager(opened)  # before any work; update_sources checks it again
        generated = spaces.generate(
            caller,
            body.get("opportunity_ids"),
            program_ids=body.get("program_ids") or None,
            workflow=body.get("workflow") or None,
        )
        before, after = spaces.update_sources(opened, generated)
        space = opened.space
        metadata = {
            "field": "sources",
            "sources": _source_counts(space),
            "before_opportunities": len(before["opportunity_ids"]),
            "before": before,
            "after": after,
        }
        touched = sorted({int(i) for i in before["opportunity_ids"]} | {int(i) for i in space.opportunity_ids})
        _audit_space(Action.UPDATE, touched, "labsdata_space", opened.id, request.user, metadata)
        return {"space": spaces.open(caller, opened.id, allow_changed=True).as_dict()}


class SpaceArchiveView(_JsonView):
    """POST: archive a space (its managers only). It stops being listed or opened; its
    saved queries stay with it."""

    def handle(self, request, body):
        from connect_labs.audit_trail.models import Action

        opened = spaces.open(_caller(request), self.kwargs["space_id"], allow_changed=True)
        spaces.archive(opened)
        _audit_space(Action.DELETE, opened, "labsdata_space", opened.id, request.user, {"archived": True})
        return {"archived": opened.id}


class SpaceItemsView(_JsonView):
    """GET: the space's saved queries, the latest of each name with its versions.
    POST: save a query into the space (``{name, sql, max_rows, display}``): checked as a run
    over the space would be, and versioned per (space, name) -- any of the space's people may
    add a version; ``owner`` is who saved it."""

    def read(self, request):
        opened = spaces.open(_caller(request), self.kwargs["space_id"], allow_changed=True)
        items = _latest(spaces.queries(opened), versions=True)
        by_id = {q.pk: q for q in spaces.queries(opened).filter(pk__in=[i["id"] for i in items])}
        for item in items:
            # Managers delete any item; a member, only one they saved (spaces.may_delete).
            item["can_delete"] = spaces.may_delete(opened, by_id[item["id"]])
        return {"space_id": opened.id, "items": items}

    def handle(self, request, body):
        from django.db import IntegrityError, transaction

        from connect_labs.audit_trail.models import Action

        from .models import SavedQuery

        opened = spaces.open(_caller(request), self.kwargs["space_id"])
        name, sql, shown, max_rows = _query_fields(body)
        service.validate_selected(sql, opened)
        with transaction.atomic():
            previous = spaces.queries(opened).select_for_update().filter(name=name).order_by("-version").first()
            try:
                saved = SavedQuery.objects.create(
                    owner=request.user,
                    space=opened.space,
                    name=name,
                    sql=sql,
                    opportunity_ids=list(opened.space.opportunity_ids),
                    program_ids=list(opened.space.program_ids),
                    workflow=dict(opened.space.workflow),
                    max_rows=max_rows,
                    display=shown or {},
                    version=(previous.version + 1) if previous else 1,
                    parent=previous,
                )
            except IntegrityError as e:
                raise _Invalid("conflict", "That name was just saved; save again.") from e
        metadata = {"space_id": opened.id, "version": saved.version, "sql_length": len(sql)}
        _audit_space(Action.CREATE, opened, "labsdata_space_query", saved.pk, request.user, metadata)
        return {"saved": saved.as_dict()}


class SpaceItemDeleteView(_JsonView):
    """POST: delete a saved query from the space, every version of its name: a manager, or
    the member who saved every version of it (``spaces.delete_item``)."""

    def handle(self, request, body):
        from connect_labs.audit_trail.models import Action

        opened = spaces.open(_caller(request), self.kwargs["space_id"], allow_changed=True)
        saved = spaces.query_item(opened, self.kwargs.get("pk"))
        if saved is None:
            raise _Invalid("not_found", "There is no such query in this space.", status=404)
        deleted = spaces.delete_item(opened, saved)
        _audit_space(Action.DELETE, opened, "labsdata_space_query", saved.pk, request.user, {"space_id": opened.id})
        return {"deleted": deleted}


# --- who can open a space, and who manages it (spaces.AUDIENCES, DataSpace.managers) -----
# Person-session writes only, behind CSRF: no MCP tool and no page action reaches these
# (tests/test_spaces_gate.py). Each is a manager's (spaces.require_manager), audited with
# before and after.


class SpaceAudienceView(_JsonView):
    """POST ``{audience}``: who, among the people who hold every source, may open the space
    (``holders`` or ``private``). It can only narrow the data rule, never widen it."""

    def handle(self, request, body):
        from connect_labs.audit_trail.models import Action

        opened = spaces.open(_caller(request), self.kwargs["space_id"], allow_changed=True)
        before, after = spaces.set_audience(opened, body.get("audience"))
        metadata = {"field": "audience", "before": before, "after": after}
        _audit_space(Action.UPDATE, opened, "labsdata_space", opened.id, request.user, metadata)
        return {"space": opened.as_dict()}


class SpaceManagersView(_JsonView):
    """POST ``{person}`` (a username or email): make them a manager too. They must hold every
    source now; any refusal is one ``not_eligible``, whatever the reason."""

    def handle(self, request, body):
        from connect_labs.audit_trail.models import Action

        opened = spaces.open(_caller(request), self.kwargs["space_id"], allow_changed=True)
        spaces.require_manager(opened)  # before looking anyone up
        before, after = spaces.add_manager(opened, spaces.find_person(body.get("person")))
        metadata = {"field": "managers", "before": before, "after": after}
        _audit_space(Action.UPDATE, opened, "labsdata_space", opened.id, request.user, metadata)
        return {"space": opened.as_dict()}


class SpaceManagerRemoveView(_JsonView):
    """POST: stop someone being a manager (yourself included), never the last one."""

    def handle(self, request, body):
        from connect_labs.audit_trail.models import Action

        opened = spaces.open(_caller(request), self.kwargs["space_id"], allow_changed=True)
        before, after = spaces.remove_manager(opened, self.kwargs["user_id"])
        metadata = {"field": "managers", "before": before, "after": after}
        _audit_space(Action.UPDATE, opened, "labsdata_space", opened.id, request.user, metadata)
        # They may have removed themselves: say what they are now.
        return {"space": spaces.open(_caller(request), opened.id, allow_changed=True).as_dict()}


class SpaceAdminManagerView(_JsonView):
    """POST ``{person}``: a labs admin (Dimagi staff) makes someone who can open the space a
    manager -- for a space none of whose managers can open it any more. The admin need not
    hold the space; nothing of it is read or shown. Nobody can make themselves a manager."""

    def handle(self, request, body):
        from connect_labs.audit_trail.models import Action

        try:
            opportunity_ids, before, after = spaces.admin_add_manager(
                request.user, self.kwargs["space_id"], spaces.find_person(body.get("person"))
            )
        except PermissionError as e:
            raise _Invalid("not_admin", str(e), status=403) from e
        metadata = {"field": "managers", "before": before, "after": after, "by_labs_admin": True}
        _audit_space(Action.UPDATE, opportunity_ids, "labsdata_space", self.kwargs["space_id"], request.user, metadata)
        return {"managers": after}

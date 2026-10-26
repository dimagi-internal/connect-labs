"""The agent on a page reads only what that page shows.

A canopy page grant (``labs/canopy.py`` ``PAGE_SCOPES``) lets the agent call the ``data_*``
tools as the visitor, and the visitor's own access is wide: on a workflow run page over one
programme, the agent could otherwise read any other programme the person holds and offer a
combined sum (data_chat_eval's ``outside-selection``). So a call under a page grant is held
to the page's selection:

* a **workflow run page** -- the workflow's opportunities, and its pipelines;
* a **data space's page** -- that space;
* the **plain Data page** -- the selection on screen.

**Where the selection comes from: labs, never the agent.** When labs draws a page with the
panel, it signs the page's selection into the panel's token URL (``attach``), bound to that
page token and that person. When the panel mints, labs' mint view (``labs/canopy_token.py``)
verifies it and records it (``record_mint``): a ``PageHold`` for the page token, and a
``PageGrant`` for the ID-JAG it issued. canopy redeems that ID-JAG for a delegated token
whose ``grant_jti`` is the ID-JAG's ``jti`` (the canopy SDK's ``DelegatedToken``), so at the
MCP a call's page is found through its own token (``current``). On the plain Data page the
person changes the selection in place; the page tells labs in the person's own session
(``update_from_page``, CSRF, as them, every source resolved as them) -- the agent has no
tool or page action that can: the page reports a selection only with the DOM event of the
person's own click (``static/labsdata/data.js`` ``reportSelection``), and a page action that
names other sources shows them in a canvas tab without changing the selection.

A delegated call whose grant has no hold is refused (fail closed). A person's own MCP use --
a PAT, an OAuth sign-in -- has no page and is not held here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import quote

from django.core import signing

#: The signed selection's query parameter on the panel's token URL.
PARAM = "holds"
_SALT = "connect_labs.labsdata.page_holds"
#: A page token lives two hours (the SDK's ``DEFAULT_MAX_AGE_SECONDS``); the selection signed
#: beside it, as long.
MAX_AGE_SECONDS = 2 * 60 * 60
#: The scope whose tools this holds to the page.
DATA_SCOPE = "data:read"
#: The plain Data page: the only page whose selection the person changes in place.
DATA_PAGE = "labsdata:index"
#: Where a wider question goes.
DATA_PAGE_URL = "/labs/data/"


@dataclass(frozen=True)
class Holds:
    """A page's selection as labs drew it: a space, a workflow, and/or the opportunities.

    ``opportunity_ids`` is None only for a Data page on a workflow, whose opportunities are
    the workflow's own."""

    space_id: int | None = None
    workflow: dict | None = None
    opportunity_ids: tuple[int, ...] | None = ()

    def as_claims(self) -> dict:
        return {
            "s": self.space_id,
            "w": self.workflow or None,
            "o": list(self.opportunity_ids) if self.opportunity_ids is not None else None,
        }


def _ints(values) -> tuple[int, ...]:
    out = set()
    for v in values or ():
        try:
            out.add(int(v))
        except (TypeError, ValueError):
            continue
    return tuple(sorted(out))


def _workflow_ref(workflow) -> dict | None:
    if not isinstance(workflow, dict) or workflow.get("id") in (None, ""):
        return None
    try:
        out = {"id": int(workflow["id"])}
        for key in ("opportunity_id", "program_id", "run_id"):
            if workflow.get(key) not in (None, ""):
                out[key] = int(workflow[key])
        if workflow.get("pipeline_ids"):
            out["pipeline_ids"] = list(_ints(workflow["pipeline_ids"]))
    except (TypeError, ValueError):
        return None
    return out


def for_space(opened) -> Holds:
    """A data space's page: the space, and the opportunities it froze."""
    workflow = _workflow_ref(opened.space.workflow)
    return Holds(space_id=opened.id, workflow=workflow, opportunity_ids=_ints(opened.space.opportunity_ids))


def for_workflow(definition_id: int, owner: dict, opportunity_ids, *, run_id=None, pipeline_ids=()) -> Holds:
    """A workflow run page: the workflow (in the scope it lives in), the run it shows, the
    pipelines it reads and the opportunities it reads."""
    ref = {"id": definition_id, **(owner or {}), "run_id": run_id, "pipeline_ids": list(pipeline_ids or ())}
    return Holds(workflow=_workflow_ref(ref), opportunity_ids=_ints(opportunity_ids))


def for_selection(initial: dict, held: dict) -> Holds:
    """The plain Data page on ``initial`` (``views.initial_selection``): its opportunities and
    every opportunity of its programs, among those the person holds (``held``: the
    ``scope.directory`` the page already read). A workflow selection holds to the workflow."""
    workflow = _workflow_ref(initial.get("workflow"))
    if workflow:
        return Holds(workflow=workflow, opportunity_ids=None)
    programs = set(_ints(initial.get("program_ids")))
    ids = {o for o in _ints(initial.get("opportunity_ids")) if o in held}
    ids |= {o.id for o in held.values() if o.program_id is not None and o.program_id in programs}
    return Holds(opportunity_ids=tuple(sorted(ids)))


def checksum(page_token: str) -> str:
    return hashlib.sha256((page_token or "").encode()).hexdigest()


def drawing(page_token: str, signed_holds: str) -> str:
    """One drawing of one page: its page token AND the selection signed beside it. (A page
    token names a route, a person and a second, so two pages drawn in the same second share
    one; the selection tells them apart.)"""
    return hashlib.sha256(f"{page_token or ''}\n{signed_holds or ''}".encode()).hexdigest()


def sign(holds: Holds, page_token: str, user_id) -> str:
    """The selection, signed with labs' own key and bound to this page token and this person."""
    return signing.dumps(
        {"t": checksum(page_token), "u": str(user_id), **holds.as_claims()}, salt=_SALT, compress=True
    )


def _unsign(value: str, page_token: str, user_id) -> Holds | None:
    try:
        claims = signing.loads(value, salt=_SALT, max_age=MAX_AGE_SECONDS)
    except (signing.BadSignature, TypeError, ValueError):
        return None
    if not isinstance(claims, dict) or claims.get("t") != checksum(page_token) or claims.get("u") != str(user_id):
        return None
    ids = claims.get("o")
    return Holds(
        space_id=claims.get("s") if isinstance(claims.get("s"), int) else None,
        workflow=_workflow_ref(claims.get("w")),
        opportunity_ids=None if ids is None else _ints(ids),
    )


def attach(request, context: dict, holds: Holds) -> dict:
    """The panel ``context`` with the page's selection signed onto its token URL, so the mint
    can record what this page shows. Nothing changes for a panel with no page token."""
    token = context.get("page_token") or ""
    url = context.get("token_url") or ""
    user = getattr(request, "user", None)
    if not (context.get("ready") and token and url and user is not None and user.is_authenticated):
        return context
    from canopy_sdk.django.pages import panel_options

    signed = sign(holds, token, user.pk)
    context = {**context, "token_url": url + ("&" if "?" in url else "?") + f"{PARAM}={quote(signed, safe='')}"}
    context["options"] = panel_options(context)
    # For the plain Data page, which names this drawing when it reports a new selection.
    context["page_holds"] = signed
    return context


# ---------------------------------------------------------------------------
# The mint: record the hold for the grant just issued
# ---------------------------------------------------------------------------


def record_mint(request, page, payload: dict) -> None:
    """After labs' mint built the arrival for ``page``: record which page the ID-JAG in it is
    for. Only grants carrying ``data:read`` need one. A missing or forged selection records
    nothing, and the MCP then refuses every data read under that grant."""
    import logging

    from canopy_sdk import contract
    from canopy_sdk.jose import unverified_claims

    from .models import PageGrant, PageHold

    id_jag = payload.get(contract.ARRIVAL_ID_JAG_FIELD)
    if not id_jag or page is None or DATA_SCOPE not in (page.scopes or ()):
        return
    jti = str(unverified_claims(id_jag).get("jti") or "")[:64]
    page_token = request.GET.get("page") or ""
    signed = request.GET.get(PARAM) or ""
    holds = _unsign(signed, page_token, request.user.pk)
    if not jti or holds is None:
        logging.getLogger(__name__).warning(
            "canopy mint on %s for user %s carried no valid page selection; its data reads will be refused",
            page.key,
            request.user.pk,
        )
        return
    # The first mint from a drawing of the page creates its hold; a later one (the panel
    # re-mints as tokens expire) keeps whatever the person has changed it to since.
    hold, _created = PageHold.objects.get_or_create(
        page_token_checksum=drawing(page_token, signed),
        defaults={
            "user": request.user,
            "page_key": page.key,
            "space_id": holds.space_id,
            "workflow": holds.workflow or {},
            "opportunity_ids": list(holds.opportunity_ids) if holds.opportunity_ids is not None else None,
        },
    )
    if hold.user_id != request.user.pk:
        return
    PageGrant.objects.get_or_create(grant_jti=jti, defaults={"hold": hold})


def update_from_page(user, page_token: str, signed_holds: str, opportunity_ids, workflow=None) -> dict:
    """The person changed the plain Data page's selection: hold its agent to the new one.

    ``opportunity_ids`` / ``workflow`` are the selection as ``spaces.generate`` resolved it AS
    THE PERSON (every source held), in their own session. Only a token for the plain Data
    page, signed for this person, may be updated: a run page's or a space's selection is fixed."""
    from canopy_sdk.django import conf

    from .models import PageHold

    page = conf.page_tokens().read(page_token, user.pk) if page_token else None
    if page is None or page.key != DATA_PAGE or _unsign(signed_holds, page_token, user.pk) is None:
        raise ValueError("not this person's Data page")
    workflow = _workflow_ref(workflow)
    ids = list(_ints(opportunity_ids))
    PageHold.objects.update_or_create(
        page_token_checksum=drawing(page_token, signed_holds),
        defaults={
            "user": user,
            "page_key": page.key,
            "space_id": None,
            "workflow": workflow or {},
            "opportunity_ids": ids,
        },
    )
    return {"opportunity_ids": ids, "workflow": workflow}


def prune(days: int = 2) -> int:
    """Drop holds older than any grant on them can live (delegated tokens last an hour)."""
    from datetime import timedelta

    from django.utils import timezone

    from .models import PageHold

    deleted, _ = PageHold.objects.filter(updated_at__lt=timezone.now() - timedelta(days=days)).delete()
    return deleted


# ---------------------------------------------------------------------------
# The MCP: the call's page, and holding a read to it
# ---------------------------------------------------------------------------


class OutsidePage(Exception):
    """A data read under a page grant that reaches outside what the page shows."""

    code = "outside_page"

    def __init__(self, message: str, *, outside=(), page: dict | None = None):
        super().__init__(message)
        self.message = message
        self.outside = list(outside)
        self.page = page or {}

    def as_dict(self) -> dict:
        out = {"reason": self.code, "page": self.page}
        if self.outside:
            out["outside_opportunity_ids"] = self.outside
        return out


#: A delegated call whose grant names no hold.
_NO_HOLD = object()


def current():
    """The hold for this MCP call: a ``PageHold``, ``None`` when the call is not under a page
    grant (a person's own PAT or sign-in, a direct call), or ``_NO_HOLD`` -- refused."""
    try:
        from fastmcp.server.dependencies import get_access_token

        access = get_access_token()
    except Exception:  # noqa: BLE001 -- no MCP request (direct calls, tests): no page
        return None
    claims = getattr(access, "claims", None) or {}
    if claims.get("auth_method") != "delegated":
        return None
    from .models import PageGrant

    jti = str(claims.get("grant_jti") or "")
    grant = PageGrant.objects.select_related("hold").filter(grant_jti=jti).first() if jti else None
    return grant.hold if grant is not None else _NO_HOLD


def current_page() -> dict | None:
    """What this call's page shows (``describe``), or None off a page."""
    hold = current()
    return describe(hold) if hold is not None and hold is not _NO_HOLD else None


def describe(hold) -> dict:
    """What a page shows, for the agent: ids and kind, never rows."""
    out = {"page": hold.page_key}
    if hold.space_id is not None:
        out["space_id"] = hold.space_id
    if hold.workflow:
        out["workflow"] = dict(hold.workflow)
    if hold.opportunity_ids is not None:
        out["opportunity_ids"] = list(hold.opportunity_ids)
    return out


def _what(hold) -> str:
    if hold.space_id is not None:
        return f"data space {hold.space_id}"
    if hold.page_key == DATA_PAGE:
        return "the selection on the Data page"
    if hold.workflow:
        run = hold.workflow.get("run_id")
        return (
            f"workflow {hold.workflow.get('id')}"
            + (f", run {run}" if run else "")
            + (" (its opportunities and its pipelines)")
        )
    return "this page's selection"


def _wider(hold) -> str:
    if hold.page_key == DATA_PAGE and hold.space_id is None:
        return "To read more, ask the person to add it to the selection on the Data page; do not widen it yourself."
    return (
        "For a wider question, ask the person to open the Data page "
        f"({DATA_PAGE_URL}) and choose what to read there; do not widen or swap what this page reads."
    )


def _refuse(hold, why: str, outside=()) -> OutsidePage:
    return OutsidePage(
        f"{why} The agent on this page reads only what the page shows: {_what(hold)}. {_wider(hold)}",
        outside=outside,
        page=describe(hold),
    )


def _no_hold() -> OutsidePage:
    return OutsidePage(
        "This conversation's page did not record what it shows, so the data tools read nothing through it. "
        "Ask the person to reload the page (or open the Data page for a wider question)."
    )


def hold_to_page(opened, *, caller=None) -> None:
    """Refuse ``opened`` (the space a data call resolved, ``spaces.from_request``) unless it
    is within the call's page. No page (a person's own MCP use): nothing to hold to."""
    hold = current()
    if hold is None:
        return
    if hold is _NO_HOLD:
        raise _no_hold()
    if hold.space_id is not None and not (opened.saved and opened.id == hold.space_id):
        raise _refuse(hold, f"This call names other sources than data space {hold.space_id}; pass space_id alone.")
    call_workflow = (opened.selection.workflow or {}).get("id") if opened.selection.workflow else None
    if call_workflow is None and opened.space.workflow:
        call_workflow = opened.space.workflow.get("id")
    page_workflow = (hold.workflow or {}).get("id")
    if call_workflow is not None and call_workflow != page_workflow:
        raise _refuse(hold, f"Workflow {call_workflow} is not this page's.")
    allowed = hold.opportunity_ids
    if allowed is None:
        # A Data page on a workflow: the same workflow resolves to its own opportunities.
        if call_workflow is not None:
            return
        allowed = _workflow_opportunities(hold, caller)
    outside = sorted(set(opened.selection.ids) - {int(o) for o in allowed})
    if outside:
        raise _refuse(
            hold,
            f"Opportunit{'y' if len(outside) == 1 else 'ies'} {', '.join(map(str, outside))} "
            f"{'is' if len(outside) == 1 else 'are'} not part of this page.",
            outside=outside,
        )


def _workflow_opportunities(hold, caller) -> list[int]:
    """A Data page on a workflow, read without naming it: the workflow's opportunities, as the person."""
    from . import spaces

    if caller is None:
        return []
    try:
        return list(spaces.generate(caller, workflow=dict(hold.workflow)).selection.ids)
    except Exception:  # noqa: BLE001 -- cannot tell means nothing is inside
        return []


def hold_workflow(definition_id) -> None:
    """``data_workflow_context`` under a page grant: only the page's own workflow."""
    hold = current()
    if hold is None:
        return
    if hold is _NO_HOLD:
        raise _no_hold()
    page_workflow = (hold.workflow or {}).get("id")
    try:
        asked = int(definition_id)
    except (TypeError, ValueError):
        asked = None
    if page_workflow is None or asked != page_workflow:
        raise _refuse(
            hold,
            f"Workflow {definition_id} is not this page's"
            + (f" (it shows workflow {page_workflow})." if page_workflow else " (it shows no workflow)."),
        )


# ---------------------------------------------------------------------------
# The run page's own tools: its run, its workflow
# ---------------------------------------------------------------------------

#: Tools that act on a run, by its ``run_id``: a run page's grant reaches only its own run.
RUN_TOOLS = frozenset(
    {
        "workflow_run_context",
        "workflow_run_indicators",
        "workflow_indicator_explain",
        "workflow_run_cases",
        "workflow_action_status",
        "workflow_run_action",
        "workflow_action_preview_view",
    }
)
#: Proposable edit tools that name the workflow they change (``workflow_id``).
WORKFLOW_EDIT_TOOLS = frozenset(
    {
        "workflow_update_definition",
        "workflow_update_render_code",
        "workflow_patch_render_code",
        "workflow_add_pipeline_source",
        "workflow_remove_pipeline_source",
    }
)


def _asked_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def run_denial(tool_name: str, arguments: dict) -> OutsidePage | None:
    """Why a run page's tool may not make this delegated call, or None.

    ``RUN_TOOLS`` must name the page's own run (and, when given, its scope); an edit the
    agent proposes (``workflow_edit_propose``) must change the page's own workflow, or one
    of the pipelines that workflow reads. Off a page (PAT, OAuth): None. A delegated call
    whose grant recorded no page: refused."""
    arguments = arguments or {}
    if tool_name not in RUN_TOOLS and tool_name != "workflow_edit_propose":
        return None
    hold = current()
    if hold is None:
        return None
    if hold is _NO_HOLD:
        return _no_hold()
    page = hold.workflow or {}
    if tool_name in RUN_TOOLS:
        run_id = page.get("run_id")
        asked = _asked_int(arguments.get("run_id"))
        if run_id is None or asked != run_id:
            return _refuse(
                hold,
                f"Run {arguments.get('run_id')} is not the run this page shows"
                + (f" (run {run_id})." if run_id is not None else "."),
            )
        for key in ("opportunity_id", "program_id"):
            if arguments.get(key) not in (None, "") and _asked_int(arguments[key]) != page.get(key):
                return _refuse(hold, f"That {key.replace('_', ' ')} is not this run's scope.")
        return None
    # workflow_edit_propose {tool, arguments}
    tool = (arguments.get("tool") or "").strip()
    inner = arguments.get("arguments") if isinstance(arguments.get("arguments"), dict) else {}
    if tool in WORKFLOW_EDIT_TOOLS:
        if page.get("id") is None or _asked_int(inner.get("workflow_id")) != page.get("id"):
            return _refuse(hold, f"Workflow {inner.get('workflow_id')} is not the workflow this page shows.")
    elif tool == "pipeline_update_schema":
        if _asked_int(inner.get("pipeline_id")) not in set(page.get("pipeline_ids") or ()):
            return _refuse(hold, f"Pipeline {inner.get('pipeline_id')} is not one this page's workflow reads.")
    return None

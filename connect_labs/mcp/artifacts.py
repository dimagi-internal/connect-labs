"""Host-held results: a tool returns facts for the model and a ``labs-result://`` link.

canopy-web spec ``2026-10-23-host-held-results-design``. A tool that produces rows
(``data_query``, ``workflow_run_indicators``) or a result a View draws
(``targeting_pmc_states``) keeps it here, for the person the call ran as, and returns
an MCP ``resource_link`` naming it beside a short summary. canopy carries the link,
never the data; the person's View in the chat and the agent (canopy's ``site_read``)
read it back with ``resources/read``, as themselves, a page at a time
(``labs-result://<id>?page=2&page_size=100``). The store, expiry, paging and the
owner check are the canopy SDK's (``canopy_sdk.django.artifacts``, turned on by
``ARTIFACTS`` in ``labs/canopy.py:host_settings``); what is labs' own is here:

* **Who owns it.** The labs user the call ran as (``str(user.pk)``, the subject labs
  signs for that person and the one a delegated token names). ``reader_subject`` is
  the SDK's ``READER`` hook: the caller of the MCP request in flight, read from its
  access token, so it needs no database and works on the event loop.
* **Who else may read it** (``may_read``, the SDK's ``MAY_READ``): a labs user who
  could run the same thing themselves -- holds every opportunity a query read, can
  open the run its indicators came from. Public results (``targeting_pmc_states``)
  any signed-in labs user may read.
* **What a read through canopy may carry** (``read_denial``, asked for EVERY reader,
  the owner included, by ``server.ArtifactReadGate``): a result holding labs'
  sensitive data (rows from production visits) goes back through canopy only where
  the call that read it could: an agent whose ``Canopy-Execution`` statement clears it
  for sensitive data, or the person's own View (a delegated read with no statement:
  no agent is acting) of a result a cleared agent already received through canopy.
  A "no user visit data" caller never reads one. Every read is audit-logged.
* **Snapshot, not fresh.** A read serves what the agent saw (no ``ON_READ``): the
  View and the agent must agree on the rows the agent reasoned on. Paging is over
  that snapshot.

How much a model gets inline is ``INLINE_ROWS``: see ``facts``.
"""

from __future__ import annotations

import logging

from connect_labs.labs.canopy import ARTIFACTS

logger = logging.getLogger(__name__)

#: Labs' ``CANOPY_HOST["ARTIFACTS"]`` (``labs/canopy.py``): the ``labs-result`` scheme,
#: which ``site.json`` then declares so canopy keeps the links in a sensitive conversation;
#: a day's life (long enough to come back to a conversation, short enough that a result
#: does not become a standing copy of production rows -- expired rows read as ``expired``
#: and ``sweep_artifacts`` deletes them); this module's ``may_read`` and
#: ``reader_subject``; no ``ON_READ``, so a read serves the stored snapshot.
SETTINGS = ARTIFACTS
SCHEME = ARTIFACTS["SCHEME"]
TTL_SECONDS = ARTIFACTS["TTL"]

#: How many rows a canopy agent gets inline with the facts. A result this short (most
#: aggregates, which labs' guidance asks for) reaches the model whole and needs no read;
#: a longer one reaches it as a sample of this many, with the link to page through the
#: rest (``site_read``). Twenty rows of a GROUP BY is an answer; twenty rows of a
#: visit-level SELECT is enough to see its shape without filling the context.
INLINE_ROWS = 20

#: Audit-trail resource type for a read of a kept result.
RESOURCE_TYPE = "labs_result"

#: What each kind of result is, for its ``name`` and the read rules below.
KIND_QUERY = "data_query"
KIND_INDICATORS = "workflow_run_indicators"
KIND_PMC = "pmc_states"
PUBLIC_KINDS = frozenset({KIND_PMC})

# --- who is asking --------------------------------------------------------------------


def _access_token():
    try:
        from . import server

        # Through ``server`` so the in-process test bridge (testing.py), which patches
        # that binding, is seen here as well.
        return server.get_access_token()
    except Exception:  # noqa: BLE001 -- no MCP request in flight
        return None


def reader_subject() -> str | None:
    """The SDK's ``READER``: the labs user this MCP request runs as, by its token's claims.

    No database: the SDK reads it on the event loop. None outside an MCP request (a
    Celery job saves with ``user=``)."""
    claims = getattr(_access_token(), "claims", None) or {}
    uid = claims.get("user_id")
    if uid is None:
        uid = claims.get("sub")
    return str(uid) if uid not in (None, "") else None


def delegated() -> bool:
    """True when canopy is calling as a visitor (the delegated grant)."""
    claims = getattr(_access_token(), "claims", None) or {}
    return claims.get("auth_method") == "delegated"


def _active_user(subject):
    from connect_labs.users.models import User

    try:
        return User.objects.get(pk=int(subject), is_active=True)
    except (TypeError, ValueError, User.DoesNotExist):
        return None


# --- writing --------------------------------------------------------------------------


def sensitive(opportunity_ids) -> bool:
    """Whether rows read from these opportunities are labs' sensitive data (not all generated)."""
    from connect_labs.labs.synthetic.provenance import all_generated

    ids = [i for i in (opportunity_ids or []) if i is not None]
    return not all_generated(ids)


def save(user, kind: str, payload: dict, *, access: dict, name: str, description: str = "") -> dict:
    """Keep ``payload`` for ``user`` and return the ``resource_link`` naming it.

    ``access`` is what the read rules need (``opportunity_ids``, ``sensitive``, a run's
    scope); it rides in the payload as ``access``, readable by whoever may read the rest.
    ``through_canopy`` records whether this call was canopy's: a sensitive result made
    through canopy went to an agent labs had cleared, which is what lets the person's own
    View read it back (``read_denial``)."""
    from canopy_sdk.django import artifacts

    from connect_labs.audit_trail.context import get_audit_context

    ctx = get_audit_context()
    return artifacts.save(
        kind,
        {**payload, "access": {**access, "through_canopy": delegated()}},
        user=user,
        # The audit context's id: the same one this call's MCPAuditLog row, audit-trail
        # events and any error report carry.
        tool_call_id=(ctx.request_id if ctx is not None else "") or "",
        name=name,
        description=description,
    )


def with_link(result: dict, link: dict) -> dict:
    """``result`` with its link: ``result_uri`` for a reader of the JSON, and ``_links``,
    which the server (``_run_registry_tool_inner``) puts in the content as ``resource_link``
    items and strips from what the tool returns."""
    return {**result, "result_uri": link["uri"], "_links": [link]}


def facts(columns, rows) -> dict:
    """What a canopy agent is told about a table instead of the table: ``summarize_rows``
    (the row count, the columns, the first ``INLINE_ROWS`` rows, ``truncated`` when that
    sample is not the whole result) and, when it is not, how to read the rest."""
    from canopy_sdk.host.artifacts import summarize_rows

    out = summarize_rows(columns, rows, sample=INLINE_ROWS)
    if out["truncated"]:
        out["read_more"] = (
            f"Only the first {INLINE_ROWS} of {out['row_count']} rows are here. Every row is kept at "
            "`result_uri`: read it with site_read, a page at a time (append ?page=2, ?page_size=up to 1000), "
            "rather than asking again."
        )
    return out


# --- reading --------------------------------------------------------------------------


def may_read(reader: str | None, artifact) -> bool:
    """The SDK's ``MAY_READ``, for a reader who is not the owner: could they run it themselves?"""
    user = _active_user(reader) if reader else None
    if user is None:
        return False
    try:
        return _could_run(user, artifact) is None
    except Exception:  # noqa: BLE001 -- a check that cannot answer says no
        logger.info("artifact ACL check failed for %s", getattr(artifact, "kind", ""), exc_info=True)
        return False


def _could_run(user, artifact) -> str | None:
    """Why ``user`` could not have produced ``artifact`` themselves, or None."""
    payload = artifact.payload if isinstance(artifact.payload, dict) else {}
    access = payload.get("access") or {}
    kind = artifact.kind
    if kind in PUBLIC_KINDS:
        return None
    if kind == KIND_QUERY:
        from connect_labs.labs.access.scopes import Caller
        from connect_labs.labs.connect_tokens import get_valid_access_token
        from connect_labs.labsdata import scope

        try:
            scope.resolve(Caller(user=user, access_token=get_valid_access_token(user)), access["opportunity_ids"])
        except Exception as e:  # noqa: BLE001 -- ScopeError, no Connect token, ...
            return str(e) or type(e).__name__
        return None
    if kind == KIND_INDICATORS:
        from .tools.workflow_run import _Run

        try:
            with _Run(user, int(access["run_id"]), access.get("opportunity_id"), access.get("program_id")):
                return None
        except Exception as e:  # noqa: BLE001 -- MCPToolError, no Connect token, ...
            return str(e) or type(e).__name__
    return f"no read rule for {kind!r} results"


def read_denial(artifact, access_token) -> str | None:
    """Why this read may not carry this result, or None. Asked of every reader, the owner too.

    Only a result holding sensitive data is judged. A restricted ("no user visit data")
    call never reads one. A delegated call (canopy) reads one when its own statement
    clears it for sensitive data (an agent's ``site_read``), or when it carries no
    statement -- the person's own View, no agent acting -- and the result already went
    through canopy to a cleared agent. Anything else is refused, as the call that made
    it would have been."""
    from . import execution
    from .server import restricted_call

    payload = artifact.payload if isinstance(artifact.payload, dict) else {}
    access = payload.get("access") or {}
    if not access.get("sensitive"):
        return None
    if restricted_call(access_token):
        return "this result holds user visit data, which this token may not read"
    claims = getattr(access_token, "claims", None) or {}
    if claims.get("auth_method") != "delegated":
        return None
    from connect_labs.labs import canopy_panel

    call = execution.for_this_request(access_token)
    with execution.bound(call):
        clearance = canopy_panel.call_sensitive_data_clearance()
    if clearance.granted:
        return None
    if call.execution is None and call.refused == "no_statement" and access.get("through_canopy"):
        return None
    return (
        "this result holds sensitive data, which reaches an agent through canopy only when Labs has cleared it "
        f"({clearance.reason}); open it on the Data page instead"
    )


def record_read(artifact, reader_user, uri: str) -> None:
    """Audit-trail a read of a kept result, like the read that made it. Never raises."""
    from canopy_sdk.host.artifacts import page

    from connect_labs.audit_trail.models import Action
    from connect_labs.audit_trail.service import record

    payload = artifact.payload if isinstance(artifact.payload, dict) else {}
    access = payload.get("access") or {}
    try:
        _, _, query = _parse(uri)
        body, paging = page(payload, query)
    except Exception:  # noqa: BLE001
        body, paging = None, {}
    count = len(body.get("rows") or []) if paging and isinstance(body, dict) else None
    opps = [o for o in (access.get("opportunity_ids") or []) if o is not None] or [None]
    for oid in opps:
        record(
            Action.READ,
            resource_type=RESOURCE_TYPE,
            resource_id=artifact.id,
            opportunity_id=oid,
            program_id=access.get("program_id"),
            record_count=count,
            metadata={"kind": artifact.kind, "owner": artifact.owner, **paging},
            user=reader_user,
        )


def _parse(uri: str):
    from canopy_sdk.django import artifacts

    return artifacts.artifacts().parse(uri)


def stored(uri: str):
    """The stored artifact ``uri`` names (any page of it), or None for one that is not
    labs', unknown or expired -- the SDK's own read names those refusals."""
    import time

    from canopy_sdk.django.artifacts import DjangoArtifactStore
    from canopy_sdk.host.artifacts import ArtifactRefused

    try:
        scheme, artifact_id, _ = _parse(uri)
    except ArtifactRefused:
        return None
    artifact = DjangoArtifactStore().get(scheme, artifact_id)
    if artifact is None or artifact.expired(time.time()):
        return None
    return artifact


def sweep() -> int:
    """Delete expired results; how many went."""
    from canopy_sdk.django import artifacts

    return artifacts.sweep()

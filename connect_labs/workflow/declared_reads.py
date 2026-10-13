"""Which other workflows' runs a sandboxed workflow or page may read: the ones it declares.

Spec: docs/superpowers/specs/2026-10-12-page-sandbox-design.md, "Cross-workflow reads".

In the sandbox a workflow's code reaches labs only through the host
(components/workflow/sandbox/routes.ts), and the host binds what a request names to
what the workflow covers. A run id cannot be bound there -- the host cannot tell
whose run it is -- so the reads of other workflows' runs were open: a workflow's
author could read, as the viewer, any run the viewer can see. They are bound here.

**The rule.** A definition may read runs of the definitions it DECLARES
(``readable_definition_ids``): itself, the ``workflow`` of each ``workflow_sources``
entry, a companion's report (``config.source_workflow_id`` /
``source_definition_id`` / ``report_definition_id``), the per-opportunity
instances a program report watches (``config.watched_sources`` /
``per_opp_instances`` / legacy ``watched_source``), and anything listed in
``config.read_workflows``. Each is something an author wrote on the definition, so a
viewer (or a reviewer) can see what a workflow reads before it reads it.

**How the server knows a request is the frame's.** The host stamps every request it
makes for the frame with ``X-Labs-Sandbox-For: <definition id>`` (routes.ts
``checkFetch`` -> host ``proxyFetch``). The frame cannot set or drop it: it has no
network to labs, and the host passes on only ``content-type`` and ``accept``. A
request without the header is labs' own page code or another client, and is not
restricted here (it already has the session). A header that is not a definition id
refuses every cross-workflow read.

Run state can name watched sources too (a rollup job reads them), and the frame can
write its own run's state, so a rollup started from the frame may watch only a
declared definition (``refuse_job``). A report that names a companion review
(``config.flw_review`` / ``worker_review``) consents to that review reading its runs:
it links to the review with ``?source_run=<its run>``.
"""

from __future__ import annotations

import logging

from django.http import JsonResponse

logger = logging.getLogger(__name__)

HEADER = "X-Labs-Sandbox-For"
_META_KEY = "HTTP_X_LABS_SANDBOX_FOR"
#: Where the reader's definition lives (``opportunity_id=10`` / ``program_id=25``),
#: stamped by the host beside ``HEADER``: the session's scope can have moved (another
#: tab), and a program-owned definition is invisible at an opportunity's scope.
SCOPE_HEADER = "X-Labs-Sandbox-Scope"
_SCOPE_META_KEY = "HTTP_X_LABS_SANDBOX_SCOPE"

#: Config keys a report names its companion review by (``{"workflow_id", "run_id"}``):
#: the report's own consent to that review reading its runs (it links to the review
#: with ``?source_run=<its run>``).
REVIEW_KEYS = ("flw_review", "worker_review")

#: Config keys naming ONE other definition this one reads.
SINGLE_KEYS = ("source_workflow_id", "source_definition_id", "report_definition_id")
#: Config keys listing entries that name a definition each.
LIST_KEYS = ("watched_sources", "per_opp_instances")
#: Keys an entry names its definition by.
ENTRY_KEYS = ("workflow_definition_id", "creator_definition_id", "definition_id", "workflow")
#: Explicit declaration, for a read none of the above describes.
READ_KEY = "read_workflows"

#: Jobs that read the watched sources in a run's state (workflow/job_handlers/).
WATCHED_SOURCE_JOBS = ("audit_par_rollup", "program_admin_rollup")

REFUSED = (
    "This workflow or page does not declare that workflow, so it cannot read its runs here. "
    "Name it on the definition (workflow_sources, or config.read_workflows)."
)


def _int(value) -> int | None:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _entry_ids(entry) -> set[int]:
    if isinstance(entry, dict):
        return {n for n in (_int(entry.get(k)) for k in ENTRY_KEYS) if n}
    n = _int(entry)
    return {n} if n else set()


def _definition_data(definition) -> dict:
    from connect_labs.workflow.templates import with_inherited_config_flags

    data = getattr(definition, "data", definition) or {}
    if not isinstance(data, dict):
        return {}
    template_type = getattr(definition, "template_type", None) or data.get("templateType")
    try:
        return with_inherited_config_flags(data, template_type)
    except Exception:  # noqa: BLE001 -- the declaration is read as written
        return data


def readable_definition_ids(definition) -> set[int]:
    """The definitions whose runs ``definition`` declares it reads, itself included."""
    data = _definition_data(definition)
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    out: set[int] = set()
    own = _int(getattr(definition, "id", None) or data.get("id"))
    if own:
        out.add(own)
    for key in SINGLE_KEYS:
        n = _int(config.get(key))
        if n:
            out.add(n)
    for key in LIST_KEYS:
        for entry in config.get(key) or [] if isinstance(config.get(key), list) else []:
            out |= _entry_ids(entry)
    out |= _entry_ids(config.get("watched_source"))
    for entry in config.get(READ_KEY) or [] if isinstance(config.get(READ_KEY), list) else []:
        out |= _entry_ids(entry)
    for source in data.get("workflow_sources") or [] if isinstance(data.get("workflow_sources"), list) else []:
        if isinstance(source, dict):
            n = _int(source.get("workflow"))
            if n:
                out.add(n)
    return out


# ---------------------------------------------------------------------------
# On a request
# ---------------------------------------------------------------------------


def reader(request) -> int | None | bool:
    """The definition a sandboxed request reads for: its id, ``False`` for a header
    that names none (refuse everything), or ``None`` for a request that is not the
    frame's."""
    raw = (getattr(request, "META", None) or {}).get(_META_KEY)
    if raw is None:
        return None
    return _int(raw) or False


def _stamped_scope(request) -> dict:
    raw = (getattr(request, "META", None) or {}).get(_SCOPE_META_KEY) or ""
    name, _, value = raw.partition("=")
    n = _int(value)
    return {name: n} if name in ("opportunity_id", "program_id") and n else {}


def _read_definition(request, definition_id: int, scopes: list[dict]):
    """``definition_id`` read as the viewer in the first of ``scopes`` that has it
    (``{}`` is the request's own), or None. Cached on the request."""
    from connect_labs.workflow import data_access

    cache = request.__dict__.setdefault("_sandbox_definitions", {})
    if definition_id in cache:
        return cache[definition_id]
    definition = None
    for scope in scopes:
        access = None
        try:
            access = data_access.WorkflowDataAccess(request=request, **scope)
            definition = access.get_definition(definition_id)
        except Exception:  # noqa: BLE001 -- unreadable reads as "declares nothing"
            logger.warning("sandbox: could not read definition %s in %s", definition_id, scope, exc_info=True)
        finally:
            if access is not None:
                access.close()
        if definition is not None:
            break
    cache[definition_id] = definition
    return definition


def _reader_definition(request, reader_id: int):
    stamped = _stamped_scope(request)
    return _read_definition(request, reader_id, ([stamped] if stamped else []) + [{}])


def readable_for(request) -> set[int] | None:
    """What this request may read (``None``: not the frame's, unrestricted). Fails
    closed: a reader whose definition cannot be read may read nothing."""
    r = reader(request)
    if r is None:
        return None
    if r is False:
        return set()
    definition = _reader_definition(request, r)
    if definition is None:
        return set()
    return readable_definition_ids(definition)


def _names_reader_as_its_review(request, target_id: int) -> bool:
    """Whether the definition being read names the reader as its companion review
    (``config.flw_review`` / ``worker_review``): a report linking to its review with
    ``?source_run=<its run>`` consents to that review reading the run."""
    r = reader(request)
    if not r or not target_id:
        return False
    target = _read_definition(request, target_id, [{}])
    if target is None:
        return False
    config = _definition_data(target).get("config") or {}
    for key in REVIEW_KEYS:
        entry = config.get(key)
        if isinstance(entry, dict) and _int(entry.get("workflow_id")) == r:
            return True
    return False


def refusal(request, definition_id) -> JsonResponse | None:
    """A 403 when a sandboxed request reads runs of a definition its reader does not
    declare; None when it may (or is not sandboxed)."""
    readable = readable_for(request)
    if readable is None:
        return None
    target = _int(definition_id)
    if target in readable:
        return None
    if readable and _names_reader_as_its_review(request, target):
        return None
    logger.info("[sandbox] refused a read of workflow %s for %s", definition_id, reader(request))
    return JsonResponse({"error": REFUSED, "code": "undeclared_workflow"}, status=403)


def run_refusal(request, run) -> JsonResponse | None:
    """``refusal`` for a run: by the definition the run belongs to."""
    data = getattr(run, "data", None) or {}
    return refusal(request, data.get("definition_id") or getattr(run, "definition_id", None))


def filter_runs(request, runs: list) -> list:
    """Of ``runs`` (an opportunity-wide listing), the ones a sandboxed request may see."""
    readable = readable_for(request)
    if readable is None:
        return runs
    return [r for r in runs if _int((getattr(r, "data", None) or {}).get("definition_id")) in readable]


def refuse_job(request, run, job_config: dict) -> JsonResponse | None:
    """A rollup started from the frame may watch only definitions its workflow
    declares, and only on its own run. (Run state is a record the frame can write, so
    it cannot be what declares.)"""
    r = reader(request)
    if r is None:
        return None
    job_type = (job_config or {}).get("job_type")
    if job_type not in WATCHED_SOURCE_JOBS:
        return None
    no = run_refusal(request, run)
    if no is not None:
        return no
    run_definition = _int((getattr(run, "data", None) or {}).get("definition_id"))
    if r is False or run_definition != r:
        return JsonResponse({"error": "A workflow may roll up only its own run.", "code": "not_own_run"}, status=403)
    definition = _reader_definition(request, r)
    readable = readable_definition_ids(definition)
    state = (getattr(run, "data", None) or {}).get("state") or {}
    config = (_definition_data(definition).get("config") or {}) if definition is not None else {}
    entries = []
    for holder in (state, config):
        sources = holder.get("watched_sources")
        entries += [s for s in sources if isinstance(s, dict)] if isinstance(sources, list) else []
        legacy = holder.get("watched_source")
        if isinstance(legacy, dict):
            entries += [
                {"workflow_definition_id": legacy.get("creator_definition_id"), "opportunity_id": o}
                for o in legacy.get("opportunity_ids") or []
            ]
    for entry in entries:
        ids = _entry_ids(entry)
        if ids and ids <= readable:
            continue
        return JsonResponse(
            {
                "error": (
                    "This run watches a workflow its definition does not declare. "
                    "Declare it in config.watched_sources first."
                ),
                "code": "undeclared_workflow",
            },
            status=403,
        )
    return None

"""get_opportunity_apps — fetch the Learn/Deliver CommCare app structure for an opportunity.

Wraps the production endpoint
``GET /export/opportunity/<opp_id>/app_structure/?app_type=learn|deliver|both``
(see ``commcare_connect/data_export/views.py:AppStructureView`` in
``dimagi/commcare-connect``). Each requested side returns the full HQ
application JSON (modules, forms, etc.) under ``learn_app`` / ``deliver_app``,
or ``null`` if the opportunity has no app of that type configured.

This is the structure most useful for building workflow pipeline schemas, so
agents can map form questions to their JSON paths without leaving the labs MCP.

A SYNTHETIC opportunity (a labs-only clone or demo) has no app on Connect: its apps
are the ones its fixtures carry (a clone copies its source's ``app_structure``), or,
failing that, those of the real opportunity it was cloned from. Structure only --
never a submission.

``learn_content`` reads the Learn app as text an agent can work from: each module's
forms and the words a worker reads in them, so a coach can say what the Learn app
teaches in its own words (the case card's checklist, ``workflow/actions.py``).
"""

from __future__ import annotations

import logging

import httpx

from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register

logger = logging.getLogger(__name__)

VALID_APP_TYPES = ("learn", "deliver", "both")

# Fetching app_structure round-trips through CommCare HQ on the production side,
# which can be noticeably slower than a plain labs read. Bump the per-call
# timeout above LabsRecordAPIClient's 30s default.
APP_STRUCTURE_TIMEOUT_SECONDS = 120.0


def _not_found_message(opportunity_id: int, resp: httpx.Response) -> str:
    # Connect answers 404 both when the caller can't reach the opportunity and when the
    # opportunity has no HQ key, and only its `detail` tells them apart. Said as one catch-all,
    # the second read like the caller's own login failing, which canopy's credential guard
    # aborts a turn for (ACE, 2026-11-03).
    try:
        detail = resp.json().get("detail", "")
    except (ValueError, AttributeError):
        detail = ""
    if "api key" in str(detail).lower():
        return f"Opportunity {opportunity_id} has no CommCare HQ key in Connect, so its apps can't be read."
    return f"Opportunity {opportunity_id} not found, or you do not have access to it."


@register(
    name="get_opportunity_apps",
    description=(
        "Fetch the Learn and/or Deliver CommCare application structure for a "
        "Connect opportunity. Wraps production "
        "GET /export/opportunity/<opp_id>/app_structure/. Returns "
        '{"learn_app": <hq app json or null>, "deliver_app": <hq app json or null>}. '
        "Use app_type='learn' or 'deliver' to fetch only one side; default is "
        "'both'. The full HQ application JSON (modules, forms, questions) is "
        "returned, which is what you need to build workflow pipeline schemas."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {
                "type": "integer",
                "description": "Connect opportunity ID. Caller must have access to this opportunity.",
            },
            "app_type": {
                "type": "string",
                "enum": list(VALID_APP_TYPES),
                "description": "Which side(s) to fetch. Defaults to 'both'.",
            },
        },
        "required": ["opportunity_id"],
        "additionalProperties": False,
    },
)
def get_opportunity_apps(user, opportunity_id: int, app_type: str = "both") -> dict:
    if app_type not in VALID_APP_TYPES:
        raise MCPToolError(
            "INVALID_SCHEMA",
            f"Invalid app_type {app_type!r}. Must be one of: {', '.join(VALID_APP_TYPES)}.",
        )

    # Interpolated into a URL path, so it must be an integer and nothing else: a string
    # like "999/attachment_signed_url/?blob_id=..." would reach another export endpoint.
    try:
        opportunity_id = int(opportunity_id)
    except (TypeError, ValueError):
        raise MCPToolError("INVALID_SCHEMA", f"opportunity_id must be an integer; got {opportunity_id!r}.")

    return _app_structure(user, opportunity_id, app_type)


def _wanted(apps: dict, app_type: str) -> dict:
    sides = ("learn", "deliver") if app_type == "both" else (app_type,)
    return {f"{side}_app": apps.get(f"{side}_app") for side in sides}


def _synthetic_apps(user, opportunity_id: int, app_type: str) -> dict | None:
    """A synthetic opportunity's apps: from its fixtures, else from the real opportunity it
    was cloned from. None when ``opportunity_id`` is not synthetic."""
    from connect_labs.labs.synthetic.registry import get_synthetic_opp

    synthetic = get_synthetic_opp(opportunity_id)
    if synthetic is None:
        return None
    from .synthetic import _require_opportunity_access

    _require_opportunity_access(user, opportunity_id)
    apps: dict = {}
    try:
        from connect_labs.labs.integrations.connect.factory import _get_fixture_store

        loaded = _get_fixture_store().load_endpoint(opportunity_id, "app_structure")
        apps = loaded if isinstance(loaded, dict) else {}
    except Exception:  # noqa: BLE001 -- a fixture read failure falls through to the source
        logger.warning("synthetic opp %s: app_structure fixture unreadable", opportunity_id, exc_info=True)
    if any(isinstance(apps.get(k), dict) for k in ("learn_app", "deliver_app")):
        return _wanted(apps, app_type)
    source = getattr(synthetic, "cloned_from_opportunity_id", None)
    if source:
        out = _connect_apps(user, int(source), app_type)
        return {**out, "source_opportunity_id": int(source)}
    return _wanted({}, app_type)


def _app_structure(user, opportunity_id: int, app_type: str) -> dict:
    synthetic = _synthetic_apps(user, opportunity_id, app_type)
    if synthetic is not None:
        return synthetic
    return _connect_apps(user, opportunity_id, app_type)


def _connect_apps(user, opportunity_id: int, app_type: str) -> dict:
    token = require_connect_token(user)
    client = LabsRecordAPIClient(access_token=token)
    try:
        url = f"{client.base_url}/export/opportunity/{opportunity_id}/app_structure/"
        try:
            resp = client.http_client.get(
                url,
                params={"app_type": app_type},
                timeout=APP_STRUCTURE_TIMEOUT_SECONDS,
            )
        except httpx.RequestError as e:
            raise MCPToolError(
                "UPSTREAM_ERROR",
                f"Failed to reach Connect production: {e}",
            )

        if resp.status_code == 404:
            raise MCPToolError(
                "NOT_FOUND",
                _not_found_message(opportunity_id, resp),
            )
        if resp.status_code == 502:
            raise MCPToolError(
                "UPSTREAM_ERROR",
                "Connect could not fetch the app structure from CommCare HQ.",
            )
        if resp.status_code >= 400:
            raise MCPToolError(
                "UPSTREAM_ERROR",
                f"Unexpected response from Connect ({resp.status_code}): {resp.text[:200]}",
            )

        return resp.json()
    finally:
        client.close()


# ---------------------------------------------------------------------------
# The Learn app as text
# ---------------------------------------------------------------------------

#: Most characters of text returned per form, and in all.
FORM_TEXT_LIMIT = 4000
TOTAL_TEXT_LIMIT = 40000


def _words(value) -> str:
    """An HQ label (a string, or a ``{lang: text}`` map) as plain text, first language."""
    if isinstance(value, dict):
        value = next((v for v in value.values() if isinstance(v, str) and v.strip()), "")
    if not isinstance(value, str):
        return ""
    import re

    text = re.sub(r"<output[^>]*/>", "…", value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*_#`]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _form_lines(questions: list) -> list[str]:
    """The words a worker reads in a form, in order: each label, a question's choices
    after it."""
    lines: list[str] = []
    for q in questions or []:
        if not isinstance(q, dict):
            continue
        label = _words(q.get("label"))
        if label:
            lines.append(label)
        opts = [_words(o.get("label")) for o in q.get("options") or [] if isinstance(o, dict)]
        opts = [o for o in opts if o]
        if opts:
            lines.append("  choices: " + " | ".join(opts))
        if q.get("children"):
            lines.extend(_form_lines(q["children"]))
    return lines


def learn_digest(learn_app: dict | None, search: str | None = None) -> list[dict]:
    """``[{module, forms: [{form, text}]}]`` for a Learn app's JSON; with ``search``, only
    the forms whose name or text contains it (case-insensitive)."""
    out, budget = [], TOTAL_TEXT_LIMIT
    needle = (search or "").strip().lower()
    for module in (learn_app or {}).get("modules") or []:
        if not isinstance(module, dict):
            continue
        mname = _words(module.get("name"))
        forms = []
        for form in module.get("forms") or []:
            if not isinstance(form, dict):
                continue
            fname = _words(form.get("name"))
            text = "\n".join(_form_lines(form.get("questions")))
            if needle and needle not in (mname + " " + fname + " " + text).lower():
                continue
            text = text[:FORM_TEXT_LIMIT]
            if budget <= 0:
                break
            text = text[:budget]
            budget -= len(text)
            forms.append({"form": fname, "text": text})
        if forms:
            out.append({"module": mname, "forms": forms})
    return out


@register(
    name="learn_content",
    description=(
        "Read an opportunity's LEARN app as text: each module and form, with the words a "
        "worker reads in it (labels, and a quiz question's choices). Use it to say what the "
        "Learn app teaches in its own words -- e.g. the checklist on a coaching case card "
        "(start_ocs_outreach `case.learn`). `search` keeps only the forms mentioning a word "
        "(e.g. 'weigh', 'skin', 'danger'). Works on synthetic (demo) opportunities too, "
        "through the real app they were cloned from. App text only, never submissions."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "search": {"type": "string", "maxLength": 60},
        },
        "required": ["opportunity_id"],
        "additionalProperties": False,
    },
)
def learn_content(user, opportunity_id: int, search: str | None = None) -> dict:
    try:
        opportunity_id = int(opportunity_id)
    except (TypeError, ValueError):
        raise MCPToolError("INVALID_SCHEMA", f"opportunity_id must be an integer; got {opportunity_id!r}.")
    apps = _app_structure(user, opportunity_id, "learn")
    learn = apps.get("learn_app")
    if not isinstance(learn, dict):
        raise MCPToolError("NOT_FOUND", f"Opportunity {opportunity_id} has no Learn app to read.")
    modules = learn_digest(learn, search)
    out = {"app": _words(learn.get("name")), "modules": modules}
    if apps.get("source_opportunity_id"):
        out["source_opportunity_id"] = apps["source_opportunity_id"]
    if search and not modules:
        out["note"] = f"No Learn form mentions {search!r}; try another word, or none for the whole app."
    return out

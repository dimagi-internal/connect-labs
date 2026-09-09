"""Input-availability and credibility gates -- when a number must NOT be shown.

Two separate reasons an indicator is withheld, and neither is a band:

  input availability  the scope never records the input, so the honest answer is
                      n/a rather than 0. A worker who logged no danger signs has
                      not achieved a 0% danger-sign rate.
  credibility         the workbook's Targets & settings say an LLO does not record
                      this credibly, so the figure exists but must not be published.

Both were invisible at programme level -- every input exists somewhere across 11
opportunities -- and only appeared on a drill: 268 of 5,302 per-FLW checks
disagreed with the dashboard until the input gate was ported, all on C20/C21.

EVERY FACT THESE GATES READ IS NOW AN ARGUMENT. They used to be module-level
dicts -- `IND_INPUTS` (indicator -> inputs), `APP_ASKS` (opportunity -> field ->
asks), `ASKS_AS` (alias) -- plus a `load_deployment()` call that read the ON-DISK
`deployment.yml` with `name="kmc"` defaulted. So once a workflow was bound to a
registry RECORD, the suppression compiler honoured the record while these gates
still read the repo: editing credibility "without a deploy" applied to half the
system, silently. `IND_INPUTS` now lives on each measure as `meta.inputs`, and
`APP_ASKS`/`ASKS_AS` live in the registry's deployment document, so all of it
travels with whatever registry the workflow actually names.
"""

from __future__ import annotations

from typing import Any


def any_asks(field: str, opportunity_ids, *, app_asks: dict, asks_as: dict) -> bool:
    """True if ANY opportunity in scope asks for the field.

    Unknown opportunity, or unknown field on a known opportunity, counts as
    asking -- the same fail-open choice the render makes. An empty `app_asks`
    therefore gates nothing, which is what a registry that declares no
    availability facts should do.
    """
    col = asks_as.get(field, field)
    if not opportunity_ids:
        return True
    for o in opportunity_ids:
        m = (app_asks or {}).get(str(o))
        if m is None or m.get(col) is None or m.get(col):
            return True
    return False


def input_state(measure: dict[str, Any], row: dict[str, Any], opportunity_ids=None, *, deployment: dict) -> str:
    """'ok' | 'notinapp' | 'unrecorded'.

    Two distinct reasons a number is withheld, and the distinction is real: an app
    that never asks the question ("not in app") is a different fact from an app
    that asks and recorded nothing ("unrecorded"). Both render as n/a, so the
    values agree either way -- but reporting the wrong reason misdescribes the
    programme.

    Takes the MEASURE rather than an indicator id: the inputs an indicator needs
    are part of its registry definition (`meta.inputs`), so there is no separate
    table to keep in step.
    """
    app_asks = (deployment or {}).get("app_asks") or {}
    asks_as = (deployment or {}).get("asks_as") or {}
    for field in measure.get("inputs") or []:
        if not any_asks(field, opportunity_ids, app_asks=app_asks, asks_as=asks_as):
            return "notinapp"
        gate = row.get(f"anyrec_{field}")
        if gate is not None and int(gate) == 0:
            return "unrecorded"
    return "ok"


def credible_for(indicator: str, llo: str | None, *, settings: dict) -> bool:
    """Programme scope (llo=None) is never gated: it pools credible recorders.

    The two readings are deliberately opposite -- C14 asks "is this LLO listed
    true" (an allow-list) and C18/C22 ask "is it NOT false" (a deny-list). They
    agree only when every LLO is listed explicitly, which the deployment document
    does on purpose; see its `settings:` comment.
    """
    settings = settings or {}
    if indicator == "C14":
        return llo is None or bool((settings.get("mortality_recording_credible") or {}).get(llo))
    if indicator in ("C18", "C22"):
        return llo is None or (settings.get("completion_recording_credible") or {}).get(llo) is not False
    return True

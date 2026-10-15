"""WA Revisit R2 -- the second revisit round's selection.

Two steps, then one exclusion:

**Step A -- under-covered work areas, by R1's own indicators.** The whole of
`core.indicators.evaluate_run` (EVC shortfall, NCF, Inaccessible, deworming,
MUAC, vaccination; thresholds, neighbour settings and the cluster-aware
filter, all reviewer-tunable exactly as in R1), with R2's own starting
values (`STEP_A_INDICATOR_DEFAULTS` / `STEP_A_GLOBAL_DEFAULTS`): a work area
stays a candidate only while its HSD visits are under 40% of EVC, MUAC under
70%, deworming under 40%, or it has an NCF visit and at least 3 buildings.

**Step B -- always added**, whatever step A decided, each a plain per-WA fact:

- **Suspended worker** -- the work area's assigned worker is suspended on the
  ORIGINAL opportunity (Connect's `user_data` export, `suspended`). Every
  work area they own counts, completed-looking ones included: a suspended
  worker's visits are exactly what can't be trusted.
- **Rejected visit** -- the work area has at least one Health Service Delivery
  or No Children Found visit whose review status is `rejected`, even if it
  also has approved visits. Only the original opportunity's visits are read,
  so a rejection inside the R1 opportunity never counts.
- **Inaccessible in microplan** -- Connect has the work area's own status as
  `INACCESSIBLE`. In the CHC RCT opportunities these are the clusters flagged
  inaccessible when the original microplan was made (whole work area groups
  carry it, and every one is assigned to a worker); a request still awaiting
  review (`REQUEST_FOR_INACCESSIBLE`) is not counted.

Then, by default, **every work area R1 already sent back is left out**,
whichever step selected it. The
R1 opportunity is its own Connect opportunity whose work areas were uploaded
from WA Revisit plans that carried each original work area's boundary over
unchanged (`core.areas.carry_forward_features`), so an R1 work area has
EXACTLY the original's centroid -- verified on program 217 (original
2157/`00690d66...` and R1 2306/`WT6KLDU` share centroid 8.001819283466496,
9.463060048485453). The R1 work area keeps no other link back: its slug is
new and it has no case until assigned. Matching is on the centroid rounded to
`_CENTROID_DECIMALS` places (~1 cm), so floating-point noise can't split a
match but two neighbouring work areas never collide.

Pure functions over the same per-WA rows `core.candidates.build_evaluation_input`
builds, plus two fetch helpers the Celery task calls once per run
(`mopup.tasks.fetch_evaluation_data`). The returned candidates have the same
shape `core.indicators.evaluate_run` produces, so the ward summary, map,
lock and plan hand-off all work unchanged.
"""

from __future__ import annotations

import logging

from connect_labs.mopup.core import indicators as ind

logger = logging.getLogger(__name__)

SUSPENDED_FLW = "suspended_flw"
REJECTED_VISIT = "rejected_visit"
MICROPLAN_INACCESSIBLE = "microplan_inaccessible"

R2_INDICATORS = [SUSPENDED_FLW, REJECTED_VISIT, MICROPLAN_INACCESSIBLE]

R2_INDICATOR_DEFS = [
    {"key": SUSPENDED_FLW, "label": "Suspended worker", "tier": 1},
    {"key": REJECTED_VISIT, "label": "Rejected visit (HSD or NCF)", "tier": 1},
    {"key": MICROPLAN_INACCESSIBLE, "label": "Inaccessible in microplan", "tier": 1},
]

# The per-rule counts payload also reports how many work areas matched an
# enabled rule but were left out because R1 already covered them.
EXCLUDED_IN_R1 = "excluded_in_r1"

# Program 217 (CHC NG RCT): each original LLO opportunity -> its R1 revisit
# opportunity. Only a default for the setup page's R1 picker -- a run stores
# whichever R1 opportunity was actually chosen (`MopupRunRecord.r1_opportunity_id`).
R1_OPPORTUNITY_BY_ORIGINAL = {
    2154: 2304,  # JHF
    2155: 2305,  # EHA
    2156: 2291,  # ISODAF
    2157: 2306,  # SOLINA
}

_INACCESSIBLE_STATUS = "INACCESSIBLE"
_CENTROID_DECIMALS = 7

# Step A's starting values: R1's indicator set, re-pointed at R2's bar --
# "drop a work area whose HSD visits are over 40% of EVC, MUAC over 70%,
# deworming over 40%, or that had an NCF with only 1-2 buildings". R1
# flags a rate BELOW its threshold, so the same numbers are the thresholds.
# R1's own Inaccessible indicator is off: step B's microplan-inaccessible
# rule covers inaccessibility (approved status only). Vaccination is off.
STEP_A_INDICATOR_DEFAULTS = {
    ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.4},
    ind.NCF: {"enabled": True},
    ind.INACCESSIBLE: {"enabled": False},
    ind.DEWORMING: {"enabled": True, "threshold": 0.4},
    ind.MUAC: {"enabled": True, "threshold": 0.7},
    ind.VACCINATION: {"enabled": False, "threshold": 0.7},
}
STEP_A_GLOBAL_DEFAULTS = {
    # NCF only counts in a work area with at least 3 buildings.
    "min_building_count": 3,
}

# Step B's rules all start on, for every LLO; each has its own toggle.
STEP_B_DEFAULTS = {key: {"enabled": True} for key in R2_INDICATORS}

ALL_KEYS = ind.ALL_INDICATORS + R2_INDICATORS

DEFAULT_GLOBAL_CONFIG = {"exclude_r1": True}


def default_indicator_configs(opportunity_id: int | None = None) -> dict[str, dict]:
    """Step A's R1 indicators plus step B's rules, as R2 starts them."""
    step_a = {
        key: {**ind.DEFAULT_INDICATOR_CONFIGS[key], **STEP_A_INDICATOR_DEFAULTS.get(key, {})}
        for key in ind.ALL_INDICATORS
    }
    return {**step_a, **{key: dict(cfg) for key, cfg in STEP_B_DEFAULTS.items()}}


def default_global_config() -> dict:
    return {**ind.DEFAULT_GLOBAL_CONFIG, **STEP_A_GLOBAL_DEFAULTS, **DEFAULT_GLOBAL_CONFIG}


def merged_indicator_configs(raw: dict | None, opportunity_id: int | None = None) -> dict[str, dict]:
    """Every step A indicator and step B rule, defaulted then overridden per
    key by `raw` (a payload's or a run's saved `indicator_configs`). Keys
    `raw` has that are neither are dropped, the same self-healing
    `views._merged_indicator_configs` does for R1 -- which is also what lets
    an R2 run saved before step A existed open with step A's defaults."""
    raw = raw or {}
    defaults = default_indicator_configs(opportunity_id)
    return {key: {**defaults[key], **(raw.get(key) or {})} for key in ALL_KEYS}


def merged_global_config(raw: dict | None) -> dict:
    defaults = default_global_config()
    raw = raw or {}
    return {**defaults, **{k: v for k, v in raw.items() if k in defaults}}


def centroid_key(lon, lat) -> str | None:
    if lon is None or lat is None:
        return None
    try:
        return f"{round(float(lon), _CENTROID_DECIMALS)},{round(float(lat), _CENTROID_DECIMALS)}"
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Fetches -- called once per run by mopup.tasks.fetch_evaluation_data
# ---------------------------------------------------------------------------


def fetch_suspended_usernames(opportunity_id: int, access_token: str) -> set[str]:
    """Connect usernames of every worker suspended on `opportunity_id`, from
    its `user_data` export (the same export `fetch_flw_names` and the
    program 217 daily reports' suspended badge read)."""
    from connect_labs.labs.integrations.connect.export_client import ExportAPIError
    from connect_labs.labs.integrations.connect.factory import get_export_client

    endpoint = f"/export/opportunity/{opportunity_id}/user_data/"
    try:
        with get_export_client(opportunity_id=opportunity_id, access_token=access_token, timeout=60.0) as client:
            records = client.fetch_all(endpoint)
    except ExportAPIError as e:
        raise RuntimeError(f"Could not read workers for opportunity {opportunity_id}: {e}") from e
    return {str(r["username"]) for r in records if r.get("username") and _truthy(r.get("suspended"))}


def fetch_r1_centroid_keys(r1_opportunity_id: int, *, pipeline) -> set[str]:
    """Centroid keys of every work area in the R1 opportunity."""
    from connect_labs.mopup.core.geometry import fetch_work_area_centroids

    keys = set()
    for lon, lat in fetch_work_area_centroids(r1_opportunity_id, pipeline=pipeline):
        key = centroid_key(lon, lat)
        if key:
            keys.add(key)
    return keys


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def annotate_rows(rows: list[dict], *, suspended_usernames: set[str], r1_centroid_keys: set[str]) -> None:
    """Stamp the two per-WA facts R2 needs that aren't on a row already, in
    place: `owner_suspended` and `in_r1` (both 0/1).

    The owner is the work area's assigned worker in Connect
    (`owner_username`); for a row without one, the last worker who submitted
    a visit there (`flw_username`) stands in -- also a Connect username, so
    the same suspended set applies."""
    for row in rows:
        owner = row.get("owner_username") or row.get("flw_username") or ""
        row["owner_suspended"] = 1 if owner and owner in suspended_usernames else 0
        key = centroid_key(row.get("lon"), row.get("lat"))
        row["in_r1"] = 1 if key and key in r1_centroid_keys else 0


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _rule_detail(row: dict, key: str) -> dict | None:
    """The rule's evidence for this work area if it fires, else None."""
    if key == SUSPENDED_FLW:
        if row.get("owner_suspended"):
            return {"worker": row.get("owner_username") or row.get("flw_username") or ""}
        return None
    if key == REJECTED_VISIT:
        hsd = row.get("rejected_hsd_count", 0) or 0
        ncf = row.get("rejected_ncf_count", 0) or 0
        if hsd + ncf > 0:
            return {"rejected_hsd": hsd, "rejected_ncf": ncf}
        return None
    if key == MICROPLAN_INACCESSIBLE:
        if (row.get("connect_status") or "").upper() == _INACCESSIBLE_STATUS:
            return {"connect_status": _INACCESSIBLE_STATUS}
        return None
    return None


def _step_b_candidate(row: dict, detail: dict) -> dict:
    triggered = [key for key in R2_INDICATORS if key in detail]
    return {
        "wa_id": row["wa_id"],
        "wa_name": row.get("wa_name", ""),
        "wag_name": row.get("wag_name", ""),
        "ward": row.get("ward", ""),
        "lga": row.get("lga", ""),
        "state": row.get("state", ""),
        "flw_username": row.get("flw_username", ""),
        "flw_name": row.get("flw_name", ""),
        "boundary": row.get("boundary"),
        "building_count": row.get("building_count", 0),
        "expected_visit_count": row.get("expected_visit_count", 0),
        "source": ind.SOURCE_EXISTING_WA,
        "triggered_indicators": triggered,
        "severity_count": len(triggered),
        "tier": 1,
        "detail": detail,
    }


def evaluate_run(
    rows: list[dict], indicator_configs: dict, global_config: dict | None = None
) -> tuple[list[dict], dict]:
    """Step A (R1's indicators) OR step B (R2's rules), then -- unless
    `exclude_r1` is off -- minus every work area already in R1.

    A work area both steps select is one candidate listing every reason:
    step A's indicators first, then step B's rules, with both steps'
    evidence in `detail`. Returns the candidates plus
    ``{"excluded_in_r1": n, "r1_matched": n, "step_a": n, "step_b_only": n}``:
    how many selected work areas R1 already covered (and were left out),
    how many in scope R1 covered at all (a check the R1 match found
    anything), and how many survivors came from step A versus only from B."""
    config = merged_global_config(global_config)
    configs = indicator_configs or {}

    step_a_configs = {key: configs[key] for key in ind.ALL_INDICATORS if key in configs}
    step_a_global = {k: v for k, v in config.items() if k not in DEFAULT_GLOBAL_CONFIG}
    by_id = {c["wa_id"]: c for c in ind.evaluate_run(rows, step_a_configs, step_a_global)}
    from_step_a = set(by_id)

    enabled_b = [key for key in R2_INDICATORS if (configs.get(key) or {}).get("enabled")]
    for row in rows:
        detail = {}
        for key in enabled_b:
            evidence = _rule_detail(row, key)
            if evidence is not None:
                detail[key] = evidence
        if not detail:
            continue
        existing = by_id.get(row["wa_id"])
        if existing is None:
            by_id[row["wa_id"]] = _step_b_candidate(row, detail)
            continue
        existing["triggered_indicators"] = existing["triggered_indicators"] + [
            key for key in R2_INDICATORS if key in detail
        ]
        existing["severity_count"] = len(existing["triggered_indicators"])
        existing["tier"] = 1
        existing["detail"] = {**existing.get("detail", {}), **detail}

    in_r1 = {row["wa_id"] for row in rows if row.get("in_r1")}
    candidates = []
    excluded_in_r1 = 0
    for row in rows:
        candidate = by_id.get(row["wa_id"])
        if candidate is None:
            continue
        candidate["in_r1"] = row["wa_id"] in in_r1
        if candidate["in_r1"] and config["exclude_r1"]:
            excluded_in_r1 += 1
            continue
        candidates.append(candidate)

    step_a_count = sum(1 for c in candidates if c["wa_id"] in from_step_a)
    return candidates, {
        EXCLUDED_IN_R1: excluded_in_r1,
        "r1_matched": len(in_r1),
        "step_a": step_a_count,
        "step_b_only": len(candidates) - step_a_count,
    }

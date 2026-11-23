"""Audit reviews: the decision engine.

A pure function, ported line for line from the render code of workflow 30017
("KC Audit reviews", engine.js), which was itself ported from the handover prototype.
No database, no network: source runs, settings and staff decisions in, a "world" out
(entity histories, reviews with their events, and the proposals for every run).

Everything is recomputed from scratch each time. The inputs that matter are small
(one row per entity per weekly run), and replaying the whole history keeps the
results identical to the browser version, which the parity test checks
(``tests/test_engine_parity.py``).

Shapes
------
D (the data)::

    {
      "source": {"program_name": str, "opportunities": [{"id", "name", "llo"}]},
      "metrics": {<id>: {"title", "unit", "direction", "bands": [green, yellow],
                         "min_den", "flw_applicable", ...}},
      "pool": {<rate id>: <count id>},      # pools a rate across a worker's opportunities
      "parents": {<detail id>: <main id>},
      "supporting": [<detail ids>],
      "knownMetrics": [<ids>] | None,
      "runs": [{"run_id", "period_end", "programme": {<id>: cell},
                "llo": {<name>: {<id>: cell}}, "opportunity": {<opp id>: {<id>: cell}},
                "worker": [{"key", "username", "name", "opp", "llo", "cells": {<id>: cell}}]}],
    }

A cell is ``{"band", "value", "n"}`` as KC Pulse saves it.

decisions::

    {"opens":     {<run id>: {<entity>: {"num", "by", "at"}}},
     "closes":    {<run id>: {<entity>: {"status", "by", "at"}}},
     "dismissed": {<run id>: {<entity>: {"by", "at"}}}}

Times (``at``, event ``t``) are epoch milliseconds, as in the browser.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

ENGINE_VERSION = 1

NEGATIVE_DEFAULT = [
    "new_yellow",
    "new_red",
    "worsened_to_yellow",
    "worsened_to_red",
    "stayed_yellow",
    "stayed_red",
    "became_data_gap",
    "stayed_data_gap",
]
WORSE = ["new_yellow", "new_red", "worsened_to_yellow", "worsened_to_red", "became_data_gap"]
BETTER = ["improved_to_yellow", "improved_to_green"]
STUCK_CH = ["stayed_yellow", "stayed_red", "stayed_data_gap"]
AUTO_CLS = ["nochange", "stuck", "improved", "worsened", "absent"]
LEVELS = ["program", "llo", "opportunity", "flw"]
ACTION_KEYS = ["investigate", "share_feedback", "assess_progress", "formal_warning", "suspend"]
LEVEL_ACTIONS_DEFAULT = {
    "program": ["investigate", "share_feedback", "assess_progress"],
    "llo": ACTION_KEYS,
    "opportunity": [],
    "flw": ACTION_KEYS,
}
COLOURED = ("green", "yellow", "red")

_INT_KEY = re.compile(r"^(0|[1-9][0-9]*)$")


def js_keys(d: dict | None) -> list:
    """Object.keys order: integer-like keys ascending first, then the rest as inserted."""
    if not d:
        return []
    ints = [k for k in d if _INT_KEY.match(str(k)) and int(k) < 2**32 - 1]
    rest = [k for k in d if k not in ints]
    return sorted(ints, key=lambda k: int(k)) + rest


def _truthy_num(x) -> bool:
    return bool(x) and x == x  # 0, None and NaN are falsy in JS


def _band_count(m: dict | None) -> int:
    return len((m or {}).get("bands") or [])


def detect_scales(D: dict) -> dict:
    """% values arrive as fractions while bands are in points: work out the factor per metric."""
    pf = {}
    for mid in js_keys(D["metrics"]):
        m = D["metrics"][mid]
        if m.get("unit") != "%":
            pf[mid] = 1
            continue
        mx = 0.0
        for r in D["runs"]:
            c = (r.get("programme") or {}).get(mid)
            v = c.get("value") if c else None
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                mx = max(mx, abs(v))
        bands_big = any(b > 1.5 for b in (m.get("bands") or []))
        pf[mid] = 100 if (mx <= 1.0001 and bands_big) else 1
    return pf


def grade(D: dict, pf: dict, mid: str, value, n) -> str:
    m = D["metrics"].get(mid)
    if not m:
        return "unbanded"
    if not _truthy_num(n):
        return "nodata"
    min_den = 10 if m.get("min_den") is None else m["min_den"]
    if n < min_den:
        return "insufficient"
    if value is None:
        return "nodata"
    if _band_count(m) < 2:
        return "unbanded"
    v = value * (pf.get(mid) or 1)
    g, y = m["bands"][0], m["bands"][1]
    if (m.get("direction") or "higher") == "lower":
        return "green" if v <= g else "yellow" if v <= y else "red"
    return "green" if v >= g else "yellow" if v >= y else "red"


def band_to_state(band, had_data_before) -> str:
    if band in COLOURED:
        return band
    if band == "unrecorded":
        return "data_gap"
    if band == "nodata":
        return "data_gap" if had_data_before else "na"
    if band == "insufficient":
        return "too_few"
    return "na"


def change_type(prev, curr) -> str:
    """prev = the last coloured state in an unbroken run of weeks; a week with no colour band breaks it."""
    if curr == "too_few":
        return "too_few"
    if curr == "na":
        return "na"
    if curr == "data_gap":
        return "stayed_data_gap" if prev == "data_gap" else "became_data_gap"
    if not prev or prev == "data_gap":
        if curr == "green":
            return "improved_to_green" if prev else "stayed_green"
        return "new_" + curr
    rank = {"green": 0, "yellow": 1, "red": 2}
    if curr == prev:
        return "stayed_" + curr
    if rank[curr] > rank[prev]:
        return "worsened_to_" + curr
    return "improved_to_" + curr


def opp_info(D: dict, oid) -> dict:
    for o in (D.get("source") or {}).get("opportunities") or []:
        if str(o.get("id")) == str(oid):
            return o
    return {"id": oid, "name": f"Opportunity {oid}", "llo": "?"}


def default_flw_key(w: dict) -> str:
    return w.get("username") or str(w.get("key")).split("::")[-1]


_WORST_ORDER = {"red": 5, "yellow": 4, "unrecorded": 3, "green": 2, "insufficient": 1, "nodata": 0}


def build_run_entities(D: dict, pf: dict, run: dict, flw_key: Callable[[dict], str]) -> dict:
    """Entities in one run: program, LLOs, opportunities, and FLWs as people pooled across opportunities."""
    out: dict[str, dict] = {}
    ids = js_keys(D["metrics"])
    pool_map = D.get("pool") or {}
    out["program"] = {
        "type": "program",
        "key": "program",
        "name": (D.get("source") or {}).get("program_name") or "KMC program",
        "parent": None,
        "cells": run.get("programme") or {},
    }
    llos = run.get("llo") or {}
    for llo in js_keys(llos):
        out["llo:" + str(llo)] = {
            "type": "llo",
            "key": "llo:" + str(llo),
            "name": llo,
            "parent": "program",
            "cells": llos[llo],
        }
    opps = run.get("opportunity") or {}
    for oid in js_keys(opps):
        o = opp_info(D, oid)
        out["opp:" + str(oid)] = {
            "type": "opportunity",
            "key": "opp:" + str(oid),
            "name": o.get("name"),
            "parent": "llo:" + str(o.get("llo")),
            "cells": opps[oid],
            "llo": o.get("llo"),
        }
    by_user: dict[str, list] = {}
    for w in run.get("worker") or []:
        by_user.setdefault(flw_key(w), []).append(w)
    for u, rows in by_user.items():
        llo = rows[0].get("llo")
        cells: dict = {}
        if len(rows) == 1:
            cells = rows[0].get("cells") or {}
        else:
            for mid in ids:
                pool = pool_map.get(mid)
                if pool:
                    num, den, anyc = 0, 0, False
                    for r in rows:
                        c = (r.get("cells") or {}).get(pool)
                        if c and _truthy_num(c.get("n")):
                            num += c.get("value") or 0
                            den += c["n"]
                            anyc = True
                    if not anyc:
                        cells[mid] = {"band": "nodata", "value": None, "n": 0}
                        continue
                    val = num / den if den else None
                    m = D["metrics"].get(mid) or {}
                    val_scaled = val * 100 if (m.get("unit") == "%" and pf.get(mid) == 1 and val is not None) else val
                    cells[mid] = {
                        "band": grade(D, pf, mid, val_scaled, den),
                        "value": val_scaled,
                        "n": den,
                        "pooled": len(rows),
                    }
                else:
                    best = None
                    for r in rows:
                        c = (r.get("cells") or {}).get(mid)
                        oc = (_WORST_ORDER.get(c.get("band"), -1)) if c else -2
                        ob = (_WORST_ORDER.get(best.get("band"), -1)) if best else -2
                        if c and (not best or oc > ob):
                            best = c
                    if best:
                        cells[mid] = {**best, "pooled": len(rows)}
        out["flw:" + u] = {
            "type": "flw",
            "key": "flw:" + u,
            "name": rows[0].get("name") or u,
            "username": u,
            "parent": "llo:" + str(llo),
            "cells": cells,
            "llo": llo,
            "opps": [r.get("opp") for r in rows],
        }
    return out


def default_config(D: dict, saved: dict | None = None) -> dict:
    """Starting settings. `saved` (the stored settings) wins wherever it says something."""
    saved = saved or {}
    supporting = D.get("supporting") or []
    parent = D.get("parents") or {}
    metrics = {}
    for mid in js_keys(D["metrics"]):
        m = D["metrics"][mid]
        if _band_count(m) < 2:
            continue
        # As in the browser, where an empty list is truthy: a saved list, even an empty one, wins.
        known = saved.get("knownMetrics")
        if known is None:
            known = D.get("knownMetrics")
        is_known = (mid in known) if known is not None else True
        role = "detail" if (not is_known or mid in supporting) else "main"
        levels = [lv for lv in LEVELS if lv != "flw"] if m.get("flw_applicable") is False else list(LEVELS)
        metrics[mid] = {
            "role": role,
            "parent": parent.get(mid),
            "levels": levels,
            "initiateOn": list(NEGATIVE_DEFAULT),
            "persistence": 1,
            "pool": "counts" if (D.get("pool") or {}).get(mid) else "worst_band",
            "autoAdded": not is_known,
            "confirmed": is_known,
        }
        s = (saved.get("metrics") or {}).get(mid)
        if s:
            metrics[mid].update(s)
    saved_levels = saved.get("levelActions") or {}
    # A level saved with no actions keeps none (and so opens no reviews); only an unsaved level takes the default.
    level_actions = {
        lv: list(saved_levels[lv] if saved_levels.get(lv) is not None else LEVEL_ACTIONS_DEFAULT[lv]) for lv in LEVELS
    }
    return {
        "metrics": metrics,
        "levelActions": level_actions,
        "closeAfter": 2 if saved.get("closeAfter") is None else saved["closeAfter"],
        "autoApply": bool(saved.get("autoApply")),
    }


def green_streak(arr) -> int:
    n, want = 0, None
    for x in reversed(arr or []):
        if x["state"] != "green" or (want is not None and x["ri"] != want):
            break
        n += 1
        want = x["ri"] - 1
    return n


def bad_streak(arr) -> int:
    n, want = 0, None
    for x in reversed(arr or []):
        if x["state"] not in ("yellow", "red", "data_gap") or (want is not None and x["ri"] != want):
            break
        n += 1
        want = x["ri"] - 1
    return n


def classify(p: dict) -> str:
    if p["kind"] == "initiate":
        return "open"
    if p["kind"] == "absent":
        return "absent"
    if p.get("suggestClose"):
        return "close"
    ch = [(p["changes"].get(m) or {}).get("change") for m in p["metrics"]]
    if p.get("added") or any(c in WORSE for c in ch):
        return "worsened"
    if any(c in BETTER for c in ch):
        return "improved"
    if any(c in STUCK_CH for c in ch):
        return "stuck"
    return "nochange"


def default_run_time(run: dict) -> int:
    """The moment a weekly run 'happens': the day after its period end, 06:00 UTC, in ms."""
    end = str(run.get("period_end") or run.get("as_of"))[:10]
    d = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(d.timestamp() * 1000) + 86_400_000 + 6 * 3_600_000


def replay(D: dict, CFG: dict, decisions: dict | None = None, opts: dict | None = None) -> dict:
    """Replays every run through the rules. See the module docstring for the shapes."""
    opts = opts or {}
    decisions = decisions or {}
    opens = decisions.get("opens") or {}
    closes = decisions.get("closes") or {}
    dismissed = decisions.get("dismissed") or {}
    flw_key = opts.get("flwKey") or default_flw_key
    run_time = opts.get("runTime") or default_run_time
    pf = detect_scales(D)
    runs = D["runs"]

    go_live_idx = 0
    if opts.get("goLiveRunId") is not None:
        go_live_idx = len(runs)
        for g, r in enumerate(runs):
            if str(r["run_id"]) == str(opts["goLiveRunId"]):
                go_live_idx = g
                break
    elif opts.get("goLiveIndex") is not None:
        go_live_idx = opts["goLiveIndex"]

    ents = [build_run_entities(D, pf, r, flw_key) for r in runs]
    entity_info: dict[str, dict] = {}
    for m in ents:
        entity_info.update(m)
    history: dict[str, dict] = {}
    last_real: dict[str, str] = {}
    had_data: dict[str, bool] = {}
    reviews: list[dict] = []
    open_by: dict[str, dict] = {}
    last_closed: dict[str, dict] = {}
    by_num: dict[str, dict] = {}
    proposals_by_run: list[list] = [None] * len(runs)
    metric_ids = list(CFG["metrics"].keys())
    mains = [mid for mid in metric_ids if CFG["metrics"][mid]["role"] == "main"]

    def level_on(lv):
        return len(CFG["levelActions"].get(lv) or []) > 0

    def persists(arr, m):
        if m["persistence"] <= 1:
            return True
        last = arr[-m["persistence"] :]
        return len(last) == m["persistence"] and all(x["change"] in m["initiateOn"] for x in last)

    def close_review(rv, t, status, actor, ri):
        if rv["status"] != "open":
            return
        rv["status"] = status
        rv["closedAt"] = t
        rv["closedRi"] = ri
        rv["events"].append({"t": t, "type": "status_change", "actor": actor, "ri": ri, "payload": {"to": status}})
        open_by.pop(rv["ent"], None)
        last_closed[rv["ent"]] = rv

    def metric_changes(p):
        return [{"m": m, **(p["changes"].get(m) or {})} for m in p["metrics"]]

    def apply_one(p, ri, t, actor):
        e = entity_info.get(p["ent"])
        if p["kind"] == "initiate":
            d = p["decision"]
            prev = last_closed.get(p["ent"])
            rv = {
                "id": d.get("num"),
                "ent": p["ent"],
                "type": e["type"],
                "name": e["name"],
                "llo": e["name"] if e["type"] == "llo" else e.get("llo"),
                "status": "open",
                "openedRi": ri,
                "openedRunId": runs[ri]["run_id"],
                "openedAt": d.get("at") or t,
                "openedBy": d.get("by") or actor,
                "closedAt": None,
                "metrics": list(p["metrics"]),
                "prevId": prev["id"] if prev else None,
                "events": [],
            }
            rv["events"].append(
                {
                    "t": t,
                    "type": "opened",
                    "actor": d.get("by") or actor,
                    "ri": ri,
                    "payload": {"metrics": metric_changes(p)},
                }
            )
            if prev:
                rv["events"].append({"t": t, "type": "linked", "actor": "system", "payload": {"prevId": prev["id"]}})
            reviews.append(rv)
            open_by[p["ent"]] = rv
            by_num[str(rv["id"])] = rv
            p["openedId"] = rv["id"]
        elif p["kind"] in ("update", "absent"):
            r = by_num.get(str(p.get("review")))
            if not r:
                return
            if p["kind"] == "absent":
                r["events"].append({"t": t, "type": "absent_from_run", "actor": "system", "ri": ri, "payload": {}})
                return
            r["events"].append(
                {
                    "t": t,
                    "type": "run_update",
                    "actor": actor,
                    "ri": ri,
                    "payload": {"metrics": metric_changes(p), "auto": bool(p.get("auto")), "cls": p.get("cls")},
                }
            )
            for m in p.get("added") or []:
                r["metrics"].append(m)
                ch = p["changes"].get(m) or {}
                r["events"].append(
                    {
                        "t": t,
                        "type": "metric_added",
                        "actor": actor,
                        "ri": ri,
                        "payload": {"m": m, "change": ch.get("change"), "state": ch.get("state")},
                    }
                )
            if p.get("suggestClose"):
                r["events"].append({"t": t, "type": "close_suggested", "actor": "system", "ri": ri, "payload": {}})

    for ri, run in enumerate(runs):
        t = run_time(run)
        rid = str(run["run_id"])
        for k in list(history.keys()):
            if k not in ents[ri]:
                for mid in metric_ids:
                    last_real.pop(k + "|" + mid, None)
        live = ri >= go_live_idx
        props: list[dict] = []
        for k, e in ents[ri].items():
            H = history.setdefault(k, {})
            row_changes = {}
            for mid in metric_ids:
                if CFG["metrics"][mid]["role"] == "off":
                    continue
                c = (e.get("cells") or {}).get(mid)
                band = c.get("band") if c else "nodata"
                lk = k + "|" + mid
                state = band_to_state(band, had_data.get(lk))
                prev = last_real.get(lk)
                ch = change_type(prev, state)
                H.setdefault(mid, []).append(
                    {
                        "ri": ri,
                        "band": band,
                        "state": state,
                        "value": c.get("value") if c and c.get("value") is not None else None,
                        "n": (c.get("n") if c else 0) or 0,
                        "change": ch,
                        "pooled": c.get("pooled") if c else None,
                    }
                )
                if state in ("green", "yellow", "red", "data_gap"):
                    last_real[lk] = state
                else:
                    last_real.pop(lk, None)
                if state in COLOURED:
                    had_data[lk] = True
                row_changes[mid] = {"prev": prev, "state": state, "change": ch}
            if not live:
                continue
            open_rv = open_by.get(k)
            trig = []
            if level_on(e["type"]):
                for mid in mains:
                    m = CFG["metrics"][mid]
                    rc = row_changes.get(mid)
                    if e["type"] in m["levels"] and rc and rc["change"] in m["initiateOn"] and persists(H[mid], m):
                        trig.append(mid)
            if open_rv:
                tracked = list(open_rv["metrics"])
                for mid in trig:
                    if mid not in tracked:
                        tracked.append(mid)
                all_green = all(green_streak(H.get(mid)) >= CFG["closeAfter"] for mid in tracked)
                props.append(
                    {
                        "ri": ri,
                        "kind": "update",
                        "ent": k,
                        "metrics": tracked,
                        "added": [mid for mid in trig if mid not in open_rv["metrics"]],
                        "changes": row_changes,
                        "suggestClose": all_green,
                        "review": open_rv["id"],
                    }
                )
            elif trig:
                props.append({"ri": ri, "kind": "initiate", "ent": k, "metrics": trig, "changes": row_changes})
        if live:
            for k, rv in list(open_by.items()):
                if k not in ents[ri]:
                    props.append(
                        {
                            "ri": ri,
                            "kind": "absent",
                            "ent": k,
                            "review": rv["id"],
                            "metrics": rv["metrics"],
                            "changes": {},
                        }
                    )
        proposals_by_run[ri] = props
        if not live:
            continue
        for p in props:
            p["cls"] = classify(p)
        for p in props:
            if p["cls"] == "open":
                d = (opens.get(rid) or {}).get(p["ent"])
                if d:
                    p["decision"] = d
                    p["decided"] = "confirmed"
                    apply_one(p, ri, t, d.get("by"))
                elif (dismissed.get(rid) or {}).get(p["ent"]):
                    p["decided"] = "dismissed"
                else:
                    p["pending"] = True
            elif p["cls"] == "close":
                # The run update is logged either way; the review closes only if someone confirmed it.
                c = (closes.get(rid) or {}).get(p["ent"])
                apply_one(p, ri, t, "system")
                if c:
                    p["decided"] = "confirmed"
                elif (dismissed.get(rid) or {}).get(p["ent"]):
                    p["decided"] = "dismissed"
                else:
                    p["pending"] = True
            else:
                p["auto"] = True
                apply_one(p, ri, t, "system")
        # Closes recorded at this run (confirmed "ready to close", Suspend, Closed – other).
        for k, c in (closes.get(rid) or {}).items():
            rv = open_by.get(k)
            if rv:
                close_review(rv, c.get("at") or t, c.get("status") or "closed_improved", c.get("by") or "system", ri)

    for r in reviews:
        r["events"].sort(key=lambda ev: ev["t"])
    return {
        "runs": runs,
        "ents": ents,
        "entityInfo": entity_info,
        "history": history,
        "reviews": reviews,
        "openBy": open_by,
        "byNum": by_num,
        "proposalsByRun": proposals_by_run,
        "latestRi": len(runs) - 1,
        "goLiveIdx": go_live_idx,
        "pf": pf,
    }


def from_kc_run(r: dict) -> dict | None:
    """A KC Pulse saved run (state read with ``snapshot.*`` keys) -> the run shape above."""
    st = r.get("state") or {}

    def get(k):
        return st.get("snapshot." + k)

    W = ((st.get("snapshot") or {}).get("series") or {}).get("WINDOW") or {
        "programme": get("series.WINDOW.programme"),
        "byLLO": get("series.WINDOW.byLLO"),
        "byOpp": get("series.WINDOW.byOpp"),
        "byFLW": get("series.WINDOW.byFLW"),
        "measures": get("series.WINDOW.measures"),
    }
    if not W.get("programme") or not W.get("byFLW"):
        return None
    llo, opp, opps = {}, {}, []
    for x in W.get("byLLO") or []:
        llo[x.get("llo")] = x.get("ind") or {}
    for x in W.get("byOpp") or []:
        opp[str(x.get("opp"))] = x.get("ind") or {}
        opps.append({"id": x.get("opp"), "llo": x.get("llo")})
    return {
        "run_id": r.get("id") if r.get("id") is not None else r.get("run_id"),
        "period_start": r.get("period_start"),
        "period_end": r.get("period_end"),
        "programme": W["programme"],
        "llo": llo,
        "opportunity": opp,
        "measures": W.get("measures"),
        "opps": opps,
        "worker": [
            {
                "key": f.get("key"),
                "username": f.get("flw"),
                "name": f.get("name"),
                "opp": str(f.get("opp")),
                "llo": f.get("llo"),
                "cells": f.get("ind") or {},
            }
            for f in (W.get("byFLW") or [])
        ],
    }


def deepcopy_world(world: dict) -> dict:
    return copy.deepcopy(world)


def as_json_safe(x: Any) -> Any:
    """For tests and the API: plain dict/list output with no callables."""
    if isinstance(x, dict):
        return {k: as_json_safe(v) for k, v in x.items() if not callable(v)}
    if isinstance(x, list):
        return [as_json_safe(v) for v in x]
    return x

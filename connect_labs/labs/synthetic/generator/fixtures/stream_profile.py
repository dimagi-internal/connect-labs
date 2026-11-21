"""A profile made from visits read off disk: the same bytes ``profiler.profile`` writes.

``profiler.profile`` holds every visit of an opportunity in memory as nested dicts and
walks that list once per statistic -- and once per FIELD for several of them (null
rate, identifier test, categorical values), so a 400-field app walks it over a thousand
times. At 35,000 cases the list alone was gigabytes, and the case pool and the YAML
string followed it into memory.

Here the visits live in a file of JSON lines (``VisitFile``), written as the export's
pages arrive, and are read twice:

1. Every statistic the profile needs from the raw visits, for every field at once:
   counts, rates, value counters, and each field's numbers (percentiles need them all).
   Each visit's four case-id fields are kept so its case can be decided once the whole
   cohort is seen, which ``mirror.build_entity_resolver`` requires.
2. The real case pool and the correlation columns, which need the fields the first pass
   chose.

The synthetic cases are then sampled worker by worker and written straight into the
YAML file as they are drawn: the pool is never held whole.

Same bytes, by construction where possible: every per-visit step calls the profiler's
own helpers on the same visit, and a per-field walk becomes an accumulator that sees the
same values in the same order. Where an answer cannot be accumulated exactly (a field
that is sometimes a group rather than a value, for the identifier test) the field is
re-read from the file with the profiler's own function. Anything the stream cannot
reproduce (a visit whose form_json is not an object, a shared object the old writer
would anchor) falls back to ``profiler.profile`` itself. ``test_stream_profile`` holds
the two byte-equal.
"""

from __future__ import annotations

import array
import datetime as dt
import json
import logging
import math
import os
import random
import tempfile
from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Iterator
from typing import IO, Any

import numpy as np
import pandas as pd
import yaml

from connect_labs.labs.deid import classify as deid

from . import case_model as cm
from . import mirror as mi
from . import profiler as pr
from .manifest import YAML_DUMPER, Manifest, ManifestValidationError
from .schema_loader import parse_form_schema_from_app_json

logger = logging.getLogger(__name__)


class _Unsupported(Exception):
    """Input the stream does not reproduce; the caller runs ``profiler.profile`` instead."""


# ---------------------------------------------------------------------------
# The visits, on disk
# ---------------------------------------------------------------------------


class VisitFile:
    """Visits as JSON lines on disk, read in order as often as needed, or by index."""

    def __init__(self, path: str):
        self.path = path
        self._offsets = array.array("q")
        self._end = 0

    @classmethod
    def write(cls, path: str, visits: Iterable[dict]) -> VisitFile:
        vf = cls(path)
        with open(path, "wb") as fh:
            vf._append(fh, visits)
        return vf

    def _append(self, fh: IO[bytes], visits: Iterable[dict]) -> None:
        for v in visits:
            line = json.dumps(v, separators=(",", ":")).encode() + b"\n"
            self._offsets.append(self._end)
            fh.write(line)
            self._end += len(line)

    def __len__(self) -> int:
        return len(self._offsets)

    def __iter__(self) -> Iterator[dict]:
        with open(self.path, "rb") as fh:
            for line in fh:
                yield json.loads(line)

    def read(self, indices: Iterable[int]) -> list[dict]:
        with open(self.path, "rb") as fh:
            out = []
            for i in indices:
                fh.seek(self._offsets[i])
                out.append(json.loads(fh.readline()))
            return out


def fetch_visits(base_url: str, opp_id: int, access_token: str, path: str) -> VisitFile:
    """The ``user_visits`` export written page by page to ``path`` (``prod_export._fetch_endpoint``'s walk)."""
    import httpx

    from connect_labs.labs.synthetic.prod_export import _V2_HEADERS

    url = f"{base_url.rstrip('/')}/export/opportunity/{opp_id}/user_visits/"
    headers = {"Authorization": f"Bearer {access_token}", **_V2_HEADERS}
    vf = VisitFile(path)
    with open(path, "wb") as fh:
        while url:
            resp = httpx.get(url, headers=headers, timeout=180, follow_redirects=True)
            resp.raise_for_status()
            payload = resp.json()
            vf._append(fh, payload.get("results") or [])
            url = payload.get("next")
    return vf


# ---------------------------------------------------------------------------
# Pass 1: everything the profile reads off the raw visits, every field at once
# ---------------------------------------------------------------------------


class _Field:
    """What the profiler's per-field walks read at one dotted path, accumulated."""

    __slots__ = (
        "present",
        "numbers",
        "id_seen",
        "id_digits",
        "group",
        "values",
        "rep_total",
        "rep_seen",
        "rep_dates",
        "rep_long",
    )

    def __init__(self) -> None:
        self.present = 0  # raw not in (None, "")  -- _profile_null_rate
        self.numbers = array.array("d")  # float(raw), in visit order -- _profile_field_distributions
        self.id_seen = 0  # _is_numeric_identifier's seen / digits
        self.id_digits = 0
        self.group = False  # a dict or list here, whose str() the identifier test reads
        # _observed_values' counter while it can still be a category; None once
        # looks_like_free_text would say so (it only ever turns True as values arrive).
        self.values: Counter | None = Counter()
        # _discover_replayable_text_paths: observations, distinct values (None past the
        # cardinality cap), all of them ISO dates, any of them too long.
        self.rep_total = 0
        self.rep_seen: set | None = set()
        self.rep_dates = True
        self.rep_long = False


_ID_RE = deid.IDENTIFIER_VALUE_RE
_MAX_TOKEN = deid.MAX_REPLAYABLE_VALUE_LEN
_MAX_CATEGORIES = deid.MAX_CATEGORY_VALUES
_MAX_REPLAY = deid.MAX_REPLAYABLE_CARDINALITY


def _observe(f: _Field, raw: Any) -> None:
    group = isinstance(raw, (dict, list))
    if raw != "":
        f.present += 1
        f.id_seen += 1
        if group:
            f.group = True
        else:
            f.id_digits += bool(_ID_RE.match(str(raw).strip().split(".")[0]))
            if f.values is not None:
                text = str(raw)
                c = f.values
                if text in c:
                    c[text] += 1
                elif any(len(t) > _MAX_TOKEN or _ID_RE.match(t) for t in text.split()):
                    f.values = None
                else:
                    c[text] = 1
                    if len(c) > _MAX_CATEGORIES:
                        f.values = None
    try:
        f.numbers.append(float(raw))
    except (ValueError, TypeError):
        pass
    if not group:
        text = str(raw).strip()
        if text:
            f.rep_total += 1
            seen = f.rep_seen
            if seen is None or text not in seen:
                if f.rep_dates and deid.parse_iso_date(text) is None:
                    f.rep_dates = False
                if len(text) > _MAX_TOKEN:
                    f.rep_long = True
                if seen is not None:
                    seen.add(text)
                    if len(seen) > _MAX_REPLAY:
                        f.rep_seen = None


class _Pass1:
    def __init__(self) -> None:
        self.n = 0
        self.fields: dict[str, _Field] = {}
        self.walked: set[str] = set()  # _walk_paths' leaves (depth <= 6), keyed as it keys them
        # Per worker, in first-visit order: visits, valid, flagged, GPS points.
        self.flw: dict[str, list] = {}
        self.dates: Counter = Counter()  # visit day -> visits
        self.first_date: dt.date | None = None
        self.last_date: dt.date | None = None
        self.dow = [0.0] * 7
        self.hod = [0.0] * 24
        self.entity_ids: set = set()
        self.over_limit = 0
        self.status_flag: dict[str, Counter] = {"flagged": Counter(), "unflagged": Counter()}
        self.flag_reasons: Counter = Counter()
        self.per_form: Counter = Counter()
        self.form_hits: dict[str, Counter] = defaultdict(Counter)
        self.repeats: dict[str, list] = {}  # path -> [instance counts, child values]
        self.ids = ([], [], [], [])  # per visit: beneficiary ref, case, subcase, entity_id
        self._intern: dict = {}

    def _keep(self, x):
        return self._intern.setdefault(x, x) if isinstance(x, str) else x

    def add(self, v: dict) -> None:
        self.n += 1
        fj = v.get("form_json") or {}
        if not isinstance(fj, dict):
            raise _Unsupported("a visit whose form_json is not an object")
        self._scan(fj, "", 0, True)

        username = v.get("username")
        if username:
            w = self.flw.get(username)
            if w is None:
                w = self.flw[username] = [0, 0, 0, []]
            w[0] += 1
            w[1] += v.get("status") in pr.VALID_STATUSES
            w[2] += bool(v.get("flagged"))
            point = pr._visit_point(v)
            if point is not None:
                w[3].append(point)

        vd = v.get("visit_date")
        if vd:
            d = _iso_day(vd)
            if d is not None:
                self.dates[d] += 1
                self.first_date = d if self.first_date is None or d < self.first_date else self.first_date
                self.last_date = d if self.last_date is None or d > self.last_date else self.last_date
                self.dow[d.weekday()] += 1
                created = v.get("date_created")
                if isinstance(created, str) and "T" in created:
                    try:
                        self.hod[dt.datetime.fromisoformat(created).hour] += 1
                    except ValueError:
                        pass

        eid = v.get("entity_id")
        if eid:
            self.entity_ids.add(self._keep(eid))
        status = v.get("status")
        self.over_limit += status == "over_limit"
        if status in ("approved", "pending", "rejected", "over_limit"):
            self.status_flag["flagged" if v.get("flagged") else "unflagged"][status] += 1
        if v.get("flagged") and v.get("flag_reason"):
            raw = v["flag_reason"]
            self.flag_reasons[raw if isinstance(raw, str) else json.dumps(raw, sort_keys=True)] += 1

        form = str((fj.get("form") or {}).get("@name") or "")
        if form:
            self.per_form[form] += 1
            seen: set[str] = set()
            _presence_walk(fj.get("form") or {}, "form", seen)
            for path in seen:
                self.form_hits[path][form] += 1

        found: dict[str, list] = {}
        pr._walk_repeat_lists(fj, "", found)
        for path, lists in found.items():
            acc = self.repeats.setdefault(path, [[], {}])
            for lst in lists:
                acc[0].append(len(lst))
                for el in lst:
                    pr._flatten_element(el, "", acc[1])

        for col, x in zip(self.ids, (mi._beneficiary_ref(v), mi._case_id(v), mi._subcase_id(v), eid or None)):
            col.append(self._keep(x))

    def _scan(self, node: dict, prefix: str, depth: int, reachable: bool) -> None:
        # ``reachable``: _extract_nested can get here (it splits a path on "."), so no
        # key on the way has a dot in it. _walk_paths keys a leaf by the joined path
        # either way, and stops below depth 6.
        for key, val in node.items():
            path = f"{prefix}.{key}" if prefix else key
            here = reachable and "." not in key
            if here and val is not None:
                f = self.fields.get(path)
                if f is None:
                    f = self.fields[path] = _Field()
                _observe(f, val)
            if isinstance(val, dict):
                self._scan(val, path, depth + 1, here)
            elif depth <= 6:
                self.walked.add(path)


def _iso_day(vd: Any) -> dt.date | None:
    """``date.fromisoformat(vd[:10])``, None where it is not a date. A non-string raises
    TypeError, as the profiler's ``_profile_temporal`` does."""
    try:
        return dt.date.fromisoformat(vd[:10])
    except ValueError:
        return None


def _presence_walk(node: Any, prefix: str, seen: set[str]) -> None:
    """profiler._profile_presence_by_form's walk."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k.startswith(("@", "#")) or k == "meta":
                continue
            _presence_walk(v, f"{prefix}.{k}" if prefix else k, seen)
    elif not isinstance(node, list) and node not in (None, ""):
        seen.add(prefix)


# ---------------------------------------------------------------------------
# Which case a visit belongs to: mirror.build_entity_resolver, on the kept ids
# ---------------------------------------------------------------------------


def _resolver(ids: tuple[list, list, list, list]):
    refs, cases, subs, ents = ids
    submitted_against: set = set()
    named: set = set()
    for ref, cid in zip(refs, cases):
        if ref:
            named.add(ref)
        for x in (cid, ref):
            if x:
                submitted_against.add(x)

    def by_case(i: int):
        ref = refs[i]
        if ref:
            return ref
        cid = cases[i]
        if cid and cid in named:
            return cid
        sub = subs[i]
        if sub and sub in submitted_against:
            return sub
        if cid:
            return cid
        eid = ents[i]
        return eid if eid else None

    def by_entity(i: int):
        eid = ents[i]
        return eid if eid else None

    def spread(fn) -> tuple[int, int]:
        keys = set()
        present = 0
        for i in range(len(refs)):
            k = fn(i)
            if k:
                keys.add(k)
                present += 1
        return len(keys), present

    case_groups, case_seen = spread(by_case)
    ent_groups, ent_seen = spread(by_entity)
    case_degenerate = case_seen and case_groups > 0.9 * case_seen
    entity_groups_better = ent_seen and ent_groups and ent_groups < 0.9 * case_groups
    if case_degenerate and entity_groups_better:
        return by_entity
    return by_case


# ---------------------------------------------------------------------------
# The profile, step for step as profiler.profile takes it
# ---------------------------------------------------------------------------


class _Profile:
    def __init__(self, visits: VisitFile, p1: _Pass1):
        self.visits = visits
        self.p1 = p1
        self._resolve = None
        self._ident: dict[str, bool] = {}

    @property
    def resolve(self):
        if self._resolve is None:
            self._resolve = _resolver(self.p1.ids)
        return self._resolve

    # -- per-field answers -------------------------------------------------

    def is_numeric_identifier(self, path: str) -> bool:
        if path not in self._ident:
            if pr._is_identifier_path(path):
                self._ident[path] = True
            else:
                f = self.p1.fields.get(path)
                if f is None:
                    self._ident[path] = False
                elif f.group:  # str() of a group: read the field again, the profiler's way
                    self._ident[path] = pr._is_numeric_identifier(self.visits, path)
                else:
                    self._ident[path] = f.id_seen > 0 and f.id_digits >= 0.5 * f.id_seen
        return self._ident[path]

    def null_rate(self, path: str) -> float:
        n = self.p1.n
        f = self.p1.fields.get(path)
        present = f.present if f is not None else 0
        return round(1.0 - present / n, 4) if n else 0.0

    def observed(self, path: str) -> Counter | None:
        """_observed_values, or None where looks_like_free_text would be True."""
        f = self.p1.fields.get(path)
        return Counter() if f is None else f.values

    def categorical(self, path: str) -> dict[str, float]:
        counts = self.observed(path)
        if counts is None:
            return {}
        total = sum(counts.values())
        if total == 0:
            return {}
        return {k: round(c / total, 4) for k, c in counts.items()}

    def field_distributions(self, paths: list[str] | None) -> dict[str, dict[str, Any]]:
        if not paths:
            paths = self.discover_numeric_paths()
        out: dict[str, dict[str, Any]] = {}
        for path in paths:
            f = self.p1.fields.get(path)
            if f is None or not f.numbers:
                continue
            values = f.numbers.tolist()
            mean, std = pr._mean_std(values)
            if len(values) >= 5 and std >= 0.001:
                out[path] = {
                    "distribution": "normal",
                    "mean": round(mean, 3),
                    "stddev": round(std, 3),
                    "lo": round(float(np.percentile(values, 1)), 3),
                    "hi": round(float(np.percentile(values, 99)), 3),
                }
            else:
                out[path] = {"distribution": "uniform", "low": round(min(values), 3), "high": round(max(values), 3)}
        return out

    def discover_numeric_paths(self, sample_size: int = 200) -> list[str]:
        """profiler._discover_numeric_paths: the same sampled cases, read back by index."""
        n = self.p1.n
        if not n:
            return []
        resolve = self.resolve
        order: dict = {}
        for i in range(n):
            eid = resolve(i)
            if eid:
                order.setdefault(eid, []).append(i)
        picked: list[int] = []
        sampled_entities: set = set()
        if order:
            eids = list(order)
            stride = max(1, len(eids) // max(1, sample_size // 4))
            for eid in eids[::stride]:
                picked.extend(order[eid])
                sampled_entities.add(eid)
                if len(picked) >= sample_size:
                    break
        if not picked:
            stride = max(1, n // sample_size)
            picked = list(range(0, n, stride))[:sample_size]
        del order

        path_counts: Counter[str] = Counter()
        path_numeric: Counter[str] = Counter()
        path_entities: dict[str, set] = defaultdict(set)
        for i, v in zip(picked, self.visits.read(picked)):
            fj = v.get("form_json") or {}
            seen: Counter[str] = Counter()
            numeric_here: Counter[str] = Counter()
            pr._walk_paths(fj, "", seen, numeric_here)
            path_counts.update(seen)
            path_numeric.update(numeric_here)
            eid = resolve(i)
            if eid:
                for path in seen:
                    path_entities[path].add(eid)

        visit_floor = len(picked) * 0.3
        case_floor = len(sampled_entities) * 0.3
        paths = []
        for path, count in path_counts.items():
            covers_visits = count >= visit_floor
            covers_cases = bool(sampled_entities) and len(path_entities.get(path, ())) >= case_floor
            if not (covers_visits or covers_cases):
                continue
            if path_numeric[path] > count * 0.5:
                paths.append(path)
        return sorted(paths)

    def replayable_text_paths(self, candidates: set[str]) -> tuple[set[str], set[str]]:
        date_like: set[str] = set()
        enumerable: set[str] = set()
        for path in candidates:
            f = self.p1.fields.get(path)
            if f is None or not f.rep_total or pr._is_identifier_path(path):
                continue
            if f.rep_total < pr._MIN_REPLAYABLE_OBSERVATIONS:
                continue
            if f.rep_dates:
                date_like.add(path)
            elif f.rep_seen is not None and not f.rep_long:
                enumerable.add(path)
        return date_like, enumerable

    # -- whole-opportunity blocks -----------------------------------------

    def ranked_flws(self) -> list[tuple[str, list]]:
        return sorted(self.p1.flw.items(), key=lambda kv: -kv[1][0])

    def personas(self, *, curate: bool, opp_jitter: float) -> list[dict[str, Any]]:
        personas = []
        for i, (_, (total, approved, flagged, _pts)) in enumerate(self.ranked_flws()):
            approval_rate = pr._safe_rate(approved, total)
            flag_rate = pr._safe_rate(flagged, total)
            archetype = pr._classify_archetype(approval_rate, flag_rate)
            if curate:
                floor = min(0.6, pr._ARCHETYPE_FLAG_FLOOR[archetype] * opp_jitter)
                flag_rate = round(max(flag_rate, floor), 3)
                approval_rate = round(1.0 - flag_rate, 3)
            acc_mean = approval_rate
            acc_std = min(0.08, acc_mean * 0.1)
            comp_mean = min(1.0, acc_mean + 0.05)
            comp_std = min(0.08, comp_mean * 0.1)
            personas.append(
                {
                    "id": f"flw_{i + 1:03d}",
                    "display_name": f"Worker {i + 1}",
                    "archetype": archetype,
                    "accuracy_distribution": {"mean": round(acc_mean, 3), "stddev": round(acc_std, 3)},
                    "completeness_distribution": {"mean": round(comp_mean, 3), "stddev": round(comp_std, 3)},
                    "flag_rate": round(flag_rate, 3),
                }
            )
        return personas

    def timeline(self) -> dict[str, Any]:
        p1 = self.p1
        if p1.first_date is None:
            today = dt.date.today()
            return {
                "start_date": (today - dt.timedelta(days=28)).isoformat(),
                "end_date": today.isoformat(),
                "weeks": 4,
                "visit_cadence_per_week_per_flw": {"mean": 8, "stddev": 2},
            }
        start, end = p1.first_date, p1.last_date
        weeks = max(1, round(max((end - start).days, 7) / 7))
        cadence_mean, cadence_std = pr._mean_std([w[0] / weeks for w in p1.flw.values()])
        return {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "weeks": weeks,
            "visit_cadence_per_week_per_flw": {
                "mean": round(cadence_mean, 1),
                "stddev": round(max(cadence_std, 0.5), 1),
            },
        }

    def weekly_volume(self, start_date: dt.date, weeks: int) -> list[float] | None:
        if weeks < 2:
            return None
        per_week = [0] * weeks
        for d, c in self.p1.dates.items():
            idx = (d - start_date).days // 7
            if 0 <= idx < weeks:
                per_week[idx] += c
        avg = sum(per_week) / weeks
        if avg <= 0:
            return None
        return [round(c / avg, 3) for c in per_week]

    def temporal(self) -> dict:
        hod = self.p1.hod if sum(self.p1.hod) else [1.0] * 24
        dow = self.p1.dow if sum(self.p1.dow) else [1.0] * 7
        return {"day_of_week": list(dow), "hour_of_day": list(hod)}

    def flag_reasons(self) -> dict[str, float]:
        counts = self.p1.flag_reasons
        total = sum(counts.values())
        return {k: round(c / total, 4) for k, c in counts.items()} if total else {}

    def presence_by_form(self, min_visits: int = 5) -> dict[str, dict[str, float]]:
        per_form, hits = self.p1.per_form, self.p1.form_hits
        return {
            path: {
                form: round(c / per_form[form], 4) for form, c in sorted(by.items()) if per_form[form] >= min_visits
            }
            for path, by in sorted(hits.items())
            if any(per_form[form] >= min_visits for form in by)
        }

    def repeat_groups(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for path, (counts, child_values) in self.p1.repeats.items():
            if not any(c > 0 for c in counts):
                continue
            total = len(counts)
            count_dist = {int(k): round(c / total, 4) for k, c in Counter(counts).items()}
            field_dists: dict[str, Any] = {}
            for rel_path, vals in child_values.items():
                dist = pr._distribution_for_values(vals)
                if dist is not None:
                    field_dists[rel_path] = dist
            out[path] = {"count": count_dist, "field_distributions": field_dists}
        return out

    def over_limit_rate(self) -> float:
        if not self.p1.n:
            return 0.0
        return min(0.5, round(self.p1.over_limit / self.p1.n, 3))

    def status_by_flag(self) -> dict[str, dict[str, float]]:
        return {
            branch: {status: round(n / sum(c.values()), 4) for status, n in sorted(c.items())}
            for branch, c in self.p1.status_flag.items()
            if sum(c.values()) >= 5
        }

    def geography(self, *, seed: int) -> dict | None:
        from statistics import median

        rng = random.Random(seed)
        bearing = rng.uniform(0, 2 * math.pi)
        shift_km = rng.uniform(*pr.GEO_SHIFT_KM)
        centers: dict[str, list[float]] = {}
        for i, (_, w) in enumerate(self.ranked_flws()):
            points = w[3]
            if not points:
                continue
            lat = median(p[0] for p in points)
            lon = median(p[1] for p in points)
            north = shift_km * math.cos(bearing) + rng.gauss(0.0, pr.GEO_WORKER_JITTER_KM)
            east = shift_km * math.sin(bearing) + rng.gauss(0.0, pr.GEO_WORKER_JITTER_KM)
            lat += north / 111.0
            lon += east / (111.0 * max(0.1, math.cos(math.radians(lat))))
            centers[f"flw_{i + 1:03d}"] = [round(lon, 4), round(lat, 4)]
        if not centers:
            return None
        return {"persona_centers": centers, "settlement_spread_km": pr.GEO_HOUSEHOLD_SPREAD_KM}

    # -- pass 2: correlation columns and the real case pool -----------------

    def pass2(self, corr_paths: list[str], kinds: dict[str, str], pool_paths: tuple | None):
        """One more read: the correlation's columns and, with ``pool_paths``, every real case."""
        n = self.p1.n
        cat_kinds = {"select", "multiselect", "text"}
        cols = {}
        for path in corr_paths:
            f = self.p1.fields.get(path)
            if not n or f is None or f.present / n < pr._MIN_CORR_COVERAGE:
                continue  # _profile_correlation's coverage cut, from pass 1's count
            if kinds.get(path, "decimal") in cat_kinds:
                cols[path] = ("cat", array.array("i"), {}, Counter())
            else:
                cols[path] = ("num", array.array("d"), None, None)

        resolve = self.resolve
        entities: dict = {}  # eid -> [visits per worker, [(day ordinal, entry, date ordinals)]]
        if pool_paths is not None:
            numeric, dates, cats = (sorted(p) for p in pool_paths)

        for i, v in enumerate(self.visits):
            fj = v.get("form_json") or {}
            for path, (kind, col, codes, counts) in cols.items():
                raw = mi._extract_nested(fj, path)
                if kind == "cat":
                    if raw in (None, ""):
                        col.append(-1)
                    else:
                        text = str(raw)
                        code = codes.get(text)
                        if code is None:
                            code = codes[text] = len(codes)
                        counts[code] += 1
                        col.append(code)
                else:
                    try:
                        col.append(float(raw))
                    except (TypeError, ValueError):
                        col.append(np.nan)
            if pool_paths is None:
                continue
            eid = resolve(i)
            if not eid:
                continue
            ent = entities.get(eid)
            if ent is None:
                ent = entities[eid] = [Counter(), []]
            ent[0][v.get("username") or ""] += 1
            d = mi._parse_date(v.get("visit_date"))
            if d is None:
                continue
            entry: dict[str, Any] = {"day": 0, "values": mi._numeric_leaves(fj, numeric)}
            date_ords = []
            for path in dates:
                dd = mi._parse_date(mi._extract_nested(fj, path))
                if dd is not None:
                    date_ords.append((path, dd.toordinal()))
            c = mi._categorical_leaves(fj, cats)
            fname = mi._extract_nested(fj, "form.@name")
            ent[1].append((d, entry, date_ords, c, fname if isinstance(fname, str) and fname else None))

        return self._correlation(cols), entities

    @staticmethod
    def _correlation(cols: dict) -> dict | None:
        if len(cols) < 2:
            return None
        frame = {}
        for path, (kind, col, codes, counts) in cols.items():
            if kind == "cat":
                # Rank by frequency, ties in first-seen order: sorted() over a Counter
                # built in visit order, as the profiler builds it.
                by_text = {code: text for text, code in codes.items()}
                ranked = sorted(((by_text[code], c) for code, c in sorted(counts.items())), key=lambda kv: -kv[1])
                rank_of_code = {codes[text]: r for r, (text, _) in enumerate(ranked)}
                raw = np.frombuffer(col, dtype=np.int32)
                if (raw >= 0).all():
                    frame[path] = np.array([rank_of_code[c] for c in raw.tolist()], dtype=np.int64)
                else:
                    frame[path] = np.array(
                        [rank_of_code[c] if c >= 0 else np.nan for c in raw.tolist()], dtype=np.float64
                    )
            else:
                frame[path] = np.frombuffer(col, dtype=np.float64).copy()
        corr = pd.DataFrame(frame).corr(method="spearman").fillna(0.0)
        mat = corr.to_numpy(copy=True)
        np.fill_diagonal(mat, 1.0)
        return {
            "fields": list(corr.columns),
            "matrix": [[round(float(x), 4) for x in row] for row in mat.tolist()],
            "method": "spearman",
        }


def _real_pool(entities: dict, username_to_persona: dict[str, str]) -> list[dict[str, Any]]:
    """mirror.profile_entity_structure's transplant pool, owners already personas (profiler.profile's remap)."""
    pool = []
    for by_flw, dated in entities.values():
        if not dated:
            continue
        owner = min(by_flw.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        persona = username_to_persona.get(owner)
        dated.sort(key=lambda row: row[0])
        if persona is None:
            continue
        first = dated[0][0]
        start = first.toordinal()
        series_visits = []
        for d, entry, date_ords, cats, form in dated:
            entry["day"] = (d - first).days
            if date_ords:
                entry["dates"] = {path: o - start for path, o in date_ords}
            if cats:
                entry["cats"] = cats
            if form:
                entry["form"] = form
            series_visits.append(entry)
        pool.append({"owner": persona, "start_date": first.isoformat(), "visits": series_visits})
    return pool


# ---------------------------------------------------------------------------
# The synthetic cases, sampled as they are written
# ---------------------------------------------------------------------------


class _Cases:
    """The sampled pool as an iterable the YAML writer drains: worker by worker, in order."""

    def __init__(self, model, owners: list[str], seed: int, processes: int, parallel: bool):
        self.model, self.owners, self.seed, self.processes, self.parallel = model, owners, seed, processes, parallel
        self.count = 0

    def __iter__(self) -> Iterator[dict]:
        batches = self._forked() if self.parallel else ([self._owner(o)] for o in self.owners)
        for batch in batches:
            for cases in batch:
                for case in cases:
                    self.count += 1
                    yield case

    def _owner(self, owner: str) -> list[dict]:
        return cm._sample_owner(self.model, owner, self.seed)

    def _forked(self) -> Iterator[list[list[dict]]]:
        """Chunks of workers across forked processes, handed back in order, a few chunks ahead.

        A bounded window, not ``pool.map``: map submits every chunk at once, and results
        the writer has not reached yet would pile up into the whole pool again."""
        import gc
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor
        from concurrent.futures.process import BrokenProcessPool

        owners = self.owners
        size = max(1, len(owners) // (self.processes * 8))
        chunks = [owners[i : i + size] for i in range(0, len(owners), size)]
        _FORK.update(model=self.model, seed=self.seed)
        gc.freeze()
        done = 0
        try:
            with ProcessPoolExecutor(
                max_workers=self.processes, mp_context=multiprocessing.get_context("fork")
            ) as pool:
                window: deque = deque()
                nxt = 0
                while done < len(chunks):
                    while nxt < len(chunks) and len(window) < 2 * self.processes:
                        window.append(pool.submit(_sample_chunk_forked, chunks[nxt]))
                        nxt += 1
                    batch = window.popleft().result()
                    done += 1
                    yield batch
        except BrokenProcessPool:
            logger.warning("a forked sampling process died (likely out of memory); sampling the rest in one process")
            for chunk in chunks[done:]:
                yield [self._owner(o) for o in chunk]
        finally:
            gc.unfreeze()
            _FORK.clear()


_FORK: dict[str, Any] = {}


def _sample_chunk_forked(owners: list[str]) -> list[list[dict]]:
    return [cm._sample_owner(_FORK["model"], o, _FORK["seed"]) for o in owners]


# ---------------------------------------------------------------------------
# YAML, written as it is walked (manifest.dump_yaml's events, to a file)
# ---------------------------------------------------------------------------


class _NotStreamable(Exception):
    pass


def _write_yaml(data: Any, fh: IO[str]) -> None:
    """``manifest.dump_yaml(data)``'s bytes to ``fh``, a ``_Cases`` drained in place of its list.

    A repeated object is what yaml.dump would anchor and the stream cannot: raised, and
    the caller writes the profile the old way. Sampled cases are new objects each (so a
    case is checked against itself, and the ids of written cases are not kept)."""
    from yaml import events as ev
    from yaml.nodes import MappingNode, ScalarNode, SequenceNode

    rep = yaml.SafeDumper(None, default_flow_style=False, sort_keys=False)
    map_tag = rep.resolve(MappingNode, None, True)
    seq_tag = rep.resolve(SequenceNode, None, True)

    def walk(x, seen: set[int]):
        if isinstance(x, _Cases):
            yield ev.SequenceStartEvent(None, seq_tag, True, flow_style=False)
            for case in x:
                yield from walk(case, set())
            yield ev.SequenceEndEvent()
            return
        if not rep.ignore_aliases(x):
            if id(x) in seen:
                raise _NotStreamable
            seen.add(id(x))
        if type(x) is dict:
            yield ev.MappingStartEvent(None, map_tag, True, flow_style=False)
            for k, v in x.items():
                yield from walk(k, seen)
                yield from walk(v, seen)
            yield ev.MappingEndEvent()
            return
        if type(x) is list:
            yield ev.SequenceStartEvent(None, seq_tag, True, flow_style=False)
            for v in x:
                yield from walk(v, seen)
            yield ev.SequenceEndEvent()
            return
        node = rep.represent_data(x)
        if not isinstance(node, ScalarNode):
            raise _NotStreamable  # a tuple, say
        implicit = (
            node.tag == rep.resolve(ScalarNode, node.value, (True, False)),
            node.tag == rep.resolve(ScalarNode, node.value, (False, True)),
        )
        yield ev.ScalarEvent(None, node.tag, implicit, node.value, style=node.style)

    def stream():
        yield ev.StreamStartEvent()
        yield ev.DocumentStartEvent(explicit=False)
        yield from walk(data, set())
        yield ev.DocumentEndEvent(explicit=False)
        yield ev.StreamEndEvent()

    yaml.emit(stream(), stream=fh, Dumper=YAML_DUMPER)


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------


def profile_to_file(
    *,
    out: str,
    opportunity_id: int,
    visits: VisitFile,
    user_data: list[dict],
    opportunity_detail: dict,
    form_json_paths: list[str] | None = None,
    app_structure: dict | None = None,
    curate: bool = False,
    mirror: bool = False,
    noise_seed: int | None = None,
    case_timelines: bool = False,
    processes: int = 1,
) -> dict[str, Any]:
    """Write to ``out`` the manifest YAML ``profiler.profile`` returns for the same visits.

    ``visits`` is the export on disk (``VisitFile.write`` / ``fetch_visits``). Returns
    what it wrote: ``{"bytes", "synthetic_cases", "streamed"}`` -- ``streamed`` False
    when the input needed ``profiler.profile`` itself (see the module doc)."""
    kwargs = dict(
        opportunity_id=opportunity_id,
        user_data=user_data,
        opportunity_detail=opportunity_detail,
        form_json_paths=form_json_paths,
        app_structure=app_structure,
        curate=curate,
        mirror=mirror,
        noise_seed=noise_seed,
        case_timelines=case_timelines,
        processes=processes,
    )
    try:
        cases = _stream(out, visits, **kwargs)
    except (_Unsupported, _NotStreamable) as exc:
        logger.warning("opp %s: profiling in memory instead (%s)", opportunity_id, type(exc).__name__)
        text = pr.profile(user_visits=list(visits), **kwargs)
        with open(out, "w") as fh:
            fh.write(text)
        return {"bytes": os.path.getsize(out), "synthetic_cases": None, "streamed": False}
    return {"bytes": os.path.getsize(out), "synthetic_cases": cases, "streamed": True}


def profile_text(*, visits: VisitFile, **kwargs) -> str:
    """``profile_to_file``'s manifest as text: what ``profiler.profile`` returns for the
    same visits, without the export ever being held in memory."""
    with tempfile.TemporaryDirectory(prefix="labs-manifest-") as tmp:
        out = os.path.join(tmp, "manifest.yaml")
        profile_to_file(out=out, visits=visits, **kwargs)
        with open(out, encoding="utf-8") as fh:
            return fh.read()


def _stream(
    out: str,
    visits: VisitFile,
    *,
    opportunity_id: int,
    user_data: list[dict],
    opportunity_detail: dict,
    form_json_paths: list[str] | None,
    app_structure: dict | None,
    curate: bool,
    mirror: bool,
    noise_seed: int | None,
    case_timelines: bool,
    processes: int,
) -> int | None:
    p1 = _Pass1()
    for v in visits:
        p1.add(v)
    P = _Profile(visits, p1)

    opp_name = opportunity_detail.get("name", f"Opportunity {opportunity_id}")
    opp_rng = random.Random(opportunity_id)
    opp_jitter = round(opp_rng.uniform(0.7, 1.4), 3) if curate else 1.0

    personas = P.personas(curate=curate, opp_jitter=opp_jitter)
    timeline = P.timeline()
    field_dists = P.field_distributions(form_json_paths)
    field_dists = {p: d for p, d in field_dists.items() if not P.is_numeric_identifier(p)}

    kinds: dict[str, str] = {}
    computed_paths: set[str] = set()
    if app_structure is not None:
        form_schema = parse_form_schema_from_app_json(app_structure, app_type="deliver")
        kinds = pr._classify_paths(form_schema)
        computed_paths = {q.json_path for q in form_schema.questions if getattr(q, "calculated", False)}
        for path, kind in kinds.items():
            if kind in {"int", "decimal"} and path not in field_dists and not P.is_numeric_identifier(path):
                extra = P.field_distributions([path])
                if path in extra:
                    field_dists[path] = extra[path]
        for path, dist in field_dists.items():
            dist["null_rate"] = P.null_rate(path)
        for path, kind in kinds.items():
            if kind in {"select", "multiselect"} and path not in field_dists:
                values = P.categorical(path)
                if values:
                    if curate and not pr._is_outcome_path(path):
                        target = round(min(0.3, opp_rng.uniform(0.05, 0.18) * opp_jitter), 4)
                        values = pr._curate_categorical(values, target)
                    field_dists[path] = {
                        "distribution": "categorical",
                        "values": values,
                        "null_rate": P.null_rate(path),
                    }

    cohort_size = max(len(p1.entity_ids), 10)
    kpis = pr._profile_kpis(field_dists, [])

    pool_paths = None
    if mirror or case_timelines:
        numeric_paths = {
            p
            for p, d in field_dists.items()
            if d.get("distribution") in ("normal", "uniform") and not P.is_numeric_identifier(p)
        }
        date_paths = {p for p, k in kinds.items() if k == "date"}
        categorical_paths = {p for p, d in field_dists.items() if d.get("distribution") == "categorical"}
        categorical_paths |= {p for p, k in kinds.items() if k in {"select", "multiselect"}}
        categorical_paths = {p for p in categorical_paths if P.observed(p) is not None}
        unclaimed = {
            p
            for p, k in kinds.items()
            if k == "text" and p not in numeric_paths and p not in date_paths and p not in categorical_paths
        }
        unclaimed |= {
            p
            for p in p1.walked
            if p not in kinds
            and not pr._is_envelope_path(p)
            and p not in numeric_paths
            and p not in date_paths
            and p not in categorical_paths
        }
        replay_dates, replay_enums = P.replayable_text_paths(unclaimed)
        pool_paths = (numeric_paths, date_paths | replay_dates, categorical_paths | replay_enums)

    correlation, entities = P.pass2(list(field_dists.keys()), kinds, pool_paths)
    temporal = P.temporal()
    flag_reasons = P.flag_reasons()

    start_date_obj = dt.date.fromisoformat(timeline["start_date"])
    weekly = P.weekly_volume(start_date_obj, timeline["weeks"])
    if weekly is not None:
        timeline["weekly_volume_multipliers"] = weekly

    cohort: dict = {"id": "primary", "size": cohort_size, "field_distributions": field_dists, "progression": "flat"}
    presence_by_form = P.presence_by_form()
    if presence_by_form:
        cohort["presence_by_form"] = presence_by_form
    if correlation is not None:
        cohort["correlation"] = correlation
    repeat_groups = P.repeat_groups()
    if repeat_groups:
        cohort["repeat_groups"] = repeat_groups

    cases = None
    seed = noise_seed if noise_seed is not None else pr._mirror_noise_seed(opportunity_id)
    if pool_paths is not None:
        username_to_persona = {u: f"flw_{i + 1:03d}" for i, (u, _) in enumerate(P.ranked_flws())}
        pool = _real_pool(entities, username_to_persona)
        del entities
        if pool:
            computed_whole = {
                p
                for p in computed_paths
                if all(
                    float(v["values"][p]).is_integer()
                    for s in pool
                    for v in s["visits"]
                    if p in (v.get("values") or {})
                )
            }
            real = [dict(s, visits=list(s.get("visits") or [])) for s in pool if s.get("visits")]
            del pool
            if real:
                model = cm.fit_case_model(real, frozen_paths=computed_whole)
                owners = sorted(model.owners)
                parallel = processes > 1 and len(owners) > 1 and len(real) >= cm._PARALLEL_MIN_CASES
                del real
                if any(model.owners[o] for o in owners):
                    cases = _Cases(model, owners, seed, processes, parallel)
                    cohort["longitudinal"] = {"mode": "modelled", "numbers": "modelled", "transplant_pool": cases}
    else:
        del entities

    anomalies = (
        pr._seed_anomalies(personas, field_dists, weeks=timeline["weeks"], opp_id=opportunity_id, jitter=opp_jitter)
        if curate
        else []
    )
    manifest_dict = {
        "opportunity_id": opportunity_id,
        "opportunity_name": opp_name,
        "random_seed": 42,
        "timeline": timeline,
        "flw_personas": personas,
        "beneficiary_cohorts": [cohort],
        "anomalies": anomalies,
        "kpi_config": kpis,
        "coaching_arcs": [],
        "temporal": temporal,
        "flag_reason_distribution": flag_reasons,
        "over_limit_rate": P.over_limit_rate(),
        "status_by_flag": P.status_by_flag(),
    }
    geography = P.geography(seed=seed)
    if geography is not None:
        manifest_dict["geography"] = geography

    # Validate before writing. The pool is only checked for being non-empty, so a
    # stand-in case validates exactly what the sampled ones would.
    if cases is not None:
        cohort["longitudinal"] = {**cohort["longitudinal"], "transplant_pool": [{}]}
    try:
        Manifest.from_dict(manifest_dict)
    except ManifestValidationError as exc:
        raise ManifestValidationError(f"Profiler produced an invalid manifest (this is a bug): {exc}") from exc
    if cases is not None:
        cohort["longitudinal"] = {**cohort["longitudinal"], "transplant_pool": cases}

    directory = os.path.dirname(os.path.abspath(out))
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".yaml.part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            try:
                _write_yaml(manifest_dict, fh)
            except yaml.representer.RepresenterError as exc:
                raise ManifestValidationError(f"Profiler produced an invalid manifest (this is a bug): {exc}") from exc
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    if cases is not None:
        logger.info("case timelines for opp %s: %s synthetic cases, written as sampled", opportunity_id, cases.count)
        return cases.count
    return None

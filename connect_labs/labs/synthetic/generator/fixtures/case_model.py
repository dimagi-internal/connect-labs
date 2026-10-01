"""Modelled case timelines: new cases sampled from models of a real opportunity's cases.

This replaces the old "close mirror" pool. Mirror kept every real case and moved it a
little (±3% on numbers, 1-14 days in time, every categorical answer verbatim), so a
clone held a near-copy of each real case's timeline. Here the real cases are only
ever used to FIT models, at profile time, and the pool that goes into the manifest is
SAMPLED from those models: every case in it is new.

What the models keep, because analyses depend on it:

* **Each worker's caseload.** Every worker (persona) gets the same number of cases,
  with the same case lengths, as its real counterpart -- counts, not contents. So
  cases-per-FLW and visits-per-case reproduce exactly.
* **Each case's timeline.** A case draws its parameters -- start values, growth
  slopes, per-case constants (birth weight, date of birth), visit spacing -- jointly
  from a Gaussian copula fitted across the real cases, conditioned on the case's
  length. So "heavier babies grow faster" and "longer cases are spaced wider" survive,
  as joint distributions, without any one real case being reproduced. Margins are
  real quantiles INTERPOLATED, so a drawn value sits between observed values rather
  than on one.
* **Outcomes tied to trajectories.** Categorical answers (alive, KMC status, danger
  signs) follow a per-path Markov chain conditioned on the case's growth tercile, so a
  slow grower is as likely to die as in the source.
* **The app's own identities.** Computed fields that equal the visit's position, or
  the case's day plus a per-case offset (visit counters, ages), are rebuilt from that
  identity rather than modelled as noise.
* **Form mix and field presence.** Form sequence is a Markov chain by position; each
  field appears with the probability its form records it.

What keeps it safe:

* **k-anonymity on everything categorical** -- an answer seen in fewer than
  ``K_MIN`` distinct cases is dropped from the model, and a field recorded in fewer
  than ``K_MIN`` cases is not modelled at all.
* **No real start date** -- start dates are each worker's dates, smoothed by a
  non-zero shift.
* **A privacy gate** (distance to closest record, ``_Gate``) -- a sampled case of 3+
  visits that lands on a real case (same visit days, numbers within 1% on average,
  and much closer to it than any other real case is) is redrawn; one the gate keeps
  refusing is dropped, never emitted. Counts are reported, and
  ``nearest_real_case_report`` measures it independently for tests.

The output has the same shape the engine already replays (``entities.plan_mirror_visits``):
``{"owner", "start_date", "visits": [{"day", "values", "dates", "cats", "form"}]}``.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.stats import norm

from .copula import nearest_psd
from .entities import _time_varying_paths

#: An answer or field seen in fewer distinct cases than this is not modelled.
K_MIN = 5
#: Gap buckets by visit position: 1st->2nd, ..., 5th->6th, then everything later.
_GAP_BUCKETS = 6
#: Start dates move by a non-zero number of days within this, each way.
START_SMOOTH_DAYS = 7
#: The privacy gate: a sampled case this close to a real one is redrawn.
_GATE_MIN_VISITS = 3
_GATE_REL_TOL = 0.01
#: ...and closer to that real case than this share of its nearest real neighbour.
_GATE_NN_SHARE = 0.25
_GATE_MAX_TRIES = 20


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------


@dataclass
class _Margin:
    """An empirical margin: sorted observed values, sampled by interpolated quantile."""

    values: np.ndarray
    whole: bool

    def ppf(self, u: float) -> float:
        m = len(self.values)
        if m == 1:
            return float(self.values[0])
        pos = min(max(u, 0.0), 1.0) * (m - 1)
        lo = int(math.floor(pos))
        hi = min(lo + 1, m - 1)
        value = float(self.values[lo] + (self.values[hi] - self.values[lo]) * (pos - lo))
        return float(round(value)) if self.whole else value

    def cdf_scores(self, x: np.ndarray) -> np.ndarray:
        """Normal scores of ``x`` by mid-rank within this margin (ties share a rank)."""
        m = len(self.values)
        left = np.searchsorted(self.values, x, side="left")
        right = np.searchsorted(self.values, x, side="right")
        u = (left + right) / 2.0 / m
        return norm.ppf(np.clip(u, 0.5 / m, 1 - 0.5 / m))


@dataclass
class _PathModel:
    kind: str  # "const" | "varying" | "index" | "dayoffset"
    whole: bool
    places: int
    lo: float
    hi: float
    resid_sd: float = 0.0
    index_k: float = 0.0


@dataclass
class _CatModel:
    varying: bool
    first: dict[int, Counter]  # tercile -> value counts (first observation / constant)
    trans: dict[tuple[int, str], Counter]  # (tercile, prev) -> next counts
    allowed: set[str]
    # Values that only ever END a case (a death, a discharge): drawn once, at the last
    # visit, from the case's growth tercile and length -- never mid-case, so a clone's
    # dead baby is not visited again and a short slow-growing case dies as often as
    # the source's do. ``ending[(tercile, long)]`` counts each terminal value and _NONE.
    terminal: set[str] = field(default_factory=set)
    ending: dict[tuple[int, int], Counter] = field(default_factory=dict)


_NONE = "\x00none"


@dataclass
class CaseModel:
    owners: dict[str, list[int]]  # persona -> its cases' visit counts
    owner_starts: dict[str, list[int]]  # persona -> its cases' start ordinals
    columns: list[str]
    margins: dict[str, _Margin]
    corr: np.ndarray
    gap_ratios: dict[int, np.ndarray]  # bucket -> gap / case mean gap
    form_first: Counter
    form_trans: dict[str, Counter]
    presence: dict[str, dict[str | None, float]]  # path -> form -> P(recorded)
    numeric: dict[str, _PathModel]
    dates: dict[str, _PathModel]
    cats: dict[str, _CatModel]
    driver: str | None
    driver_cuts: tuple[float, ...]
    median_n: float = 0.0
    report: dict[str, Any] = field(default_factory=dict)


def _decimals(value: float) -> int:
    text = repr(float(value))
    if "e" in text or "." not in text:
        return 0 if float(value).is_integer() else 6
    return min(len(text.split(".")[1].rstrip("0")), 6)


def _fit_line(points: list[tuple[float, float]]) -> tuple[float, float, list[float]]:
    xs = np.array([p[0] for p in points], dtype=float)
    ys = np.array([p[1] for p in points], dtype=float)
    if len(set(xs.tolist())) < 2:
        return float(ys[0]), 0.0, []
    slope, intercept = np.polyfit(xs, ys, 1)
    resid = (ys - (intercept + slope * xs)).tolist()
    return float(intercept), float(slope), resid


def _bucket(i: int) -> int:
    return min(i, _GAP_BUCKETS)


def fit_case_model(pool: list[dict[str, Any]], *, frozen_paths: set[str] | frozenset = frozenset()) -> CaseModel:
    """Fit the models above to a REAL pool (owners already remapped to personas)."""
    series_list = [s for s in pool if s.get("visits")]
    for s in series_list:
        s["visits"] = sorted(s["visits"], key=lambda v: v["day"])
    time_varying = _time_varying_paths(series_list)
    report: dict[str, Any] = {"real_cases": len(series_list)}

    owners: dict[str, list[int]] = defaultdict(list)
    owner_starts: dict[str, list[int]] = defaultdict(list)
    for s in series_list:
        owners[s["owner"]].append(len(s["visits"]))
        owner_starts[s["owner"]].append(dt.date.fromisoformat(s["start_date"]).toordinal())

    # Per-case parameter rows: column -> value.
    rows: list[dict[str, float]] = []
    gap_ratios: dict[int, list[float]] = defaultdict(list)
    form_first: Counter = Counter()
    form_trans: dict[str, Counter] = defaultdict(Counter)
    form_seen: Counter = Counter()
    path_in_form: dict[str, Counter] = defaultdict(Counter)

    num_obs: dict[str, list[float]] = defaultdict(list)
    num_cases: Counter = Counter()
    date_obs: dict[str, list[float]] = defaultdict(list)
    date_cases: Counter = Counter()
    resid: dict[str, list[float]] = defaultdict(list)
    index_offsets: dict[str, list[float]] = defaultdict(list)
    day_offset_consistent: dict[str, list[bool]] = defaultdict(list)

    for s in series_list:
        visits = s["visits"]
        row: dict[str, float] = {"n": float(len(visits))}
        days = [int(v["day"]) for v in visits]
        if len(days) >= 2:
            gaps = [b - a for a, b in zip(days, days[1:])]
            mean_gap = max(sum(gaps) / len(gaps), 1e-9)
            row["mean_gap"] = sum(gaps) / len(gaps)
            for i, g in enumerate(gaps, start=1):
                gap_ratios[_bucket(i)].append(g / mean_gap if mean_gap > 1e-9 else 0.0)
        forms = [v.get("form") for v in visits]
        if forms and forms[0]:
            form_first[forms[0]] += 1
        for a, b in zip(forms, forms[1:]):
            if a and b:
                form_trans[a][b] += 1
        for v in visits:
            form_seen[v.get("form")] += 1
            for path in list(v.get("values") or {}) + list(v.get("dates") or {}) + list(v.get("cats") or {}):
                path_in_form[path][v.get("form")] += 1

        per_path: dict[str, list[tuple[int, int, float]]] = defaultdict(list)  # (day, index, value)
        for idx, v in enumerate(visits, start=1):
            for path, val in (v.get("values") or {}).items():
                per_path[path].append((int(v["day"]), idx, float(val)))
                num_obs[path].append(float(val))
        for path, pts in per_path.items():
            num_cases[path] += 1
            if path in frozen_paths:
                index_offsets[path].extend(val - idx for _, idx, val in pts)
                offsets = {val - day for day, _, val in pts}
                day_offset_consistent[path].append(len(offsets) == 1)
                row[f"o:{path}"] = pts[0][2] - pts[0][0]
                # Falls through: if neither identity holds it is modelled like any
                # other field (a trend, or a per-case constant).
            if path in time_varying:
                b, sl, res = _fit_line([(d, val) for d, _, val in pts])
                row[f"b:{path}"] = b
                if len({d for d, _, _ in pts}) >= 2:
                    row[f"s:{path}"] = sl
                resid[path].extend(res)
            else:
                row[f"c:{path}"] = pts[0][2]

        per_date: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for v in visits:
            for path, off in (v.get("dates") or {}).items():
                per_date[path].append((int(v["day"]), float(off)))
                date_obs[path].append(float(off))
        for path, pts in per_date.items():
            date_cases[path] += 1
            if len({off - day for day, off in pts}) == 1 and len(pts) > 1:
                row[f"dd:{path}"] = pts[0][1] - pts[0][0]
            else:
                row[f"d:{path}"] = pts[0][1]
        rows.append(row)

    numeric: dict[str, _PathModel] = {}
    dropped = []
    for path, obs in num_obs.items():
        if num_cases[path] < K_MIN:
            dropped.append(path)
            continue
        whole = all(float(x).is_integer() for x in obs)
        places = max(_decimals(x) for x in obs)
        lo, hi = min(obs), max(obs)
        kind = "varying" if path in time_varying else "const"
        model = _PathModel(kind=kind, whole=whole, places=places, lo=lo, hi=hi)
        if path in frozen_paths:
            offs = index_offsets[path]
            common = Counter(offs).most_common(1)[0] if offs else (0.0, 0)
            if offs and common[1] / len(offs) >= 0.95:
                model.kind, model.index_k = "index", float(common[0])
            elif day_offset_consistent[path] and all(day_offset_consistent[path]):
                model.kind = "dayoffset"
            # Otherwise no identity holds and it keeps the kind set above: a trend if
            # it varies within cases, a per-case constant if it does not.
        if kind == "varying" and resid[path]:
            model.resid_sd = float(np.std(resid[path]))
        numeric[path] = model
    dates: dict[str, _PathModel] = {}
    for path, obs in date_obs.items():
        if date_cases[path] < K_MIN:
            dropped.append(path)
            continue
        dates[path] = _PathModel(kind="date", whole=True, places=0, lo=min(obs), hi=max(obs))
    report["fields_not_modelled"] = sorted(dropped)

    # Copula columns: keep those seen in enough cases, for paths still modelled.
    def _path_of(col: str) -> str:
        return col.split(":", 1)[1] if ":" in col else col

    counts = Counter(col for row in rows for col in row)
    columns = [
        col
        for col, c in sorted(counts.items())
        if c >= K_MIN and (col in ("n", "mean_gap") or _path_of(col) in numeric or _path_of(col) in dates)
    ]
    if "n" not in columns:
        columns.insert(0, "n")
    margins = {}
    for col in columns:
        vals = np.sort(np.array([row[col] for row in rows if col in row], dtype=float))
        margins[col] = _Margin(vals, whole=col in ("n",) or col.startswith(("d:", "dd:")) or _whole_col(col, numeric))
    corr = _correlation(rows, columns, margins)

    # Outcomes: tercile driver = the measured (not app-computed) time-varying path
    # with the most slopes, among those whose slope actually varies between cases.
    slope_cols = sorted(
        (
            c
            for c in columns
            if c.startswith("s:") and c[2:] not in frozen_paths and float(np.ptp(margins[c].values)) > 0
        ),
        key=lambda c: -len(margins[c].values),
    )
    driver = slope_cols[0] if slope_cols else "n"
    driver_vals = margins[driver].values
    # Growth bands: fifths when there are enough cases to fill them, else thirds.
    bands = 5 if len(series_list) >= 40 * K_MIN else 3
    cuts = tuple(float(np.quantile(driver_vals, q / bands)) for q in range(1, bands))

    cats, suppressed = _fit_cats(series_list, rows, driver, cuts, time_varying)
    report["categorical_values_suppressed"] = suppressed

    presence = {}
    for path, by_form in path_in_form.items():
        presence[path] = {f: min(1.0, by_form[f] / form_seen[f]) for f in by_form if form_seen[f]}

    return CaseModel(
        owners=dict(owners),
        owner_starts=dict(owner_starts),
        columns=columns,
        margins=margins,
        corr=corr,
        gap_ratios={b: np.array(v) for b, v in gap_ratios.items() if len(v) >= K_MIN} or {1: np.array([1.0])},
        form_first=form_first,
        form_trans=dict(form_trans),
        presence=presence,
        numeric=numeric,
        dates=dates,
        cats=cats,
        driver=driver,
        driver_cuts=cuts,
        median_n=float(np.median([len(s["visits"]) for s in series_list])),
        report=report,
    )


def _whole_col(col: str, numeric: dict[str, _PathModel]) -> bool:
    path = col.split(":", 1)[1] if ":" in col else col
    model = numeric.get(path)
    return bool(model and model.whole and col.startswith(("c:", "o:")))


def _correlation(rows: list[dict[str, float]], columns: list[str], margins: dict[str, _Margin]) -> np.ndarray:
    k = len(columns)
    scores = {}
    for col in columns:
        vals = np.array([row.get(col, np.nan) for row in rows], dtype=float)
        mask = ~np.isnan(vals)
        out = np.full(len(rows), np.nan)
        out[mask] = margins[col].cdf_scores(vals[mask])
        scores[col] = out
    corr = np.eye(k)
    for i in range(k):
        for j in range(i + 1, k):
            a, b = scores[columns[i]], scores[columns[j]]
            both = ~np.isnan(a) & ~np.isnan(b)
            if both.sum() >= K_MIN and np.std(a[both]) > 0 and np.std(b[both]) > 0:
                r = float(np.corrcoef(a[both], b[both])[0, 1])
                corr[i, j] = corr[j, i] = max(min(r, 0.999), -0.999)
    return nearest_psd(corr)


def _tercile(value: float, cuts: tuple[float, ...]) -> int:
    """The growth band ``value`` falls in (0 = slowest), given the band cut points."""
    return int(sum(1 for cut in cuts if value > cut))


def _fit_cats(series_list, rows, driver, cuts, time_varying) -> tuple[dict[str, _CatModel], int]:
    cases_with: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    # (tercile, long, seq, value at the case's LAST visit or None)
    seqs: dict[str, list[tuple[int, int, list[str], str | None]]] = defaultdict(list)
    # value -> [occurrences, occurrences at the case's last visit]
    at_end: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    median_n = float(np.median([len(s["visits"]) for s in series_list]))
    for ci, (s, row) in enumerate(zip(series_list, rows)):
        terc = _tercile(row[driver], cuts) if driver in row else 1
        long = int(len(s["visits"]) > median_n)
        per_path: dict[str, list[str]] = defaultdict(list)
        last = len(s["visits"]) - 1
        last_val: dict[str, str] = {}
        for vi, v in enumerate(s["visits"]):
            for path, val in (v.get("cats") or {}).items():
                val = str(val)
                per_path[path].append(val)
                cases_with[path][val].add(ci)
                at_end[path][val][0] += 1
                if vi == last:
                    at_end[path][val][1] += 1
                    last_val[path] = val
        for path, seq in per_path.items():
            seqs[path].append((terc, long, seq, last_val.get(path)))

    out: dict[str, _CatModel] = {}
    suppressed = 0
    for path, by_value in cases_with.items():
        allowed = {val for val, cases in by_value.items() if len(cases) >= K_MIN}
        suppressed += len(by_value) - len(allowed)
        if not allowed:
            continue
        varying = path in time_varying
        terminal = (
            {
                val
                for val in allowed
                if at_end[path][val][1] >= 0.9 * at_end[path][val][0] and len(by_value[val]) < 0.5 * len(seqs[path])
            }
            if varying
            else set()
        )
        first: dict[int, Counter] = defaultdict(Counter)
        trans: dict[tuple[int, str], Counter] = defaultdict(Counter)
        ending: dict[tuple[int, int], Counter] = defaultdict(Counter)
        for terc, long, seq, last_val in seqs[path]:
            if terminal:
                end = last_val if last_val in terminal else _NONE
                ending[(terc, long)][end] += 1
                ending[(-1, -1)][end] += 1
            seq = [x for x in seq if x in allowed and x not in terminal]
            if not seq:
                continue
            first[terc][seq[0]] += 1
            first[-1][seq[0]] += 1
            for a, b in zip(seq, seq[1:]):
                trans[(terc, a)][b] += 1
                trans[(-1, a)][b] += 1
        out[path] = _CatModel(
            varying=varying,
            first=dict(first),
            trans=dict(trans),
            allowed=allowed,
            terminal=terminal,
            ending=dict(ending),
        )
    return out, suppressed


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


def _draw(counter: Counter, rng: random.Random) -> str | None:
    total = sum(counter.values())
    if not total:
        return None
    r = rng.random() * total
    for key, c in sorted(counter.items()):
        r -= c
        if r <= 0:
            return key
    return key


def _backoff(model: _CatModel, terc: int, prev: str | None) -> Counter:
    if prev is None:
        c = model.first.get(terc)
        return c if c and sum(c.values()) >= K_MIN else model.first.get(-1, Counter())
    c = model.trans.get((terc, prev))
    if c and sum(c.values()) >= K_MIN:
        return c
    c = model.trans.get((-1, prev))
    return c if c else model.first.get(-1, Counter())


def _sample_params(model: CaseModel, n: int, rng: np.random.Generator) -> dict[str, float]:
    cols = model.columns
    i_n = cols.index("n")
    m_n = model.margins["n"]
    vals = m_n.values
    # Uniform within n's tied block of the empirical CDF, so conditioning is exact.
    left = np.searchsorted(vals, n, side="left") / len(vals)
    right = np.searchsorted(vals, n, side="right") / len(vals)
    u_n = rng.uniform(left, right) if right > left else left
    z_n = float(norm.ppf(min(max(u_n, 1e-6), 1 - 1e-6)))
    others = [i for i in range(len(cols)) if i != i_n]
    params: dict[str, float] = {"n": float(n)}
    if not others:
        return params
    r_on = model.corr[others, i_n]
    mu = r_on * z_n
    cov = model.corr[np.ix_(others, others)] - np.outer(r_on, r_on)
    w, v = np.linalg.eigh((cov + cov.T) / 2.0)
    z = mu + (v * np.sqrt(np.clip(w, 0, None))) @ rng.standard_normal(len(others))
    for i, zi in zip(others, z):
        params[cols[i]] = model.margins[cols[i]].ppf(float(norm.cdf(zi)))
    return params


def _round_clamp(value: float, pm: _PathModel) -> float:
    value = float(round(value)) if pm.whole else round(value, pm.places)
    return min(max(value, pm.lo), pm.hi)


def _sample_case(
    model: CaseModel, owner: str, n: int, start_ordinal: int, rng: random.Random, nrng: np.random.Generator
) -> dict[str, Any]:
    params = _sample_params(model, n, nrng)
    # Visit days: the case's mean gap times pooled per-position ratios.
    days = [0]
    mean_gap = max(params.get("mean_gap", 0.0), 0.0)
    for i in range(1, n):
        ratios = model.gap_ratios.get(_bucket(i))
        if ratios is None:
            ratios = model.gap_ratios[max(model.gap_ratios)]
        gap = max(0, int(round(mean_gap * float(ratios[int(nrng.integers(len(ratios)))]))))
        days.append(days[-1] + gap)
    forms: list[str | None] = []
    for i in range(n):
        if i == 0:
            forms.append(_draw(model.form_first, rng))
        else:
            prev = forms[-1]
            forms.append(_draw(model.form_trans.get(prev, Counter()), rng) or prev)

    driver_value = params.get(model.driver, params["n"]) if model.driver else params["n"]
    terc = _tercile(driver_value, model.driver_cuts)
    long = int(n > model.median_n)
    prev_cat: dict[str, str | None] = {}
    const_cat: dict[str, str | None] = {}
    # Each terminal-valued path's ending for this case, decided up front.
    endings: dict[str, str | None] = {}
    for path, cm in model.cats.items():
        if cm.terminal:
            counts = cm.ending.get((terc, long))
            if not counts or sum(counts.values()) < K_MIN:
                counts = cm.ending.get((-1, -1), Counter())
            end = _draw(counts, rng)
            endings[path] = None if end in (None, _NONE) else end
    visits = []
    for idx, (day, form) in enumerate(zip(days, forms), start=1):
        values: dict[str, float] = {}
        for path, pm in model.numeric.items():
            if rng.random() >= model.presence.get(path, {}).get(form, 0.0):
                continue
            if pm.kind == "index":
                values[path] = float(idx + pm.index_k)
            elif pm.kind == "dayoffset" and f"o:{path}" in params:
                values[path] = float(round(params[f"o:{path}"] + day))
            elif pm.kind == "varying" and f"b:{path}" in params:
                v = params[f"b:{path}"] + params.get(f"s:{path}", 0.0) * day + nrng.normal(0.0, pm.resid_sd)
                values[path] = _round_clamp(v, pm)
            elif f"c:{path}" in params:
                values[path] = _round_clamp(params[f"c:{path}"], pm)
        dates: dict[str, int] = {}
        for path, pm in model.dates.items():
            if rng.random() >= model.presence.get(path, {}).get(form, 0.0):
                continue
            if f"dd:{path}" in params:
                dates[path] = int(round(params[f"dd:{path}"] + day))
            elif f"d:{path}" in params:
                dates[path] = int(round(params[f"d:{path}"]))
        cats: dict[str, str] = {}
        for path, cm in model.cats.items():
            if idx == n and endings.get(path):
                cats[path] = endings[path]
                continue
            if rng.random() >= model.presence.get(path, {}).get(form, 0.0):
                continue
            if not cm.varying:
                if path not in const_cat:
                    const_cat[path] = _draw(_backoff(cm, terc, None), rng)
                val = const_cat[path]
            else:
                val = _draw(_backoff(cm, terc, prev_cat.get(path)), rng)
                prev_cat[path] = val
            if val is not None:
                cats[path] = val
        visit: dict[str, Any] = {"day": day, "values": values}
        if dates:
            visit["dates"] = dates
        if cats:
            visit["cats"] = cats
        if form:
            visit["form"] = form
        visits.append(visit)
    shifts = [d for d in range(-START_SMOOTH_DAYS, START_SMOOTH_DAYS + 1) if d != 0]
    start = dt.date.fromordinal(start_ordinal + rng.choice(shifts))
    return {"owner": owner, "start_date": start.isoformat(), "visits": visits}


def _distance(a: dict, b: dict) -> float | None:
    """Mean relative difference over the numbers two same-shaped cases share, or None."""
    diffs = []
    for av, bv in zip(a["visits"], b["visits"]):
        for path, x in (av.get("values") or {}).items():
            y = (bv.get("values") or {}).get(path)
            if y is not None:
                diffs.append(abs(x - y) / max(abs(y), 1e-9))
    return float(np.mean(diffs)) if diffs else None


class _Gate:
    """Distance-to-closest-record: a sampled case is ON a real case when it is within
    ``_GATE_REL_TOL`` of one AND closer to it than a quarter (``_GATE_NN_SHARE``) of
    that real case's own distance to its nearest real neighbour. The second condition
    is what makes "close" mean "identifying": in a tight population (every baby
    within 2% of the next) being near some real case is what a faithful model must
    do, and says nothing about any one of them."""

    def __init__(self, real: list[dict]):
        self.by_shape: dict[tuple, list[tuple[dict, float]]] = defaultdict(list)
        grouped: dict[tuple, list[dict]] = defaultdict(list)
        for s in real:
            if len(s["visits"]) >= _GATE_MIN_VISITS:
                grouped[tuple(int(v["day"]) for v in s["visits"])].append(s)
        for shape, cases in grouped.items():
            for i, r in enumerate(cases):
                others = [d for j, o in enumerate(cases) if j != i and (d := _distance(r, o)) is not None]
                self.by_shape[shape].append((r, min(others) if others else math.inf))

    def too_close(self, case: dict) -> bool:
        if len(case["visits"]) < _GATE_MIN_VISITS:
            return False
        for real, nn in self.by_shape.get(tuple(v["day"] for v in case["visits"]), []):
            d = _distance(case, real)
            if d is not None and d <= _GATE_REL_TOL and d < _GATE_NN_SHARE * nn:
                return True
        return False


def synthesize_case_pool(
    pool: list[dict[str, Any]], *, seed: int, frozen_paths: set[str] | frozenset = frozenset()
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fit models to the real ``pool`` and return ``(new_pool, report)``. See the module doc."""
    real = [dict(s, visits=list(s.get("visits") or [])) for s in pool if s.get("visits")]
    if not real:
        return [], {"real_cases": 0}
    model = fit_case_model(real, frozen_paths=frozen_paths)
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    gate = _Gate(real)

    out: list[dict[str, Any]] = []
    redrawn = 0
    for owner in sorted(model.owners):
        starts = model.owner_starts[owner]
        for n in model.owners[owner]:
            for _ in range(_GATE_MAX_TRIES):
                case = _sample_case(model, owner, n, rng.choice(starts), rng, nrng)
                if not gate.too_close(case):
                    break
                redrawn += 1
            else:
                continue  # never emit a case the gate kept refusing
            out.append(case)
    report = {**model.report, "synthetic_cases": len(out), "redrawn_by_privacy_gate": redrawn}
    return out, report


def nearest_real_case_report(synthetic: list[dict], real: list[dict]) -> dict[str, Any]:
    """For tests and audits: how many synthetic cases (3+ visits) sit on a real case."""
    real = [{**s, "visits": sorted(s["visits"], key=lambda v: v["day"])} for s in real]
    gate = _Gate(real)
    hits = sum(1 for case in synthetic if gate.too_close(case))
    # Stricter, gate-independent: a sampled case whose every number equals a real
    # case's (same days, same values) -- what a copied case looks like.
    real_keys = {tuple((v["day"], tuple(sorted((v.get("values") or {}).items()))) for v in s["visits"]) for s in real}
    copies = sum(
        1
        for case in synthetic
        if len(case["visits"]) >= 2
        and tuple((v["day"], tuple(sorted((v.get("values") or {}).items()))) for v in case["visits"]) in real_keys
    )
    return {"cases": len(synthetic), "on_a_real_case": hits, "exact_copies_of_a_real_case": copies}

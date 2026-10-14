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
* **What kind of case it is.** A case's whole form sequence is drawn from the source's
  cases of the same length (a sequence seen in fewer than ``K_MIN`` cases is built
  form by form from the rarer sequences' own chain), and its answers are drawn from
  cases of the same SHAPE -- the forms it is made of. So a child followed up was
  enrolled by its own Screening, exactly as often as in the source; before this, a
  long case could be a Screening repeated, and a followed-up child's Screening was
  drawn with no regard to the follow-ups (RUTF clone 10113: 81 of 147 followed
  children "not enrolled"). Each field appears with the probability its form records it.
* **The source's window.** No sampled visit falls before the source's first visit day
  or after its last: a case is moved (and, if longer than the window, squeezed) to
  fit. Without it, start smoothing and sampled gaps put 10113's visits up to five
  weeks past the source's last day -- visits that had not happened yet.

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
from .entities import _time_varying_paths, place_in_window

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
#: Field copies: b entered as a copy of a in at least this share of cases is modelled.
_COPY_MIN_SHARE = 0.05


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
    # How much a reading's departure from its case's trend carries into the next one
    # (lag-1 correlation of the source's residuals, 0..0.95). 0 is independent noise
    # around the trend; near 1 a series wanders step by step. Learned per field, because
    # sources differ: an independent draw where the source's errors carry made
    # impossible steps between close visits, and a random walk where they do not blurs
    # each case's slope (see _ar_coefficient).
    resid_ar: float = 0.0


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
    # The same counts again, by the case's SHAPE (the forms it is made of, see _shape)
    # within its tercile, and by shape alone (tercile -1). An answer depends on what
    # kind of case it sits in: a child screened and followed up was enrolled by its
    # Screening, one screened only mostly was not. Drawn with no regard to shape, a
    # clone followed up children its own Screening had turned away. Order tried:
    # (shape, tercile), shape, tercile, everything -- so a programme whose cases all
    # share one shape (KMC) draws exactly as by tercile alone.
    first_by_shape: dict[tuple[tuple, int], Counter] = field(default_factory=dict)
    trans_by_shape: dict[tuple[tuple, int, str], Counter] = field(default_factory=dict)
    ending_by_shape: dict[tuple[tuple, int, int], Counter] = field(default_factory=dict)


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
    # n -> whole form sequences of cases that long, kept only when seen in K_MIN cases;
    # the rest is a ``None`` share, built form by form from the chain the rarer
    # sequences make (rare_first / rare_trans), so a rare kind of case keeps its kind.
    form_seqs: dict[int, Counter]
    rare_first: Counter
    rare_trans: dict[str, Counter]
    presence: dict[str, dict[str | None, float]]  # path -> form -> P(recorded)
    numeric: dict[str, _PathModel]
    dates: dict[str, _PathModel]
    cats: dict[str, _CatModel]
    driver: str | None
    driver_cuts: tuple[float, ...]
    median_n: float = 0.0
    # The source's first and last visit day (date ordinals). No sampled visit falls
    # outside it: a source never has a visit after its last day, and a clone dated
    # weeks past it reads as visits that have not happened yet.
    window: tuple[int, int] | None = None
    report: dict[str, Any] = field(default_factory=dict)
    # Every real case's first-visit day (ordinals), which no sampled case may start on.
    real_starts: frozenset[int] = frozenset()
    # n -> (mean gap, visit days) of the source's cases that long, when K_MIN or more
    # are: a case's whole schedule is drawn from these (see _sample_days).
    day_patterns: dict[int, list[tuple[float, tuple[int, ...]]]] = field(default_factory=dict)
    # (path a, path b) -> {persona or "*": share of cases where b was entered as a copy of a}.
    copies: dict[tuple[str, str], dict[str, float]] = field(default_factory=dict)


def _decimals(value: float) -> int:
    text = repr(float(value))
    if "e" in text or "." not in text:
        return 0 if float(value).is_integer() else 6
    return min(len(text.split(".")[1].rstrip("0")), 6)


def _robust_sd(values: list[float]) -> float:
    """The spread of the residuals, unmoved by a handful of wild ones.

    A plain standard deviation let a few real data-entry slips (2,500 g typed as
    25,000) set the noise on EVERY synthetic weighing: the EHA cohort profiled on
    2026-10-13 came back with 72% of its weights outside 800-5,000 g, half far too
    light and half far too heavy, around a correct median. The scaled median
    absolute deviation is the standard deviation for clean, normal residuals and
    ignores the slips.
    """
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return 0.0
    mad = float(np.median(np.abs(arr - np.median(arr))))
    return 1.4826 * mad


def _fit_line(points: list[tuple[float, float]]) -> tuple[float, float, list[float]]:
    xs = np.array([p[0] for p in points], dtype=float)
    ys = np.array([p[1] for p in points], dtype=float)
    if len(set(xs.tolist())) < 2:
        return float(ys[0]), 0.0, []
    slope, intercept = np.polyfit(xs, ys, 1)
    resid = (ys - (intercept + slope * xs)).tolist()
    return float(intercept), float(slope), resid


def _ar_coefficient(pairs: list[tuple[float, float]], sd: float) -> float:
    """Lag-1 correlation of consecutive residuals, the slips clipped first, kept in 0..0.95.

    Clipped at three robust SDs so a few data-entry slips cannot decide it (the same
    reason resid_sd is robust). Negative values -- a residual followed by its opposite,
    what a straight line fitted to a curve leaves -- are read as 0, independent noise.
    """
    if sd <= 0 or len(pairs) < K_MIN:
        return 0.0
    a = np.clip(np.array(pairs, dtype=float), -3 * sd, 3 * sd)
    if a[:, 0].std() == 0 or a[:, 1].std() == 0:
        return 0.0
    r = float(np.corrcoef(a[:, 0], a[:, 1])[0, 1])
    return 0.0 if not np.isfinite(r) else min(max(r, 0.0), 0.95)


def _fit_copies(
    case_consts: list[tuple[str, dict[str, float]]], numeric: dict[str, _PathModel]
) -> dict[tuple[str, str], dict[str, float]]:
    """Per-case constants entered as a copy of another (enrolment weight = birth weight).

    Drawn independently, two such fields match by chance almost never; in the source
    one in three EHA babies has an enrolment weight copied from the birth weight, which
    the birth-copy rate counts. Kept per persona, as copying is a worker's habit.
    """
    const_paths = sorted(p for p, m in numeric.items() if m.kind == "const")
    together: Counter = Counter()
    copied: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for owner, consts in case_consts:
        present = [p for p in const_paths if p in consts]
        for i, a in enumerate(present):
            for b in present[i + 1 :]:
                together[(a, b)] += 1
                c = copied[(a, b)][owner]
                c[0] += 1
                c[1] += int(abs(consts[a] - consts[b]) < 1)
    out: dict[tuple[str, str], dict[str, float]] = {}
    for pair, n in together.items():
        by_owner = copied[pair]
        k = sum(c[1] for c in by_owner.values())
        if n < K_MIN or k < K_MIN or k / n < _COPY_MIN_SHARE or k / n > 0.95:
            # Over 95%: the two are the same field under two names, which the
            # copula already keeps together; nothing to add.
            continue
        shares = {"*": k / n}
        for owner, (m, j) in by_owner.items():
            if m >= K_MIN:
                shares[owner] = j / m
        out[pair] = shares
    return out


def _sample_days(model: CaseModel, n: int, mean_gap: float, nrng: np.random.Generator) -> list[int] | None:
    """A whole visit schedule from the source's cases of this length, or None.

    Real follow-up runs to a protocol: a KMC case is seen until about day 28, so its
    schedule's total length is tight even when single gaps are not. Drawing each gap on
    its own (gap_ratios) kept the gaps' spread but lost that, and a clone's cases ended
    early or ran long -- "lost by day 28" read 46% against the source's 19%. The schedule
    taken is one whose mean gap is near the case's sampled mean gap (so the gap keeps its
    place in the copula), and every visit between the first and the last moves by up to
    a day so no source schedule is reproduced exactly. The last stays put: the length of
    follow-up is what the outcome indicators measure (seen at day 28 or lost), and the
    case's start date is jittered on its own.
    """
    pats = model.day_patterns.get(n)
    if not pats:
        return None
    gaps = np.array([g for g, _ in pats])
    i = int(np.searchsorted(gaps, mean_gap))
    lo, hi = max(0, i - 3), min(len(pats), i + 4)
    _, days = pats[int(nrng.integers(lo, hi))]
    out = [0]
    for d in days[1:-1]:
        out.append(min(max(out[-1], int(d) + int(nrng.integers(-1, 2))), int(days[-1])))
    if len(days) > 1:
        out.append(int(days[-1]))
    return out


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
    window = (
        min(min(starts) for starts in owner_starts.values()),
        max(dt.date.fromisoformat(s["start_date"]).toordinal() + int(s["visits"][-1]["day"]) for s in series_list),
    )

    # Per-case parameter rows: column -> value.
    rows: list[dict[str, float]] = []
    gap_ratios: dict[int, list[float]] = defaultdict(list)
    form_first: Counter = Counter()
    form_trans: dict[str, Counter] = defaultdict(Counter)
    form_seq_counts: dict[int, Counter] = defaultdict(Counter)
    form_seen: Counter = Counter()
    path_in_form: dict[str, Counter] = defaultdict(Counter)

    num_obs: dict[str, list[float]] = defaultdict(list)
    num_cases: Counter = Counter()
    date_obs: dict[str, list[float]] = defaultdict(list)
    date_cases: Counter = Counter()
    resid: dict[str, list[float]] = defaultdict(list)
    resid_pairs: dict[str, list[tuple[float, float]]] = defaultdict(list)
    day_patterns: dict[int, list[tuple[float, tuple[int, ...]]]] = defaultdict(list)
    case_consts: list[tuple[str, dict[str, float]]] = []
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
            day_patterns[len(days)].append((row["mean_gap"], tuple(d - days[0] for d in days)))
            for i, g in enumerate(gaps, start=1):
                gap_ratios[_bucket(i)].append(g / mean_gap if mean_gap > 1e-9 else 0.0)
        forms = [v.get("form") for v in visits]
        form_seq_counts[len(forms)][tuple(forms)] += 1
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
                resid_pairs[path].extend(zip(res, res[1:]))
            else:
                row[f"c:{path}"] = pts[0][2]
        case_consts.append((s["owner"], {p: pts[0][2] for p, pts in per_path.items() if p not in time_varying}))

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
            model.resid_sd = _robust_sd(resid[path])
        if kind == "varying" and len(resid_pairs[path]) >= K_MIN:
            model.resid_ar = _ar_coefficient(resid_pairs[path], model.resid_sd)
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

    rare_first: Counter = Counter()
    rare_trans: dict[str, Counter] = defaultdict(Counter)
    for seqs in form_seq_counts.values():
        for seq, c in seqs.items():
            if c >= K_MIN:
                continue
            if seq and seq[0]:
                rare_first[seq[0]] += c
            for a, b in zip(seq, seq[1:]):
                if a and b:
                    rare_trans[a][b] += c

    presence = {}
    for path, by_form in path_in_form.items():
        presence[path] = {f: min(1.0, by_form[f] / form_seen[f]) for f in by_form if form_seen[f]}

    return CaseModel(
        real_starts=frozenset(d for starts in owner_starts.values() for d in starts),
        day_patterns={n: sorted(pats) for n, pats in day_patterns.items() if len(pats) >= K_MIN},
        copies=_fit_copies(case_consts, numeric),
        owners=dict(owners),
        owner_starts=dict(owner_starts),
        columns=columns,
        margins=margins,
        corr=corr,
        gap_ratios={b: np.array(v) for b, v in gap_ratios.items() if len(v) >= K_MIN} or {1: np.array([1.0])},
        form_first=form_first,
        form_trans=dict(form_trans),
        form_seqs={n: _kept_sequences(seqs) for n, seqs in form_seq_counts.items()},
        rare_first=rare_first,
        rare_trans=dict(rare_trans),
        presence=presence,
        numeric=numeric,
        dates=dates,
        cats=cats,
        driver=driver,
        driver_cuts=cuts,
        median_n=float(np.median([len(s["visits"]) for s in series_list])),
        window=window,
        report=report,
    )


def _kept_sequences(seqs: Counter) -> Counter:
    """The form sequences seen in K_MIN cases or more, plus their rarer siblings' share as
    ``None`` -- drawing ``None`` means "build this one form by form", so a rare kind of
    case (a child already in treatment when the window opened) keeps its share without
    any one rare sequence being carried."""
    kept = Counter({seq: c for seq, c in seqs.items() if c >= K_MIN})
    rest = sum(seqs.values()) - sum(kept.values())
    if rest:
        kept[None] = rest
    return kept


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


def _shape(forms) -> tuple:
    """The forms a case is made of, in the order they first appear: ``("Screening",)`` for a
    child screened only, ``("Screening", "Visit Form")`` for one screened and followed up."""
    seen: list = []
    for form in forms:
        if form and form not in seen:
            seen.append(form)
    return tuple(seen)


def _fit_cats(series_list, rows, driver, cuts, time_varying) -> tuple[dict[str, _CatModel], int]:
    cases_with: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    # (tercile, long, shape, seq, value at the case's LAST visit or None)
    seqs: dict[str, list[tuple[int, int, tuple, list[str], str | None]]] = defaultdict(list)
    # value -> [occurrences, occurrences at the case's last visit, cases], counted over
    # cases of TWO OR MORE visits only: in a one-visit case every value is "at the end",
    # which says nothing about whether the value ends a case.
    at_end: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    median_n = float(np.median([len(s["visits"]) for s in series_list]))
    # (tercile, long, shape, paths recorded) for EVERY case: a case that never records a
    # path did not end on it, which the ending odds must count (see below).
    case_keys: list[tuple[int, int, tuple, set[str]]] = []
    for ci, (s, row) in enumerate(zip(series_list, rows)):
        terc = _tercile(row[driver], cuts) if driver in row else 1
        long = int(len(s["visits"]) > median_n)
        shape = _shape(v.get("form") for v in s["visits"])
        per_path: dict[str, list[str]] = defaultdict(list)
        last = len(s["visits"]) - 1
        last_val: dict[str, str] = {}
        counted: set[tuple[str, str]] = set()
        for vi, v in enumerate(s["visits"]):
            for path, val in (v.get("cats") or {}).items():
                val = str(val)
                per_path[path].append(val)
                cases_with[path][val].add(ci)
                if vi == last:
                    last_val[path] = val
                if last >= 1:
                    tally = at_end[path][val]
                    tally[0] += 1
                    tally[1] += int(vi == last)
                    if (path, val) not in counted:
                        tally[2] += 1
                        counted.add((path, val))
        for path, seq in per_path.items():
            seqs[path].append((terc, long, shape, seq, last_val.get(path)))
        case_keys.append((terc, long, shape, set(per_path)))

    out: dict[str, _CatModel] = {}
    suppressed = 0
    for path, by_value in cases_with.items():
        allowed = {val for val, cases in by_value.items() if len(cases) >= K_MIN}
        suppressed += len(by_value) - len(allowed)
        if not allowed:
            continue
        varying = path in time_varying
        # A value ENDS a case when, in the cases long enough to tell, it is recorded on
        # the last visit (90%+ of its occurrences, in K_MIN+ cases) -- however common it
        # is. Discharge is most cases' outcome in some programmes (60% at one KMC
        # opportunity); capping an ending at half the cases sprinkled it mid-case there.
        terminal = (
            {
                val
                for val in allowed
                if at_end[path][val][2] >= K_MIN and at_end[path][val][1] >= 0.9 * at_end[path][val][0]
            }
            if varying
            else set()
        )
        first: dict[int, Counter] = defaultdict(Counter)
        trans: dict[tuple[int, str], Counter] = defaultdict(Counter)
        ending: dict[tuple[int, int], Counter] = defaultdict(Counter)
        first_by_shape: dict[tuple[tuple, int], Counter] = defaultdict(Counter)
        trans_by_shape: dict[tuple[tuple, int, str], Counter] = defaultdict(Counter)
        ending_by_shape: dict[tuple[tuple, int, int], Counter] = defaultdict(Counter)
        if terminal:
            # Cases that never recorded the path ended without it. Counting only the cases
            # that have it made a field recorded solely on a discharge (always
            # "discharged") end EVERY sampled case that way.
            for terc, long, shape, recorded in case_keys:
                if path not in recorded:
                    for key in ((terc, long), (-1, -1)):
                        ending[key][_NONE] += 1
                    for key in ((shape, terc, long), (shape, -1, long)):
                        ending_by_shape[key][_NONE] += 1
        for terc, long, shape, seq, last_val in seqs[path]:
            if terminal:
                end = last_val if last_val in terminal else _NONE
                ending[(terc, long)][end] += 1
                ending[(-1, -1)][end] += 1
                ending_by_shape[(shape, terc, long)][end] += 1
                ending_by_shape[(shape, -1, long)][end] += 1
            seq = [x for x in seq if x in allowed and x not in terminal]
            if not seq:
                continue
            first[terc][seq[0]] += 1
            first[-1][seq[0]] += 1
            first_by_shape[(shape, terc)][seq[0]] += 1
            first_by_shape[(shape, -1)][seq[0]] += 1
            for a, b in zip(seq, seq[1:]):
                trans[(terc, a)][b] += 1
                trans[(-1, a)][b] += 1
                trans_by_shape[(shape, terc, a)][b] += 1
                trans_by_shape[(shape, -1, a)][b] += 1
        out[path] = _CatModel(
            varying=varying,
            first=dict(first),
            trans=dict(trans),
            allowed=allowed,
            terminal=terminal,
            ending=dict(ending),
            first_by_shape=dict(first_by_shape),
            trans_by_shape=dict(trans_by_shape),
            ending_by_shape=dict(ending_by_shape),
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


def _draw_sequence(seqs: Counter | None, rng: random.Random) -> tuple | None:
    """One form sequence from ``seqs`` (see _kept_sequences), or None to build it form by form."""
    if not seqs:
        return None
    items = sorted(seqs.items(), key=lambda kv: repr(kv[0]))
    r = rng.random() * sum(c for _, c in items)
    for seq, c in items:
        r -= c
        if r <= 0:
            return seq
    return items[-1][0]


def _backoff(model: _CatModel, terc: int, prev: str | None, shape: tuple | None = None) -> Counter:
    if shape is not None:
        for t in (terc, -1):
            c = model.first_by_shape.get((shape, t)) if prev is None else model.trans_by_shape.get((shape, t, prev))
            if c and sum(c.values()) >= K_MIN:
                return c
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


def fit_days(days: list[int], length: int) -> list[int]:
    """A case's visit days, squeezed to span at most ``length`` days if they span more.

    Sampled gaps can add up to a case longer than the source ever saw (the source's
    window cut its cases off). Every day is scaled down, so order and relative spacing
    are kept and the last visit lands on the window's last day."""
    span = days[-1] if days else 0
    if span <= length or span <= 0:
        return days
    return [d * max(length, 0) // span for d in days]


def _round_clamp(value: float, pm: _PathModel) -> float:
    value = float(round(value)) if pm.whole else round(value, pm.places)
    return min(max(value, pm.lo), pm.hi)


def _sample_case(
    model: CaseModel, owner: str, n: int, start_ordinal: int, rng: random.Random, nrng: np.random.Generator
) -> dict[str, Any]:
    params = _sample_params(model, n, nrng)
    # Visit days: the case's mean gap times pooled per-position ratios.
    mean_gap = max(params.get("mean_gap", 0.0), 0.0)
    days = _sample_days(model, n, mean_gap, nrng) or [0]
    for i in range(len(days), n):
        ratios = model.gap_ratios.get(_bucket(i))
        if ratios is None:
            ratios = model.gap_ratios[max(model.gap_ratios)]
        gap = max(0, int(round(mean_gap * float(ratios[int(nrng.integers(len(ratios)))]))))
        days.append(days[-1] + gap)
    if model.window:
        days = fit_days(days, model.window[1] - model.window[0])
    # Forms: a whole sequence drawn from the source's cases of this length, so a long
    # case is a long case's kind (screened, then followed up), not a short case's
    # Screening repeated. A length whose sequences are too rare to keep falls back to
    # the position-by-position chain.
    seqs = model.form_seqs.get(n)
    seq = _draw_sequence(seqs, rng)
    if seq is not None:
        forms: list[str | None] = list(seq)
    else:
        # A rare sequence: build it from the rare sequences' own chain when that is what
        # was drawn, else (a length the source never had) from every case's chain.
        rare = bool(seqs) and bool(model.rare_first)
        first = model.rare_first if rare else model.form_first
        trans = model.rare_trans if rare else model.form_trans
        forms = []
        for i in range(n):
            if i == 0:
                forms.append(_draw(first, rng))
            else:
                prev = forms[-1]
                forms.append(_draw(trans.get(prev, Counter()), rng) or prev)
    shape = _shape(forms)

    driver_value = params.get(model.driver, params["n"]) if model.driver else params["n"]
    terc = _tercile(driver_value, model.driver_cuts)
    long = int(n > model.median_n)
    prev_cat: dict[str, str | None] = {}
    const_cat: dict[str, str | None] = {}
    # Each terminal-valued path's ending for this case, decided up front.
    endings: dict[str, str | None] = {}
    for path, cm in model.cats.items():
        # Only a case whose last form records the path can end on it: a child only
        # ever screened has no treatment outcome to end with.
        if cm.terminal and model.presence.get(path, {}).get(forms[-1], 0.0) > 0:
            for counts in (
                cm.ending_by_shape.get((shape, terc, long)),
                cm.ending_by_shape.get((shape, -1, long)),
                cm.ending.get((terc, long)),
                cm.ending.get((-1, -1), Counter()),
            ):
                if counts and sum(counts.values()) >= K_MIN:
                    break
            end = _draw(counts, rng)
            endings[path] = None if end in (None, _NONE) else end
    # Per-case constants copied where the source's workers copy one field into another
    # (see _fit_copies).
    for (a, b), shares in model.copies.items():
        if f"c:{a}" in params and f"c:{b}" in params and rng.random() < shares.get(owner, shares["*"]):
            params[f"c:{b}"] = params[f"c:{a}"]
    last_resid: dict[str, float] = {}
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
                # Departure from the case's trend, carried from the previous reading as
                # much as the source's carry (resid_ar); same spread whatever the carry.
                prev = last_resid.get(path)
                fresh = nrng.normal(0.0, pm.resid_sd)
                e = fresh if prev is None else pm.resid_ar * prev + math.sqrt(1 - pm.resid_ar**2) * fresh
                last_resid[path] = e
                v = params[f"b:{path}"] + params.get(f"s:{path}", 0.0) * day + e
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
                    const_cat[path] = _draw(_backoff(cm, terc, None, shape), rng)
                val = const_cat[path]
            else:
                val = _draw(_backoff(cm, terc, prev_cat.get(path), shape), rng)
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
    start = start_ordinal + rng.choice(shifts)
    if model.window:
        start = place_in_window(start, days[-1], *model.window)
        # Pinned to the window's edge, a case whose schedule is the length of a real one
        # can start on that real case's first day. Never ship a real first-visit date.
        real = model.real_starts
        if start in real:
            first, last = model.window
            for d in (1, -1, 2, -2, 3, -3):
                moved = start + d
                if moved not in real and first <= moved and moved + days[-1] <= last:
                    start = moved
                    break
    return {"owner": owner, "start_date": dt.date.fromordinal(start).isoformat(), "visits": visits}


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
    do, and says nothing about any one of them.

    Vectorised per visit-day shape. ``_distance`` one pair at a time was quadratic in
    pure Python -- every pair of real cases sharing a shape at fit time, then every
    real case of the shape for every sampled case -- and KMC's schedules are regular
    enough that one shape holds hundreds of cases: it was most of a profile's time
    (minutes for a 1,700-case opportunity). Each shape is now one matrix of its
    cases' numbers, (visit, path) per column with NaN where a case has none, and the
    same mean relative difference is computed for all of a shape's cases at once.
    ``_distance`` stays the definition; the tests hold the two equal."""

    # Rows per block of the pairwise computation, so a large shape never builds one
    # (n x n x columns) array at once.
    _BLOCK = 256

    def __init__(self, real: list[dict]):
        grouped: dict[tuple, list[dict]] = defaultdict(list)
        for s in real:
            if len(s["visits"]) >= _GATE_MIN_VISITS:
                grouped[tuple(int(v["day"]) for v in s["visits"])].append(s)
        # shape -> (column index, real-case matrix, |matrix| floored, nearest-neighbour distances)
        self._shapes: dict[tuple, tuple[dict, np.ndarray, np.ndarray, np.ndarray]] = {}
        for shape, cases in grouped.items():
            columns: dict[tuple[int, str], int] = {}
            for case in cases:
                for vi, v in enumerate(case["visits"]):
                    for path in v.get("values") or {}:
                        columns.setdefault((vi, path), len(columns))
            matrix = np.full((len(cases), len(columns)), np.nan)
            for ci, case in enumerate(cases):
                for vi, v in enumerate(case["visits"]):
                    for path, x in (v.get("values") or {}).items():
                        matrix[ci, columns[(vi, path)]] = x
            denom = np.maximum(np.abs(matrix), 1e-9)
            nn = np.full(len(cases), np.inf)
            for lo in range(0, len(cases), self._BLOCK):
                block = matrix[lo : lo + self._BLOCK]
                # d[i, j] = mean over shared columns of |a_i - b_j| / max(|b_j|, eps)
                d = self._mean_rel(block[:, None, :], matrix[None, :, :], denom[None, :, :])
                for i in range(block.shape[0]):
                    d[i, lo + i] = np.nan  # a case is not its own neighbour
                with np.errstate(all="ignore"):
                    row_min = np.nanmin(np.where(np.isnan(d), np.inf, d), axis=1)
                nn[lo : lo + block.shape[0]] = row_min
            self._shapes[shape] = (columns, matrix, denom, nn)

    @staticmethod
    def _mean_rel(a: np.ndarray, b: np.ndarray, denom: np.ndarray) -> np.ndarray:
        """Mean of |a - b| / denom over the last axis where both are present, NaN if none."""
        diff = np.abs(a - b) / denom
        shared = ~np.isnan(diff)
        count = shared.sum(axis=-1)
        total = np.where(shared, diff, 0.0).sum(axis=-1)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(count > 0, total / np.maximum(count, 1), np.nan)

    def too_close(self, case: dict) -> bool:
        if len(case["visits"]) < _GATE_MIN_VISITS:
            return False
        found = self._shapes.get(tuple(v["day"] for v in case["visits"]))
        if found is None:
            return False
        columns, matrix, denom, nn = found
        row = np.full(len(columns), np.nan)
        for vi, v in enumerate(case["visits"]):
            for path, x in (v.get("values") or {}).items():
                col = columns.get((vi, path))
                if col is not None:
                    row[col] = x
        d = self._mean_rel(row[None, :], matrix, denom)
        with np.errstate(invalid="ignore"):
            hit = (d <= _GATE_REL_TOL) & (d < _GATE_NN_SHARE * nn)
        return bool(np.any(hit))


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

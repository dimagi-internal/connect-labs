"""How a registry's report LOOKS: the display contract, declared as registry data.

The KMC reports hold their presentation in page code -- five hand-picked headline
tiles with hand-typed targets, a scorecard column list, "babies" in a dozen
strings, an organisation label table. That is why a second programme could not
have the KMC cascade without a second set of renders. The generic indicator
templates (`indicator_programme_report`, `indicator_opp_report`,
`indicator_worker_review`) read ALL of it from here instead, so a programme gets
the whole drill by authoring a registry.

Two levels, both optional, both defaulted so a registry that declares none of it
(the on-disk `visit_quality`, or any record saved before this existed) renders:

PER INDICATOR (`measures[].meta`)::

    label:      short label for a tile or a column header        default: title
    plain:      one plain-English sentence                         default: none
    headline:   true, or a 1-based position among the tiles       default: see below
    target:     the goal, in the units `bands` use (70 for 70%)   default: bands[0]
                                                                   for higher/lower
    order:      sort key within its category                       default: registry order
    scorecard:  whether it is a scorecard column                  default: see below
    credibility: a `deployment.settings` table that says which     default: none
                organisations record it credibly -- pools the
                headline over those and marks the rest

  Headline default: when no indicator declares `headline`, the first five
  indicators with `prominence: Top` (or the first five, if none declares a
  prominence). Scorecard default: when no indicator declares `scorecard`, every
  `prominence: Top` indicator (or every indicator, if none declares one).

PER REGISTRY (`display:` in the indicators document)::

    display:
      title: Kangaroo Mother Care programme        # report title; default: none
      entity: {name: baby, plural: babies}         # default: the model's entity
      worker: {name: worker, plural: workers}      # default: worker / workers
      organisation: {name: organisation, plural: organisations}
      categories: [Scale, Case mix, Follow-up]     # order; default: first appearance
      headline_count: 5                            # tiles when defaulted; default 5
      case_fields:                                 # the case table's columns
        - {field: reg_date, label: Registered, format: date}
        - {field: last_weight_g, label: Latest weight, format: count, unit: g}
      reading: {column: weight_g, label: Weight, unit: g}   # charted per case
      targets_note: 'Targets from the 2026 workplan'        # optional footnote

`resolve_display` fills every default so readers never branch on absence, and
`display_problems` is the save-time gate (`validation.validate_registry` calls it).
"""

from __future__ import annotations

import re
from typing import Any

HEADLINE_COUNT = 5
_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CASE_FIELD_FORMATS = ("date", "count", "number", "text")
_DISPLAY_KEYS = {
    "title",
    "entity",
    "worker",
    "organisation",
    "categories",
    "headline_count",
    "case_fields",
    "reading",
    "targets_note",
}
# Every per-indicator meta key this module reads, for the validator.
INDICATOR_DISPLAY_KEYS = ("label", "plain", "headline", "target", "order", "scorecard", "credibility")

# The case table when a registry names none: identity and activity, which every
# case index carries whatever the programme is.
DEFAULT_CASE_FIELDS = [
    {"field": "first_visit_date", "label": "First visit", "format": "date"},
    {"field": "last_visit_date", "label": "Last visit", "format": "date"},
    {"field": "total_visits", "label": "Visits", "format": "count"},
]


def _noun(value: Any, name: str, plural: str) -> dict[str, str]:
    value = value if isinstance(value, dict) else {}
    n = str(value.get("name") or name)
    return {"name": n, "plural": str(value.get("plural") or (plural if n == name else n + "s"))}


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def default_target(meta: dict[str, Any]) -> float | None:
    """The goal an indicator is graded toward, in its bands' units, when none is stated."""
    bands = meta.get("bands")
    if meta.get("direction") not in ("higher", "lower") or not isinstance(bands, list) or not bands:
        return None
    return float(bands[0]) if _is_num(bands[0]) else None


def indicator_display(measures: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """indicator id -> {label, plain, headline, target, order, scorecard, credibility}, defaults filled.

    `measures` is the registry's `measures` list; only those carrying `meta.indicator`
    count. `headline` resolves to an int position or None; `scorecard` to a bool.
    """
    inds = [m for m in measures or [] if isinstance(m, dict) and (m.get("meta") or {}).get("indicator")]
    metas = [(m, m["meta"]) for m in inds]
    any_prom = any(meta.get("prominence") for _m, meta in metas)
    any_headline = any(meta.get("headline") not in (None, False) for _m, meta in metas)
    any_scorecard = any("scorecard" in meta for _m, meta in metas)

    def top(meta):
        return (not any_prom) or meta.get("prominence") == "Top"

    out: dict[str, dict[str, Any]] = {}
    for pos, (m, meta) in enumerate(metas):
        target = meta.get("target")
        out[meta["indicator"]] = {
            "label": str(meta.get("label") or m.get("title") or meta["indicator"]),
            "plain": meta.get("plain"),
            "target": float(target) if _is_num(target) else default_target(meta),
            "target_declared": _is_num(target),
            "order": float(meta["order"]) if _is_num(meta.get("order")) else float(pos),
            "scorecard": bool(meta["scorecard"]) if any_scorecard and "scorecard" in meta else top(meta),
            "credibility": meta.get("credibility") or None,
            "_headline": meta.get("headline"),
            "_top": top(meta),
            "_pos": pos,
        }

    # Headline positions: declared (true = in order of appearance after numbered
    # ones), else the first N top indicators.
    if any_headline:
        chosen = [(i, d) for i, d in out.items() if d["_headline"] not in (None, False)]
        chosen.sort(key=lambda kv: (0, kv[1]["_headline"]) if _is_num(kv[1]["_headline"]) else (1, kv[1]["_pos"]))
    else:
        chosen = [(i, d) for i, d in out.items() if d["_top"]][:HEADLINE_COUNT]
    positions = {i: n + 1 for n, (i, _d) in enumerate(chosen)}
    for i, d in out.items():
        d["headline"] = positions.get(i)
        for k in ("_headline", "_top", "_pos"):
            d.pop(k)
    return out


def resolve_display(
    props_doc: dict[str, Any] | None,
    indicators_doc: dict[str, Any] | None,
    *,
    visits_pipeline: str | None = None,
    case_fields_available: list[str] | None = None,
) -> dict[str, Any]:
    """The registry's display block with every default filled. Never raises."""
    from connect_labs.semantic.model import resolve_model

    indicators_doc = indicators_doc or {}
    raw = indicators_doc.get("display") if isinstance(indicators_doc.get("display"), dict) else {}
    model = resolve_model(props_doc or {}, indicators_doc)

    measures = [m for m in indicators_doc.get("measures") or [] if (m.get("meta") or {}).get("indicator")]
    seen: list[str] = []
    for m in measures:
        cat = (m.get("meta") or {}).get("category") or "Other"
        if cat not in seen:
            seen.append(cat)
    declared = [str(c) for c in raw.get("categories") or [] if isinstance(c, str)]
    categories = declared + [c for c in seen if c not in declared]

    per = indicator_display(measures)
    headline_count = raw.get("headline_count")
    headline = sorted((i for i, d in per.items() if d["headline"]), key=lambda i: per[i]["headline"])
    if _is_num(headline_count):
        headline = headline[: int(headline_count)]

    case_fields = [
        {
            "field": str(f["field"]),
            "label": str(f.get("label") or f["field"]),
            "format": f.get("format") if f.get("format") in CASE_FIELD_FORMATS else "text",
            **({"unit": str(f["unit"])} if f.get("unit") else {}),
        }
        for f in raw.get("case_fields") or []
        if isinstance(f, dict) and isinstance(f.get("field"), str)
    ] or [dict(f) for f in DEFAULT_CASE_FIELDS]

    reading = raw.get("reading") if isinstance(raw.get("reading"), dict) else None
    if reading is None and model.value_column:
        reading = {"column": model.value_column}
    if reading:
        reading = {
            "column": str(reading.get("column") or model.value_column or ""),
            "label": str(reading.get("label") or reading.get("column") or "Reading"),
            **({"unit": str(reading["unit"])} if reading.get("unit") else {}),
        }
        if not reading["column"]:
            reading = None

    return {
        "title": raw.get("title") or None,
        "entity": {
            **_noun(raw.get("entity") or {"name": model.entity_name, "plural": model.entity_plural}, "case", "cases"),
            "key": model.key,
        },
        "worker": _noun(raw.get("worker"), "worker", "workers"),
        "organisation": _noun(raw.get("organisation"), "organisation", "organisations"),
        "categories": categories,
        "headline": headline,
        "indicators": per,
        "case_fields": case_fields,
        "reading": reading,
        "visits_pipeline": visits_pipeline,
        "targets_note": raw.get("targets_note") or None,
        # The registry's `defaults.min_denominator`: the floor a measure with none of
        # its own is graded against, and what an "n<..." cell must say.
        "min_denominator": model.min_denominator,
    }


def display_problems(indicators_doc: dict[str, Any] | None) -> list[str]:
    """Every reason the display block or display meta would mislead a reader."""
    problems: list[str] = []
    doc = indicators_doc or {}
    raw = doc.get("display")
    if raw is not None:
        if not isinstance(raw, dict):
            problems.append("display: must be a mapping")
            raw = {}
        unknown = sorted(set(raw) - _DISPLAY_KEYS)
        if unknown:
            problems.append(f"display: unknown key(s) {unknown}; expected {sorted(_DISPLAY_KEYS)}")
        for noun in ("entity", "worker", "organisation"):
            v = raw.get(noun)
            if v is not None and not (
                isinstance(v, dict) and isinstance(v.get("name"), str) and isinstance(v.get("plural", ""), str)
            ):
                problems.append(f"display.{noun}: must be {{name: <str>, plural: <str>}}")
        for key in ("title", "targets_note"):
            if raw.get(key) is not None and not isinstance(raw[key], str):
                problems.append(f"display.{key}: must be a string")
        cats = raw.get("categories")
        if cats is not None and not (isinstance(cats, list) and all(isinstance(c, str) for c in cats)):
            problems.append("display.categories: must be a list of category names")
        hc = raw.get("headline_count")
        if hc is not None and (not isinstance(hc, int) or isinstance(hc, bool) or not 1 <= hc <= 10):
            problems.append("display.headline_count: must be an integer from 1 to 10")
        fields = raw.get("case_fields")
        if fields is not None:
            if not isinstance(fields, list):
                problems.append("display.case_fields: must be a list of {field, label, format}")
            else:
                for i, f in enumerate(fields):
                    if not isinstance(f, dict) or not isinstance(f.get("field"), str) or not _FIELD.match(f["field"]):
                        problems.append(f"display.case_fields[{i}]: needs `field`, a column name")
                        continue
                    if f.get("format") is not None and f["format"] not in CASE_FIELD_FORMATS:
                        problems.append(
                            f"display.case_fields[{i}].format: {f['format']!r} is not one of "
                            f"{list(CASE_FIELD_FORMATS)}"
                        )
        reading = raw.get("reading")
        if reading is not None and not (
            isinstance(reading, dict) and isinstance(reading.get("column"), str) and _FIELD.match(reading["column"])
        ):
            problems.append("display.reading: must be {column: <column name>, label?, unit?}")

    positions: dict[int, str] = {}
    for m in doc.get("measures") or []:
        meta = (m or {}).get("meta") if isinstance(m, dict) else None
        if not meta or not meta.get("indicator"):
            continue
        ind = meta["indicator"]
        for key in ("label", "plain", "credibility"):
            if meta.get(key) is not None and not isinstance(meta[key], str):
                problems.append(f"{ind}: meta.{key} must be a string")
        h = meta.get("headline")
        if h is not None and not isinstance(h, bool) and not (isinstance(h, int) and h >= 1):
            problems.append(f"{ind}: meta.headline must be true/false or a position (1, 2, ...)")
        if isinstance(h, int) and not isinstance(h, bool):
            if h in positions:
                problems.append(f"{ind}: meta.headline position {h} is also taken by {positions[h]}")
            positions[h] = ind
        for key in ("target", "order"):
            if meta.get(key) is not None and not _is_num(meta[key]):
                problems.append(f"{ind}: meta.{key} must be a number")
        if meta.get("scorecard") is not None and not isinstance(meta["scorecard"], bool):
            problems.append(f"{ind}: meta.scorecard must be true or false")
        if _is_num(meta.get("target")) and meta.get("unit") == "%" and not 0 <= meta["target"] <= 100:
            problems.append(f"{ind}: meta.target is a percentage (0-100, like its bands), got {meta['target']!r}")
    return problems


def credibility_problems(indicators_doc: dict[str, Any], settings: dict[str, Any] | None) -> list[str]:
    """A `meta.credibility` naming a settings table the deployment does not declare."""
    settings = settings or {}
    problems = []
    for m in (indicators_doc or {}).get("measures") or []:
        meta = (m or {}).get("meta") or {}
        name = meta.get("credibility")
        if meta.get("indicator") and isinstance(name, str) and name not in settings:
            problems.append(
                f"{meta['indicator']}: meta.credibility names {name!r}, which deployment.settings does not declare"
            )
    return problems


def credibility_from_meta(indicators_doc: dict[str, Any] | None) -> dict[str, str]:
    """indicator -> settings table, from `meta.credibility`."""
    out = {}
    for m in (indicators_doc or {}).get("measures") or []:
        meta = (m or {}).get("meta") or {}
        if meta.get("indicator") and isinstance(meta.get("credibility"), str):
            out[meta["indicator"]] = meta["credibility"]
    return out

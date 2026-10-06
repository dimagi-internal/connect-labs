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
      entity: {name: baby, plural: babies,         # default: the model's entity
               label_field: entity_name,           # a case's human name; default: its id
               done_property: discharged}          # a bool property: the case's work is finished
      worker: {name: worker, plural: workers}      # default: worker / workers
      organisation: {name: organisation, plural: organisations}
      categories: [Scale, Case mix, Follow-up]     # order; default: first appearance
      headline_count: 5                            # tiles when defaulted; default 5
      case_fields:                                 # the case table's columns
        - {field: reg_date, label: Registered, format: date}
        - {field: last_weight_g, label: Latest weight, format: count, unit: g}
      reading: {column: weight_g, label: Weight, unit: g}   # charted per case
      visit_fields:                                # extra columns in one case's visit list
        - {field: weight_g, label: Weight, format: count, unit: g}
      visit_flags:                                 # per-visit review flags, shown by name
        - {column: repeat_counts_flag, label: Repeat count}   # flagged when 'yes'/true/1
        - {column: risk_level, label: High risk, value: high} # or when it equals `value`
        - {column: dup_flag, label: Repeat count,
           fields: [male_attendance, female_attendance],  # visit_fields it is about
           description: 'Counts exactly repeat the previous visit'}  # Flags tooltip
      targets_note: 'Targets from the 2026 workplan'        # optional footnote

`entity.label_field` names a case-index field (a column of the entity pipeline,
e.g. Connect's own `entity_name`) whose value is a case's human label: the case
table's first column and the worker review's case heading show it, falling back
to the id when a case has none. The builder adds it to the derived case index.

`entity.done_property` names a Layer-2 property of type `bool` (in the properties
document) that is true once a case's work is FINISHED -- a community that completed
its final step, a baby discharged -- so no further visit is due. The builder adds it
to the case index, and the renders' staleness rules (the red "no visit for over N
days" Last-visit cell, the worker review's open gap since the last visit) skip a
finished case, and a worker, opportunity or organisation whose cases are ALL
finished. Without it a programme whose cases end by design reads as neglected the
moment its cases end.

`visit_flags` is how an indicator that counts flagged visits (a repeat-count rate,
a location-review rate) points the reader at WHICH visits: the worker review marks
each visit that carries the flag. Without it the visit list can only show Connect's
own review flag, which is a different thing. A flag's optional `fields` names the
`visit_fields` it is about: on a flagged visit, each of those cells that equals the
previous visit's is marked (a repeat-count flag points at the repeated counts). Its
optional `description` is the sentence the Flags column's tooltip gives for it.

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
    "visit_fields",
    "visit_flags",
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


def _fields(raw: Any) -> list[dict[str, Any]]:
    return [
        {
            "field": str(f["field"]),
            "label": str(f.get("label") or f["field"]),
            "format": f.get("format") if f.get("format") in CASE_FIELD_FORMATS else "text",
            **({"unit": str(f["unit"])} if f.get("unit") else {}),
        }
        for f in raw or []
        if isinstance(f, dict) and isinstance(f.get("field"), str)
    ]


def _visit_flags(raw: Any) -> list[dict[str, Any]]:
    return [
        {
            "column": str(f["column"]),
            "label": str(f.get("label") or f["column"]),
            **({"value": f["value"]} if f.get("value") is not None else {}),
            **({"fields": [str(x) for x in f["fields"]]} if isinstance(f.get("fields"), list) else {}),
            **({"description": str(f["description"])} if f.get("description") else {}),
        }
        for f in raw or []
        if isinstance(f, dict) and isinstance(f.get("column"), str)
    ]


def _fields_problems(key: str, fields: Any, problems: list[str]) -> None:
    if fields is None:
        return
    if not isinstance(fields, list):
        problems.append(f"display.{key}: must be a list of {{field, label, format}}")
        return
    for i, f in enumerate(fields):
        if not isinstance(f, dict) or not isinstance(f.get("field"), str) or not _FIELD.match(f["field"]):
            problems.append(f"display.{key}[{i}]: needs `field`, a column name")
            continue
        if f.get("format") is not None and f["format"] not in CASE_FIELD_FORMATS:
            problems.append(f"display.{key}[{i}].format: {f['format']!r} is not one of {list(CASE_FIELD_FORMATS)}")


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

    case_fields = _fields(raw.get("case_fields")) or [dict(f) for f in DEFAULT_CASE_FIELDS]

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

    raw_entity = raw.get("entity") if isinstance(raw.get("entity"), dict) else {}
    # A block that only names the label field keeps the model's nouns.
    entity_noun = (
        raw_entity
        if raw_entity.get("name")
        else {"name": model.entity_name, "plural": raw_entity.get("plural") or model.entity_plural}
    )
    label_field = raw_entity.get("label_field")
    done_property = raw_entity.get("done_property")
    return {
        "title": raw.get("title") or None,
        "entity": {
            **_noun(entity_noun, "case", "cases"),
            "key": model.key,
            # Present only when declared, so a registry without one resolves as before.
            **({"label_field": label_field} if isinstance(label_field, str) and _FIELD.match(label_field) else {}),
            **(
                {"done_property": done_property}
                if isinstance(done_property, str) and _FIELD.match(done_property)
                else {}
            ),
        },
        "worker": _noun(raw.get("worker"), "worker", "workers"),
        "organisation": _noun(raw.get("organisation"), "organisation", "organisations"),
        "categories": categories,
        "headline": headline,
        "indicators": per,
        "case_fields": case_fields,
        "reading": reading,
        "visit_fields": _fields(raw.get("visit_fields")),
        "visit_flags": _visit_flags(raw.get("visit_flags")),
        "visits_pipeline": visits_pipeline,
        "targets_note": raw.get("targets_note") or None,
        # The registry's `defaults.min_denominator`: the floor a measure with none of
        # its own is graded against, and what an "n<..." cell must say.
        "min_denominator": model.min_denominator,
    }


def display_problems(indicators_doc: dict[str, Any] | None, props_doc: dict[str, Any] | None = None) -> list[str]:
    """Every reason the display block or display meta would mislead a reader.

    `props_doc` (the properties document) is what `entity.done_property` is checked
    against; without it only the key's shape is checked.
    """
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
            # The entity may name only its label / done fields; its nouns then default.
            name_optional = (
                noun == "entity"
                and isinstance(v, dict)
                and "name" not in v
                and ("label_field" in v or "done_property" in v)
            )
            if v is not None and not (
                isinstance(v, dict)
                and (isinstance(v.get("name"), str) or name_optional)
                and isinstance(v.get("plural", ""), str)
            ):
                problems.append(f"display.{noun}: must be {{name: <str>, plural: <str>}}")
        ent = raw.get("entity")
        if isinstance(ent, dict) and ent.get("label_field") is not None:
            lf = ent["label_field"]
            if not isinstance(lf, str) or not _FIELD.match(lf):
                problems.append("display.entity.label_field: must be a case-index field name, e.g. entity_name")
        if isinstance(ent, dict) and ent.get("done_property") is not None:
            problems.extend(done_property_problems(ent["done_property"], props_doc))
        for key in ("title", "targets_note"):
            if raw.get(key) is not None and not isinstance(raw[key], str):
                problems.append(f"display.{key}: must be a string")
        cats = raw.get("categories")
        if cats is not None and not (isinstance(cats, list) and all(isinstance(c, str) for c in cats)):
            problems.append("display.categories: must be a list of category names")
        hc = raw.get("headline_count")
        if hc is not None and (not isinstance(hc, int) or isinstance(hc, bool) or not 1 <= hc <= 10):
            problems.append("display.headline_count: must be an integer from 1 to 10")
        _fields_problems("case_fields", raw.get("case_fields"), problems)
        _fields_problems("visit_fields", raw.get("visit_fields"), problems)
        flags = raw.get("visit_flags")
        if flags is not None:
            if not isinstance(flags, list):
                problems.append("display.visit_flags: must be a list of {column, label, value?}")
            else:
                for i, f in enumerate(flags):
                    if (
                        not isinstance(f, dict)
                        or not isinstance(f.get("column"), str)
                        or not _FIELD.match(f["column"])
                    ):
                        problems.append(f"display.visit_flags[{i}]: needs `column`, a column name")
                    elif f.get("label") is not None and not isinstance(f["label"], str):
                        problems.append(f"display.visit_flags[{i}].label: must be a string")
                    elif f.get("value") is not None and not isinstance(f["value"], (str, int, float)):
                        problems.append(f"display.visit_flags[{i}].value: must be a string or a number")
                    elif f.get("fields") is not None and not (
                        isinstance(f["fields"], list)
                        and all(isinstance(x, str) and _FIELD.match(x) for x in f["fields"])
                    ):
                        problems.append(f"display.visit_flags[{i}].fields: must be a list of visit_fields names")
                    elif f.get("description") is not None and not isinstance(f["description"], str):
                        problems.append(f"display.visit_flags[{i}].description: must be a string")
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


def done_property_problems(name: Any, props_doc: dict[str, Any] | None) -> list[str]:
    """`display.entity.done_property` must name a bool property of the properties document."""
    key = "display.entity.done_property"
    if not isinstance(name, str) or not _FIELD.match(name):
        return [f"{key}: must be the name of a bool property, e.g. step7_done"]
    if props_doc is None:
        return []
    types = {
        str(p.get("name")): p.get("type")
        for p in (props_doc or {}).get("properties") or []
        if isinstance(p, dict) and p.get("name")
    }
    if name not in types:
        return [f"{key}: {name!r} is not a property in the properties document"]
    if types[name] != "bool":
        return [f"{key}: {name!r} is a {types[name] or 'untyped'} property; it must be type: bool"]
    return []


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

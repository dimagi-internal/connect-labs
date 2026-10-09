"""CASE STATES: a case's condition as of the report date, declared in the registry.

An indicator counts cases; a FLAG is a bool property; a STATE is a bool property a
person acts on about ONE case -- "this baby is growing well", "this weighing is
hard to believe" -- with what is needed to talk about it. It is the case-level
sibling of an indicator, and like one it is pure registry data: the rule is the
property's SQL (evaluated per case with ``:as_of``, so a saved run holds the state
as of its week), and its ``case_state:`` meta says how to present it:

    properties:
      - name: case_state_weight_check       # the case state's key (a coaching topic key)
        label: 'A weighing that is hard to believe'
        means: 'One weight recorded for this baby is hard to believe ...'
        type: bool
        sql: 'NOT weight_series_believable AND COALESCE(check_date >= ..., FALSE)'
        case_state:
          tone: check                       # celebrate | check | concern | urgent
          priority: 2                       # lower is more urgent; a case's state is
                                            # the true state with the lowest priority
          date: check_date                  # the date the evidence points to (recency, ordering)
          evidence: [step_change_g, step_days, step_from_date, step_to_date, step_rate]
          facts: 'Weight rose {step_change_g|signed} g in {step_days|days} ...'
          picture: {type: series_highlight_step, series: weight, ...}
          coach: {approach: '...', next_steps: '...', limits: '...'}

a registry may say what one line about a case reads (``about``, below), and the
per-visit series a case is read with:

    case_about: 'Birth weight {birth_weight_g|grams} g; registered {reg_date|day}; ...'


    case_series:
      - {name: weight, column: visit_weight_g, label: Weight, unit: g, format: grams,
         line: 'weight {value} g', missing: 'weight not recorded'}

``case_state_problems`` / ``case_series_problems`` are part of ``compiler.validate``.
``catalog`` is what a saved run stores (``caseStateCatalog``) and the run tools return;
``case_fields`` is what a case index needs to carry them; ``case_state``, ``facts``
and ``visit_lines`` read one case; ``case_visits`` reads its series. Nothing here
knows what a programme is.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from typing import Any

TONES = ("celebrate", "check", "concern", "urgent")

#: The picture types a state may name, and the keys each takes. Drawn by
#: ``workflow/coach_charts/case_types.py``; every word in a picture is registry data.
PICTURE_TYPES: dict[str, frozenset[str]] = {
    # The series climbing above a reference band, a star at the latest reading, a badge.
    "series_vs_reference": frozenset({"series", "title", "badge", "reference", "gain_label"}),
    # The series with one step ringed and labelled, dashed into and out of it, and a checklist.
    "series_highlight_step": frozenset({"series", "title", "highlight", "checklist_title", "checklist"}),
    # The series against a reference band, with a second series as bars underneath.
    "series_with_bars": frozenset({"series", "title", "reference", "bars", "bars_title"}),
    # A card: the labels a case column lists, why each matters, and what to do.
    "sign_card": frozenset({"title", "badge", "date", "signs", "sign_text", "why", "actions_title", "actions"}),
}

COACH_KEYS = ("approach", "next_steps", "limits")
CASE_STATE_KEYS = frozenset({"tone", "priority", "date", "evidence", "facts", "picture", "coach"})
SERIES_FORMATS = ("number", "grams", "hours", "text")

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)((?:\|[a-z0-9]+)*)\}")
FILTERS = ("int", "1dp", "abs", "signed", "date", "day", "days", "grams")


def _text_problem(value: Any, label: str, limit: int) -> list[str]:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        return [f"{label}: must be text of 1-{limit} characters"]
    return []


def _template_problems(text: Any, label: str, columns: frozenset[str] | None, limit: int = 600) -> list[str]:
    """A text template -- or a list of ``{when, text}`` choices, the first whose
    ``when`` column is set (true, non-empty) on the case being worded."""
    if isinstance(text, list):
        if not 1 <= len(text) <= 8:
            return [f"{label}: must list 1 to 8 {{when, text}} choices"]
        problems: list[str] = []
        for i, choice in enumerate(text):
            where = f"{label}[{i}]"
            if not isinstance(choice, dict):
                problems.append(f"{where}: must be a mapping of when and text")
                continue
            when = choice.get("when")
            if when is not None and (columns is not None and when not in columns):
                problems.append(f"{where}.when: {when!r} is not a column of this registry")
            problems += _template_problems(choice.get("text"), f"{where}.text", columns, limit)
        return problems
    problems = _text_problem(text, label, limit)
    if problems:
        return problems
    for m in _PLACEHOLDER.finditer(text):
        name, filters = m.group(1), [f for f in m.group(2).split("|") if f]
        if columns is not None and name not in columns:
            problems.append(f"{label}: {{{name}}} is not a column of this registry")
        problems += [f"{label}: |{f} is not a filter ({', '.join(FILTERS)})" for f in filters if f not in FILTERS]
    return problems


def _properties(props_doc: dict[str, Any]) -> list[dict]:
    return [p for p in props_doc.get("properties") or [] if isinstance(p, dict) and isinstance(p.get("name"), str)]


def series_names(props_doc: dict[str, Any]) -> set[str]:
    return {s["name"] for s in props_doc.get("case_series") or [] if isinstance(s, dict) and s.get("name")}


def _picture_problems(pic: Any, label: str, columns: frozenset[str], series: set[str]) -> list[str]:
    if not isinstance(pic, dict) or pic.get("type") not in PICTURE_TYPES:
        return [f"{label}: must be a mapping whose type is one of {', '.join(PICTURE_TYPES)}"]
    allowed = PICTURE_TYPES[pic["type"]] | {"type"}
    problems = [f"{label}.{k}: not a key of a {pic['type']} picture" for k in pic if k not in allowed]
    for key in ("series",):
        if key in pic and pic[key] not in series:
            problems.append(f"{label}.{key}: {pic[key]!r} is not one of this registry's case_series")
    bars = pic.get("bars")
    if bars is not None and bars not in series:
        problems.append(f"{label}.bars: {bars!r} is not one of this registry's case_series")
    for key in ("title", "badge", "checklist_title", "why", "actions_title", "bars_title"):
        if key in pic:
            problems += _template_problems(pic[key], f"{label}.{key}", columns, 300)
    for key in ("checklist", "actions"):
        if key in pic:
            items = pic[key]
            if not isinstance(items, list) or not 1 <= len(items) <= 8:
                problems.append(f"{label}.{key}: must list 1 to 8 steps")
            else:
                for i, item in enumerate(items):
                    problems += _text_problem(item, f"{label}.{key}[{i}]", 120)
    ref = pic.get("reference")
    if ref is not None:
        if not isinstance(ref, dict):
            problems.append(f"{label}.reference: must be a mapping of low, high (per 1000 per day) and label")
        else:
            for k in ("low", "high"):
                v = ref.get(k)
                if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 1000:
                    problems.append(f"{label}.reference.{k}: must be a number from 0 to 1000")
            problems += _text_problem(ref.get("label"), f"{label}.reference.label", 40)
    hl = pic.get("highlight")
    if hl is not None:
        if not isinstance(hl, dict):
            problems.append(f"{label}.highlight: must be a mapping of from, to and label")
        else:
            for k in ("from", "to"):
                if hl.get(k) not in columns:
                    problems.append(f"{label}.highlight.{k}: must name a date column of this registry")
            problems += _template_problems(hl.get("label"), f"{label}.highlight.label", columns, 60)
    for key in ("date", "signs"):
        if key in pic and pic[key] not in columns:
            problems.append(f"{label}.{key}: must name a column of this registry")
    st = pic.get("sign_text")
    if st is not None:
        if not isinstance(st, dict) or len(st) > 40:
            problems.append(f"{label}.sign_text: must map up to 40 labels to {{title, why}}")
        else:
            for k, v in st.items():
                if not isinstance(v, dict):
                    problems.append(f"{label}.sign_text.{k}: must be a mapping of title and why")
                    continue
                problems += _text_problem(v.get("title"), f"{label}.sign_text.{k}.title", 60)
                problems += _text_problem(v.get("why"), f"{label}.sign_text.{k}.why", 200)
    return problems


def case_state_problems(props_doc: dict[str, Any], columns: frozenset[str]) -> list[str]:
    """Every property's ``case_state:`` meta, checked: only on a bool property, known keys,
    evidence and placeholders naming real columns, a known picture. And ``case_about``."""
    problems: list[str] = []
    if props_doc.get("case_about") is not None:
        problems += _template_problems(props_doc["case_about"], "case_about", columns, 300)
    series = series_names(props_doc)
    priorities: dict[int, str] = {}
    for p in _properties(props_doc):
        meta = p.get("case_state")
        if meta is None:
            continue
        label = f"properties.{p['name']}.case_state"
        if not isinstance(meta, dict):
            problems.append(f"{label}: must be a mapping")
            continue
        if p.get("type") != "bool":
            problems.append(f"{label}: a case state is a bool property (type: bool)")
        problems += [
            f"{label}.{k}: not a case_state key ({', '.join(sorted(CASE_STATE_KEYS))})"
            for k in meta
            if k not in CASE_STATE_KEYS
        ]
        if meta.get("tone") not in TONES:
            problems.append(f"{label}.tone: must be one of {', '.join(TONES)}")
        prio = meta.get("priority")
        if not isinstance(prio, int) or isinstance(prio, bool) or not 0 <= prio <= 100:
            problems.append(f"{label}.priority: must be a whole number from 0 to 100")
        elif prio in priorities:
            problems.append(f"{label}.priority: {prio} is also {priorities[prio]}'s -- a case's state must be unique")
        else:
            priorities[prio] = p["name"]
        if meta.get("date") is not None and meta["date"] not in columns:
            problems.append(f"{label}.date: {meta['date']!r} is not a column of this registry")
        evidence = meta.get("evidence") or []
        if not isinstance(evidence, list) or len(evidence) > 20:
            problems.append(f"{label}.evidence: must list up to 20 column names")
        else:
            problems += [
                f"{label}.evidence: {e!r} is not a column of this registry" for e in evidence if e not in columns
            ]
        if "facts" in meta:
            problems += _template_problems(meta["facts"], f"{label}.facts", columns)
        if "picture" in meta:
            problems += _picture_problems(meta["picture"], f"{label}.picture", columns, series)
        coach = meta.get("coach")
        if coach is not None:
            if not isinstance(coach, dict):
                problems.append(f"{label}.coach: must be a mapping of {', '.join(COACH_KEYS)}")
            else:
                problems += [f"{label}.coach.{k}: not a coach key" for k in coach if k not in COACH_KEYS]
                for k in COACH_KEYS:
                    problems += _text_problem(coach.get(k), f"{label}.coach.{k}", 1500)
    return problems


def case_series_problems(props_doc: dict[str, Any]) -> list[str]:
    raw = props_doc.get("case_series")
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 12:
        return ["case_series: must list up to 12 series"]
    problems: list[str] = []
    seen: set[str] = set()
    for i, s in enumerate(raw):
        label = f"case_series[{i}]"
        if not isinstance(s, dict):
            problems.append(f"{label}: must be a mapping")
            continue
        for k in ("name", "column"):
            if not isinstance(s.get(k), str) or not _IDENT.match(s[k]):
                problems.append(f"{label}.{k}: must be a single column name")
        if s.get("name") in seen:
            problems.append(f"{label}.name: declared twice")
        seen.add(s.get("name"))
        if s.get("format", "number") not in SERIES_FORMATS:
            problems.append(f"{label}.format: must be one of {', '.join(SERIES_FORMATS)}")
        problems += _text_problem(s.get("label"), f"{label}.label", 40)
        if "line" in s:
            line = s["line"]
            if not isinstance(line, str) or "{value}" not in line or len(line) > 120:
                problems.append(f"{label}.line: must be text up to 120 characters containing {{value}}")
        for k in ("missing", "unit", "empty"):
            if k in s:
                problems += _text_problem(s[k], f"{label}.{k}", 80)
    return problems


# ---------------------------------------------------------------------------
# Reading states
# ---------------------------------------------------------------------------


def catalog(props_doc: dict[str, Any]) -> list[dict]:
    """The registry's case states, most urgent first: everything a reader needs to
    present one -- the property's own label and plain meaning, and its ``case_state:``
    meta."""
    out = []
    for p in _properties(props_doc):
        meta = p.get("case_state")
        if not isinstance(meta, dict):
            continue
        out.append(
            {
                "name": p["name"],
                "label": p.get("label") or p["name"],
                "means": p.get("means") or "",
                "tone": meta.get("tone"),
                "priority": meta.get("priority"),
                "date": meta.get("date"),
                "evidence": list(meta.get("evidence") or []),
                "facts": meta.get("facts") or "",
                "picture": dict(meta.get("picture") or {}),
                "coach": dict(meta.get("coach") or {}),
            }
        )
    out.sort(key=lambda s: (s["priority"] if isinstance(s["priority"], int) else 999, s["name"]))
    return out


def case_fields(props_doc: dict[str, Any]) -> dict[str, str]:
    """``{output: column}`` a case index needs to carry every state: each state, its
    date and its evidence (the picture's and facts' columns too), under their own names."""
    names: list[str] = _template_columns(props_doc.get("case_about"))
    for s in catalog(props_doc):
        names.append(s["name"])
        if s["date"]:
            names.append(s["date"])
        names += s["evidence"]
        pic = s["picture"]
        names += [c for c in (pic.get("date"), pic.get("signs")) if c]
        hl = pic.get("highlight") or {}
        names += [c for c in (hl.get("from"), hl.get("to")) if c]
        texts = [s["facts"], *_picture_texts(s)]
        names += [c for t in texts for c in _template_columns(t)]
    return {n: n for n in dict.fromkeys(names)}


def _template_columns(text: Any) -> list[str]:
    if isinstance(text, list):
        out = []
        for choice in text:
            if isinstance(choice, dict):
                out += [choice["when"]] if choice.get("when") else []
                out += _template_columns(choice.get("text"))
        return out
    return [m.group(1) for m in _PLACEHOLDER.finditer(text or "")] if isinstance(text, str) else []


def _picture_texts(state: dict) -> list:
    pic = state.get("picture") or {}
    texts = [pic[k] for k in ("title", "badge", "checklist_title", "why", "actions_title", "bars_title") if pic.get(k)]
    hl = pic.get("highlight") or {}
    if hl.get("label"):
        texts.append(hl["label"])
    return texts


def truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "t", "1", "yes")
    return bool(value)


def true_case_states(case: dict, states: list[dict]) -> list[dict]:
    """The states true for ``case`` (a case-index row), most urgent first."""
    return [s for s in states if truthy(case.get(s["name"]))]


def case_state(case: dict, states: list[dict], wanted: str | None = None) -> dict | None:
    """The case's state -- ``wanted`` if it holds -- or None."""
    true = true_case_states(case, states)
    if wanted:
        return next((s for s in true if s["name"] == wanted), None)
    return true[0] if true else None


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------


def _as_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _as_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def day_long(d: dt.date) -> str:
    """``17 May 2026``."""
    return f"{d.day} {d.strftime('%b')} {d.year}"


def day_short(d: dt.date) -> str:
    """``17 May``."""
    return f"{d.day} {d.strftime('%b')}"


def format_value(value: Any, filters: list[str]) -> str:
    """A value as a sentence words it, through ``filters``; ``not recorded`` when absent."""
    if value is None or value == "":
        return "not recorded"
    for f in filters:
        if f in ("date", "day"):
            d = _as_date(value)
            if d is None:
                return "not recorded"
            return day_short(d) if f == "date" else day_long(d)
        n = _as_number(value)
        if n is None:
            break
        if f == "abs":
            value = abs(n)
        elif f == "signed":
            return ("+" if n > 0 else "−" if n < 0 else "") + f"{abs(round(n)):,}"
        elif f in ("int", "grams"):
            return f"{int(round(n)):,}"
        elif f == "1dp":
            return f"{n:.1f}"
        elif f == "days":
            k = int(round(n))
            return f"{k} day" if k == 1 else f"{k} days"
    n = _as_number(value)
    if n is not None and not isinstance(value, str):
        if abs(n - round(n)) < 1e-9:
            return f"{int(round(n)):,}"
        return f"{n:.1f}" if abs(n) < 10 else f"{int(round(n)):,}"
    d = _as_date(value) if isinstance(value, (dt.date, dt.datetime)) else None
    return day_short(d) if d else str(value)


def choose(template: Any, case: dict) -> str:
    """The template itself, or -- for a list of ``{when, text}`` -- the first choice
    whose ``when`` column is set on the case (a choice with no ``when`` always is)."""
    if isinstance(template, list):
        for choice in template:
            if isinstance(choice, dict) and (not choice.get("when") or _is_set(case.get(choice["when"]))):
                return str(choice.get("text") or "")
        return ""
    return template or ""


def _is_set(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().lower() not in ("false", "f", "0")
    return value is not None and value is not False


def fill(template: Any, case: dict) -> str:
    """``template`` (chosen for the case, see ``choose``) with each ``{column|filters}``
    replaced from ``case``."""

    def repl(m: re.Match) -> str:
        return format_value(case.get(m.group(1)), [f for f in m.group(2).split("|") if f])

    return _PLACEHOLDER.sub(repl, choose(template, case))


def about(props_doc: dict[str, Any], case: dict) -> str:
    """The registry's one line about a case (``case_about``), filled from its row."""
    return fill(props_doc.get("case_about") or "", case).strip()


def facts(state: dict, case: dict) -> str:
    """The state's facts sentence, filled from the case's evidence as of the run."""
    return fill(state.get("facts") or "", case).strip()


# ---------------------------------------------------------------------------
# A case's visit series
# ---------------------------------------------------------------------------


def case_series(props_doc: dict[str, Any]) -> list[dict]:
    return [dict(s) for s in props_doc.get("case_series") or [] if isinstance(s, dict) and s.get("name")]


def series_value(spec: dict, value: Any) -> str | None:
    """One visit's value as its line words it, or None when absent."""
    if value is None or value == "":
        return None
    fmt = spec.get("format", "number")
    if fmt == "text":
        return str(value)
    n = _as_number(value)
    if n is None:
        return str(value)
    if fmt == "grams":
        return f"{int(round(n)):,}"
    return f"{int(round(n))}" if abs(n - round(n)) < 0.05 else f"{n:.1f}"


def visit_line(series: list[dict], visit: dict) -> str:
    """``- 17 May 2026: weight 1,350 g; ...`` -- one visit, every series in order."""
    parts = []
    for s in series:
        shown = series_value(s, visit.get(s["name"]))
        if shown is None:
            parts.append(s.get("missing") or f"{s['label'].lower()} not recorded")
        else:
            parts.append((s.get("line") or "{value}").replace("{value}", shown))
    d = _as_date(visit.get("visit_date"))
    return f"- {day_long(d) if d else visit.get('visit_date')}: " + "; ".join(parts)


def case_visits(
    pipeline_config,
    props_doc: dict[str, Any],
    *,
    opportunity_id: int,
    entity_id: str,
    as_of: str | None,
    extra_fields: dict[str, Any] | None = None,
) -> list[dict]:
    """One case's visits up to ``as_of`` (ISO date; None = today), oldest first: the
    visit date and each ``case_series`` column, read through Layer 1 -- the same
    extraction its indicators and states were computed from."""
    from django.db import connection

    from connect_labs.semantic.layer1 import build_visit_sql
    from connect_labs.semantic.model import resolve_model

    series = case_series(props_doc)
    model = resolve_model(props_doc)
    visit_sql = build_visit_sql(
        pipeline_config,
        [int(opportunity_id)],
        extra_fields=extra_fields,
        visit_filter={"opportunity_id": int(opportunity_id)},
        props_doc=props_doc,
    )
    cols = ", ".join(f"v.{s['column']} AS {s['name']}" for s in series)
    cut = "AND v.visit_date < (%s::date + 1)" if as_of else ""
    sql = (
        f"SELECT v.visit_date{', ' + cols if cols else ''} FROM ({visit_sql}) v "
        f"WHERE v.{model.key}::text = %s {cut} ORDER BY v.visit_date, v.visit_id"
    )
    params: list[Any] = [str(entity_id)] + ([str(as_of)[:10]] if as_of else [])
    with connection.cursor() as cursor:
        cursor.execute(sql.replace("%", "%%").replace("%%s", "%s"), params)
        names = [c[0] for c in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

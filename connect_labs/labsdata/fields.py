"""What is in ``form_json``, so a person or an agent can find the field they mean.

A question like "hospital vs home births by LLO" lives in a form field nobody
remembers the path of. This samples recent visits from each opportunity, walks
every form, and reports each leaf path with how often it is filled and the SQL
that reads it.

Values are shown ONLY for categorical fields, and only when every value was
seen at least ``MIN_VALUE_COUNT`` times among at most ``MAX_CATEGORIES``
distinct ones. That admits "hospital / home / clinic" and suppresses names,
phone numbers, ids, free text and coordinates by construction -- they are
high-cardinality or rare -- without a list of field names to keep current.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from django.db.models import Max

from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT
from connect_labs.labs.deid import fieldscan

from .scope import Opportunity

SAMPLE_PER_OPPORTUNITY = 300
MAX_CATEGORIES = 12
MIN_VALUE_COUNT = 5
MAX_VALUE_LENGTH = 60


def _current_rows(opp_id: int):
    base = RawVisitCache.objects.filter(opportunity_id=opp_id, pipeline_id=USER_VISITS_RAW_SLOT, visit_count__gt=0)
    generation = base.aggregate(v=Max("visit_count"))["v"]
    if generation is None:
        return RawVisitCache.objects.none()
    return base.filter(visit_count=generation)


def cache_status(opps: list[Opportunity]) -> list[dict]:
    """How much of each opportunity is in the cache right now."""
    out = []
    for o in opps:
        rows = _current_rows(o.id)
        stats = rows.aggregate(
            cached_at=Max("created_at"),
            expires_at=Max("expires_at"),
            latest_visit=Max("visit_date"),
            label=Max("visit_count"),
        )
        count = rows.count()
        out.append(
            {
                "opportunity_id": o.id,
                "opportunity_name": o.name,
                "llo": o.llo,
                "cached_visits": count,
                # A copy holding fewer rows than its own label lost rows after it was
                # written; it is not a complete load, and warming replaces it.
                "complete": not count or count >= (stats["label"] or 0),
                "cached_at": stats["cached_at"].isoformat() if stats["cached_at"] else None,
                "latest_visit_date": stats["latest_visit"].isoformat() if stats["latest_visit"] else None,
            }
        )
    return out


def cached_at(opps: list[Opportunity]) -> dict[int, str | None]:
    """When each opportunity's cached visits were loaded (ISO), or None when nothing is:
    how fresh a query over them is. ``cache_status`` without the counts."""
    out = {}
    for o in opps:
        when = _current_rows(o.id).aggregate(at=Max("created_at"))["at"]
        out[o.id] = when.isoformat() if when else None
    return out


def _accessor(path: tuple) -> str:
    if "[]" in path:
        head = path[: path.index("[]")]
        rest = path[path.index("[]") + 1 :]
        tail = f" ->> '{rest[-1]}'" if len(rest) == 1 else f" #>> '{{{','.join(rest)}}}'" if rest else ""
        return f"jsonb_array_elements(form_json #> '{{{','.join(head)}}}') AS item -- repeat group; then item{tail}"
    return f"form_json #>> '{{{','.join(path)}}}'"


def _kind(values: Counter) -> str:
    kinds = set()
    for v in values:
        if isinstance(v, bool):
            kinds.add("boolean")
        elif isinstance(v, int | float):
            kinds.add("number")
        elif isinstance(v, str):
            s = v.strip()
            if len(s) == 10 and s[4:5] == "-" and s[7:8] == "-":
                kinds.add("date")
            else:
                try:
                    float(s)
                    kinds.add("number")
                except ValueError:
                    kinds.add("text")
        elif v is None:
            continue
        else:
            kinds.add("other")
    return kinds.pop() if len(kinds) == 1 else "mixed" if kinds else "empty"


def describe_fields(opps: list[Opportunity], sample: int = SAMPLE_PER_OPPORTUNITY) -> dict:
    """Every form field across a recent sample of ``opps``' visits."""
    filled: dict[tuple, int] = defaultdict(int)
    values: dict[tuple, Counter] = defaultdict(Counter)
    per_form: dict[tuple, set] = defaultdict(set)
    sampled = 0
    for o in opps:
        forms = _current_rows(o.id).order_by("-visit_date", "-id").values_list("form_json", "deliver_unit")[:sample]
        for form_json, deliver_unit in forms:
            sampled += 1
            leaves = fieldscan.walk(form_json or {})
            seen = set()
            seen_values = set()
            for path, value in leaves:
                if value in (None, ""):
                    continue
                if path not in seen:
                    filled[path] += 1
                    seen.add(path)
                    if deliver_unit:
                        per_form[path].add(deliver_unit)
                # Count VISITS per value, not leaves: a repeat group can carry one
                # visit's value many times, and one submission must never be enough
                # to clear MIN_VALUE_COUNT.
                key = (path, value if not isinstance(value, str) else value[:MAX_VALUE_LENGTH])
                if key not in seen_values:
                    seen_values.add(key)
                    values[path][key[1]] += 1

    fields = []
    for path, count in filled.items():
        counter = values[path]
        categorical = len(counter) <= MAX_CATEGORIES and min(counter.values()) >= MIN_VALUE_COUNT
        entry = {
            "path": ".".join(path),
            "sql": _accessor(path),
            "type": _kind(counter),
            "filled_pct": round(100 * count / sampled, 1) if sampled else 0.0,
            "distinct_in_sample": len(counter),
            "deliver_units": sorted(per_form[path])[:5],
        }
        if categorical:
            entry["values"] = [{"value": v, "count": n} for v, n in counter.most_common()]
        fields.append(entry)
    fields.sort(key=lambda f: (f["path"].startswith("form.meta"), f["path"]))
    return {"sampled_visits": sampled, "fields": fields}


def search_fields(described: dict, text: str | None) -> dict:
    """Narrow a description to fields whose path, or one of whose values, matches ``text``."""
    if not text:
        return described
    needle = text.lower()
    kept = [
        f
        for f in described["fields"]
        if needle in f["path"].lower() or any(needle in str(v["value"]).lower() for v in f.get("values") or [])
    ]
    return {**described, "fields": kept, "search": text}

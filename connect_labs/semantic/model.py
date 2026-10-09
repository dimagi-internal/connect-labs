"""A registry's MODEL: what the engine needs to know about the thing it counts.

The compiler, Layer 1, the workflow binding, the explainer and the grader used to
know this as constants -- a baby, keyed by `baby_case_id`, cohorted on `reg_date`,
with a weight series, fed by a `children` pipeline -- which made the engine a KMC
engine with a registry attached. A registry now declares it, in `properties_doc`:

    entity:                       # what one row of `props` is
      name: baby                  # singular noun; also names the row key (<name>_id)
      plural: babies              # used in explanations
      key: baby_case_id           # Layer-1 column identifying it within an opportunity
      cohort_date: 'COALESCE(reg_date, first_visit::timestamp)'
                                  # over visit_agg columns; optional, default first_visit
      worker: last_visit          # which worker an entity counts for at the flw scope:
                                  #   alphabetical (default) | first_visit | last_visit
    visit_columns:                # derived columns added to every Layer-1 visit row
      - {name: child_alive_no, word_match: {column: death_visits, word: 'no'}}
      - {name: ebf_recorded, sql: 'ebf_visits IS NOT NULL'}
      - {name: form_name, column: form_names}
      - {name: danger_signs, labels: [{column: ds_fever, word: 'yes', label: 'fever'}, ...]}
                                  # the labels of the listed columns that match their word,
                                  # comma-joined (NULL when none): which signs a visit recorded
      - {name: prev_visit_date, previous: {column: visit_date, partition_by: [mother_case_id],
                                           order_by: visit_date}}
      - {name: metres_from_prev, distance_from_previous: {lat: latitude, lon: longitude,
                                                          partition_by: [mother_case_id],
                                                          order_by: visit_date}}
    pipelines:                    # which workflow pipeline sources feed Layer 1
      entity: children
      extra_fields: {weight_g: visits}
      lookups:                    # fields from ANOTHER pipeline's rows, joined on a key
        registration:             #   (an HQ form pipeline, say) -- see `Lookup`
          pipeline: registrations
          on: mother_case_id      #   Layer-1 column of the visit
          key: mother_case_id     #   field (or base column) of the lookup pipeline
          fields: {eligible_at_reg: eligible_full_intervention_bonus}
          pick: latest            #   latest | earliest | max | min | count
    weight_series:                # OPTIONAL: a per-entity reading series
      value_column: weight_g
      ...

and, in `indicators_doc`:

    defaults: {min_denominator: 25}
    series: [C, N]                # optional; otherwise derived from meta.indicator

Resolution is LENIENT on purpose: it never raises, so the validator can report
every problem at once. `compiler.validate` checks the sections (identifiers, the
word regex, every SQL fragment against the Layer-2 grammar) and runs before any
compile, so nothing malformed reaches SQL.

A document saved before these sections existed is resolved through
`semantic/legacy.py` -- see that module for exactly when, and why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from connect_labs.semantic import legacy

DEFAULT_COHORT_DATE = "first_visit"

# Which worker an entity belongs to, at every scope that groups by worker (flw,
# flw_month, case). An entity is visited by whoever visited it, often more than one
# worker -- a mother handed from one FLW to another -- but it is ONE row, so it
# counts for one of them.
#
#   alphabetical -- the lowest username among its visitors. The original rule, kept
#                   as the default so no existing registry's per-worker numbers move.
#                   It is arbitrary: nothing about a worker's name says the case is
#                   theirs.
#   first_visit  -- the worker of its first visit (who found it).
#   last_visit   -- the worker of its latest visit as of the report date (who has
#                   it now). MBW's audit credits a mother this way.
#
# Ties on the visit date break on visit_id, as the window columns do.
WORKER_ATTRIBUTIONS = ("alphabetical", "first_visit", "last_visit")
DEFAULT_WORKER_ATTRIBUTION = "alphabetical"


# Visit-column kinds that read NEIGHBOURING visits (a window), not the visit alone.
# They are computed over Layer 1's own rows before the row-level kinds, so a `sql`
# column may read them.
WINDOW_KINDS = ("previous", "distance_from_previous")
VISIT_COLUMN_KINDS = ("word_match", "labels", "sql", "column", *WINDOW_KINDS)
#: What an `optional` visit column reads as while the pipeline lacks its field
#: (`null_as`); without it a plain column reads as numeric.
NULL_AS_TYPES = ("text", "numeric", "date", "boolean")

LOOKUP_PICKS = ("latest", "earliest", "max", "min", "count")


@dataclass(frozen=True)
class VisitColumn:
    name: str
    kind: str  # one of VISIT_COLUMN_KINDS
    column: str | None = None
    word: str | None = None
    sql: str | None = None
    # Window kinds. The window is always ALSO partitioned by opportunity (a case id
    # can recur across opportunities) and ordered by visit_id after `order_by`, so
    # ties resolve the same way every time.
    partition_by: tuple[str, ...] = ()
    order_by: str | None = None
    skip_null: bool = False
    lat: str | None = None
    lon: str | None = None
    # `labels`: (column, word, label) -- the label is listed when the column matches
    # the word, as `word_match` tests it.
    labels: tuple[tuple[str, str, str], ...] = ()
    # `optional: true`: when the entity pipeline does not (yet) produce a column this
    # one reads, it is NULL instead of an error. Lets a registry declare columns
    # ahead of the pipeline fields that feed them, and an older pipeline keep working.
    optional: bool = False
    # What an `optional` column reads as while the pipeline lacks its field (NULL_AS_TYPES;
    # None: by its kind, numeric for a plain column).
    null_as: str | None = None


@dataclass(frozen=True)
class Lookup:
    """Fields drawn from ANOTHER pipeline's rows, joined to each visit on a key.

    `extra_fields` cannot do this: it evaluates the other pipeline's field paths
    against the entity pipeline's OWN visit rows, which is right only when both
    pipelines read the same forms. A pipeline on a different source -- CommCare HQ
    registration forms, a supervisor app's checklist -- has rows of its own, so its
    fields have to be JOINED: `on` (a Layer-1 column) = `key` (a field of the lookup
    pipeline, or one of its base columns such as `username`), within one opportunity.

    Several lookup rows can share a key (a worker assessed twice), so `pick` says
    which value a visit gets: `latest` / `earliest` by the row's visit_date, or
    `max` / `min` of a numeric value, or `count` of rows.
    """

    name: str
    pipeline: str | None
    on: str | None
    key: str | None
    fields: dict[str, str]
    pick: str = "latest"


@dataclass(frozen=True)
class RegistryModel:
    entity_name: str
    entity_plural: str
    key: str
    cohort_date: str
    visit_columns: tuple[VisitColumn, ...]
    entity_pipeline: str | None
    extra_fields: dict[str, str]
    weight_series: dict[str, Any] | None
    value_column: str | None
    min_denominator: int | None
    lookups: tuple[Lookup, ...] = ()
    worker: str = DEFAULT_WORKER_ATTRIBUTION
    # Which sections came from `legacy.py`. Empty for a registry that declares its model.
    shimmed: tuple[str, ...] = field(default=())

    @property
    def worker_follows_visits(self) -> bool:
        """True when the entity's worker is chosen by visit ORDER, so a one-worker
        evaluation must still see the other workers' visits to know whose it is."""
        return self.worker != DEFAULT_WORKER_ATTRIBUTION

    @property
    def row_id(self) -> str:
        """The internal per-entity key column: `<entity name>_id` (KMC: `baby_id`).

        (opportunity, key) rather than the key alone, because a case id can recur
        across opportunities -- 829 did in the KMC cohort, and keying on the id
        alone merged them.
        """
        return f"{self.entity_name}_id"


def _names(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(v for v in value if isinstance(v, str))
    return ()


def _visit_column(item: Any) -> VisitColumn:
    import dataclasses

    item = item if isinstance(item, dict) else {}
    col = _visit_column_kind(item)
    if not item.get("optional"):
        return col
    null_as = item.get("null_as") if isinstance(item.get("null_as"), str) else None
    return dataclasses.replace(col, optional=True, null_as=null_as)


def _visit_column_kind(item: dict) -> VisitColumn:
    name = str(item.get("name") or "")
    if isinstance(item.get("word_match"), dict):
        wm = item["word_match"]
        return VisitColumn(name, "word_match", column=wm.get("column"), word=wm.get("word"))
    if isinstance(item.get("previous"), dict):
        w = item["previous"]
        return VisitColumn(
            name,
            "previous",
            column=w.get("column"),
            partition_by=_names(w.get("partition_by")),
            order_by=w.get("order_by"),
            skip_null=bool(w.get("skip_null")),
        )
    if isinstance(item.get("distance_from_previous"), dict):
        w = item["distance_from_previous"]
        return VisitColumn(
            name,
            "distance_from_previous",
            lat=w.get("lat"),
            lon=w.get("lon"),
            partition_by=_names(w.get("partition_by")),
            order_by=w.get("order_by"),
        )
    if isinstance(item.get("labels"), list):
        return VisitColumn(
            name,
            "labels",
            labels=tuple(
                (str(x.get("column") or ""), str(x.get("word") or ""), str(x.get("label") or ""))
                for x in item["labels"]
                if isinstance(x, dict)
            ),
        )
    if "sql" in item:
        return VisitColumn(name, "sql", sql=item.get("sql"))
    return VisitColumn(name, "column", column=item.get("column"))


def resolve_model(props_doc: dict[str, Any] | None, indicators_doc: dict[str, Any] | None = None) -> RegistryModel:
    """The model a registry declares, with the legacy shim applied only where it applies."""
    props_doc = props_doc or {}
    old = legacy.predates_model(props_doc)
    shimmed: list[str] = []

    def section(name: str, legacy_value: Any) -> Any:
        if name in props_doc and props_doc[name] is not None:
            return props_doc[name]
        if old:
            shimmed.append(name)
            return legacy_value
        return None

    if old:
        entity = dict(legacy.ENTITY)
        shimmed.append("entity")
    else:
        entity = props_doc["entity"]
    name = str(entity.get("name") or "entity")

    pipelines = section("pipelines", legacy.PIPELINES) or {}
    extra_fields = pipelines.get("extra_fields") if isinstance(pipelines, dict) else None
    extra_fields = dict(extra_fields) if isinstance(extra_fields, dict) else {}
    ws = props_doc.get("weight_series") if isinstance(props_doc.get("weight_series"), dict) else None
    ws = ws or None
    value_column = None
    if ws:
        value_column = ws.get("value_column")
        if value_column is None and old:
            value_column = legacy.WEIGHT_SERIES_VALUE_COLUMN
            shimmed.append("weight_series.value_column")

    defaults = (indicators_doc or {}).get("defaults") if indicators_doc is not None else None
    min_den = (defaults or {}).get("min_denominator") if isinstance(defaults, dict) else None
    if min_den is None and old and indicators_doc is not None:
        min_den = legacy.INDICATOR_DEFAULTS["min_denominator"]
        shimmed.append("defaults.min_denominator")

    raw_lookups = pipelines.get("lookups") if isinstance(pipelines, dict) else None
    lookups = tuple(
        Lookup(
            name=str(lname),
            pipeline=spec.get("pipeline"),
            on=spec.get("on"),
            key=spec.get("key"),
            fields=dict(spec["fields"]) if isinstance(spec.get("fields"), dict) else {},
            pick=str(spec.get("pick") or "latest"),
        )
        for lname, spec in (raw_lookups.items() if isinstance(raw_lookups, dict) else ())
        if isinstance(spec, dict)
    )

    return RegistryModel(
        entity_name=name,
        entity_plural=str(entity.get("plural") or name + "s"),
        key=entity.get("key"),
        cohort_date=entity.get("cohort_date") or DEFAULT_COHORT_DATE,
        visit_columns=tuple(_visit_column(v) for v in (section("visit_columns", legacy.VISIT_COLUMNS) or [])),
        entity_pipeline=pipelines.get("entity") if isinstance(pipelines, dict) else None,
        extra_fields=extra_fields,
        weight_series=ws,
        value_column=value_column,
        min_denominator=int(min_den) if isinstance(min_den, (int, float)) and not isinstance(min_den, bool) else None,
        lookups=lookups,
        worker=str(entity.get("worker") or DEFAULT_WORKER_ATTRIBUTION),
        shimmed=tuple(shimmed),
    )


_PREFIX = re.compile(r"[A-Za-z]+")


def indicator_prefix(indicator: Any) -> str:
    """`C14` -> `C`, `Q03` -> `Q`, `VQ01` -> `VQ`."""
    m = _PREFIX.match(str(indicator or ""))
    return m.group(0).upper() if m else ""


def _declared_series(indicators_doc: dict[str, Any]) -> tuple[str, ...]:
    declared = (indicators_doc or {}).get("series")
    if isinstance(declared, list) and declared:
        return tuple(str(s).upper() for s in declared)
    return ()


def indicator_series(indicators_doc: dict[str, Any], meta: dict[str, Any] | None) -> str:
    """The family one indicator belongs to.

    In order: the indicator's own `meta.series`; its id's letter prefix, when the
    registry declares a family of that name (`Q03` in a registry declaring `[Q]`);
    and otherwise, in a registry declaring exactly ONE family, that family. The
    last rule is what lets ids be plain slugs -- `mortality` in a registry that
    declares `series: [KMC]` -- rather than codes whose letters double as a family.

    A registry that declares nothing falls back to the prefix, which is how records
    saved before `series:` existed still resolve.
    """
    meta = meta or {}
    explicit = meta.get("series")
    if explicit:
        return str(explicit).upper()
    prefix = indicator_prefix(meta.get("indicator"))
    declared = _declared_series(indicators_doc)
    if prefix in declared:
        return prefix
    if len(declared) == 1:
        return declared[0]
    return prefix


def series_prefixes(indicators_doc: dict[str, Any]) -> tuple[str, ...]:
    """The indicator families a registry carries, in the order they first appear.

    Declared (`indicators_doc.series`) when the registry says so; otherwise read off
    each indicator's own id. There is no fixed list.
    """
    declared = _declared_series(indicators_doc)
    if declared:
        return declared
    out: list[str] = []
    for m in (indicators_doc or {}).get("measures") or []:
        meta = m.get("meta") if isinstance(m, dict) else None
        name = indicator_series(indicators_doc, meta) if meta and meta.get("indicator") else ""
        if name and name not in out:
            out.append(name)
    return tuple(out)

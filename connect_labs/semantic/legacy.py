"""The KMC values for every model section, for registries saved BEFORE the model existed.

WHY THIS MODULE EXISTS
The engine used to be KMC-shaped in code: the entity key (`baby_case_id`), the
cohort date (`reg_date`, falling back to the first visit), the visit markers
Layer 1 adds (`child_alive_no`, `ebf_recorded`, `form_name`, ...), which workflow
pipeline Layer 1 is generated from (`children`) and which one supplies the weight
(`visits` -> `weight_g`), the value column of the series, and a minimum
denominator of 25. It is now a general engine that reads all of that from the
registry's own MODEL sections (see `semantic/model.py`).

But registries are RECORDS, edited live, and the ones saved before this change
carry none of those sections -- the live Dimagi-KMC record (19784) and every copy
seeded from disk before now. Nothing can edit them on the way in, and a record
that silently lost its markers would compile to `column "child_alive_no" does not
exist` on the next dashboard load. So their model is supplied from here.

WHEN IT APPLIES -- and only then
A properties document whose `entity` is NOT a mapping (the old bare string
`entity: baby`, or no entity at all) predates the model. For such a document, and
only for the sections it does not declare, these values are used. A document that
declares `entity:` as a mapping is on the new shape and gets NOTHING from here: an
absent section means absent (no visit columns, no series, no default
denominator floor), never "KMC's".

The shipped on-disk KMC registry (`registry/kmc/`) declares every section
explicitly and must never reach this module; `test_generic_engine.py` pins that.
Nothing new should be added here -- a new model section gets a legacy value only
if records that predate it would otherwise break.
"""

from __future__ import annotations

from typing import Any

# A workflow that binds no registry computes from this on-disk one. Every such
# workflow predates registries-as-records and is a KMC report.
DEFAULT_REGISTRY_NAME = "kmc"

ENTITY: dict[str, Any] = {
    "name": "baby",
    "plural": "babies",
    "key": "baby_case_id",
    # reg_date is a FILTERed MIN over the visits, so a baby whose rows never carried
    # one aggregates to NULL; truncating reg_date alone would drop those cases out
    # of every month instead of cohorting them on their first visit.
    "cohort_date": "COALESCE(reg_date, first_visit::timestamp)",
}

# The pipeline emits these markers as strings and applies them as `contains_word`
# at aggregation time; Layer 1 materialises the same test per visit.
VISIT_COLUMNS: list[dict[str, Any]] = [
    {"name": "child_alive_no", "word_match": {"column": "death_visits", "word": "no"}},
    {"name": "danger_sign_yes", "word_match": {"column": "danger_visits", "word": "yes"}},
    {"name": "referred_yes", "word_match": {"column": "referral_visits", "word": "yes"}},
    {"name": "self_referral_yes", "word_match": {"column": "self_referral_visits", "word": "yes"}},
    {"name": "ebf_recorded", "sql": "ebf_visits IS NOT NULL"},
    {"name": "form_name", "column": "form_names"},
]

# Layer 1 comes from the `children` pipeline; the per-visit weight from the
# separate weight-series pipeline aliased `visits`.
PIPELINES: dict[str, Any] = {"entity": "children", "extra_fields": {"weight_g": "visits"}}

WEIGHT_SERIES_VALUE_COLUMN = "weight_g"

# The KMC render's own fallback (`var MIN_DEN = 25`).
INDICATOR_DEFAULTS: dict[str, Any] = {"min_denominator": 25}


def predates_model(props_doc: dict[str, Any] | None) -> bool:
    """True for a properties document saved before the model sections existed."""
    return not isinstance((props_doc or {}).get("entity"), dict)

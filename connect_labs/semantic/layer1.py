"""Layer 1: build the visit extraction from the PIPELINE, never by hand.

WHY THIS MODULE EXISTS
`compile_indicator_sql` / `compile_rollup_sql` take `visit_sql` as a string, and
the first real parity run against opportunity 10042 was driven by a hand-written
one. It looked complete and was not: measured against the pipeline's own field
expressions it carried 3 of 10 danger-sign paths, 3 of 6 referral paths and 3 of 4
kmc-hours paths. Those three fields are exactly the ones whose indicators
disagreed with the existing dashboard (C19, C20, C23) -- the paraphrase WAS the
bug.

The fallback path lists are the expensive, hard-won part of Layer 1. Retyping them
is the one thing guaranteed to lose them, so this module generates the extraction
from the pipeline's own schema instead. Nothing downstream should ever build that
string itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# Raw marker columns the pipeline emits as strings, mapped to the booleans
# properties.yml consumes. The pipeline applies these as `filter_value` with
# filter_op `contains_word` at aggregation time; at visit level we materialise the
# same test so Layer 2 can just count them.
MARKER_BOOLEANS: dict[str, tuple[str, str]] = {
    "child_alive_no": ("death_visits", "no"),
    "danger_sign_yes": ("danger_visits", "yes"),
    "referred_yes": ("referral_visits", "yes"),
    "self_referral_yes": ("self_referral_visits", "yes"),
}


def build_visit_sql(
    pipeline_schema: dict[str, Any],
    opportunity_ids: Iterable[int],
    *,
    generate_sql_preview=None,
    extra_fields: dict[str, Any] | None = None,
) -> str:
    """Return the visit-level SQL for a set of opportunities.

    The extraction itself comes from the pipeline engine, so every fallback path
    is whatever the pipeline actually uses. This function only:

      1. widens the WHERE from one opportunity to the requested set,
      2. de-duplicates across cache partitions -- `labs_raw_visit_cache` is keyed
         by (opportunity, pipeline), so the same visit is present once per
         pipeline that has cached it, and counting it twice inflates everything,
      3. adds `opportunity_id` (the extraction does not select it) and the marker
         booleans above,
      4. merges fields from OTHER pipelines via `extra_fields`.

    (4) is not a convenience. The KMC dashboard reads its weight series from a
    SECOND pipeline ("KMC Weight Series", 5109) whose `weight_g` has five fallback
    paths, while the case pipeline's `weights` has six -- the extra
    `form.case.update.child_weight_visit`. Deriving the series from the case
    pipeline therefore produces a different set of readings, which changes
    weight_consistent and the early-growth window and moves C07-C13. Whichever set
    is *right* is a question for the workbook; for a like-for-like comparison the
    series has to come from the pipeline the dashboard actually uses.
    """
    if generate_sql_preview is None:  # pragma: no cover - import at call time
        from connect_labs.labs.analysis.backends.sql.query_builder import generate_sql_preview as _gen

        generate_sql_preview = _gen

    opps = [int(o) for o in opportunity_ids]
    if not opps:
        raise ValueError("build_visit_sql needs at least one opportunity")

    preview = generate_sql_preview(pipeline_schema, opps[0])
    ex = preview["visit_extraction_sql"]

    # The extraction selects visit columns but not opportunity_id/pipeline_id;
    # the rollup groups by the former and the dedup orders by the latter.
    ex = ex.replace(
        "SELECT\nvisit_id,",
        "SELECT DISTINCT ON (opportunity_id, visit_id)\nopportunity_id,\npipeline_id,\nvisit_id,",
        1,
    )
    opp_list = ",".join(str(o) for o in opps)
    old_where = f"WHERE opportunity_id = {opps[0]} AND pipeline_id"
    idx = ex.find(old_where)
    if idx == -1:
        raise ValueError("could not locate the extraction's WHERE clause to widen")
    head = ex[:idx]
    ex = head + f"WHERE opportunity_id IN ({opp_list})\n" + "ORDER BY opportunity_id, visit_id, pipeline_id"

    extra_cols = ""
    if extra_fields:
        parts = []
        for name, cfg in extra_fields.items():
            prev = generate_sql_preview(cfg, opps[0])
            expr = prev["field_expressions"][name]["transformed_sql"]
            parts.append(f"{expr} as {name}")
        extra_cols = ",\n" + ",\n".join(parts)
    ex = ex.replace("\nFROM labs_raw_visit_cache", extra_cols + "\nFROM labs_raw_visit_cache", 1)

    markers = ",\n  ".join(f"(x.{col} ~* '\\y{word}\\y') AS {name}" for name, (col, word) in MARKER_BOOLEANS.items())
    return f"""SELECT
  x.*,
  {markers},
  (x.ebf_visits IS NOT NULL) AS ebf_recorded,
  x.form_names AS form_name
FROM (
{ex}
) x"""

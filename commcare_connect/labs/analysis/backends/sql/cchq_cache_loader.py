"""
Direct ComputedVisitCache loader for cchq form pipelines that participate
only as JOIN targets.

The standard pipeline path (`process_and_cache`) writes to BOTH
`labs_raw_visit_cache` and `labs_computed_visit_cache`. That's correct for a
single-source pipeline — but when a cchq-side pipeline (e.g., MBW
registrations) coexists with a Connect-side pipeline (e.g., MBW visits) for
the same `opportunity_id`, both writes target `labs_raw_visit_cache` and
`build_flw_aggregation_query` reads ALL rows for that opp regardless of
source. That commingling produces wrong results.

This loader avoids the issue by writing **only** to
`labs_computed_visit_cache` (keyed by `(opportunity_id, config_hash)`),
which is naturally segmented by config. Field extraction runs in Python
using `extract_json_path_multi` instead of SQL — equivalent semantics for
plain path-coalesce + transform fields.

Limitations:
- Does not handle window_fields / extracted_filters (they require SQL).
- Does not handle `extractor` / full-context transforms.
- Aggregation type is ignored — these rows are visit-level by definition.

Use only when the cchq pipeline is consumed exclusively as a JOIN target.
For pipelines whose computed rows back a dashboard tab directly, the
standard `process_and_cache` path remains correct.
"""

from datetime import timedelta

from django.utils import timezone

from commcare_connect.labs.analysis.backends.sql.models import ComputedVisitCache
from commcare_connect.labs.analysis.config import AnalysisPipelineConfig
from commcare_connect.labs.analysis.utils import extract_json_path_multi, get_config_hash

DEFAULT_TTL_HOURS = 24


def populate_computed_cache_from_form_dicts(
    config: AnalysisPipelineConfig,
    opportunity_id: int,
    form_dicts: list[dict],
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> int:
    """Extract config fields from each form dict and write to ComputedVisitCache.

    Args:
        config: Pipeline config; only `fields` (paths/paths/transform) is consumed.
        opportunity_id: Scoping for the cache rows.
        form_dicts: Raw CCHQ form payloads (e.g., from `fetch_registration_forms`).
            Each entry should expose `id` and contain a top-level `form` key
            whose contents match the path schema (paths starting with `form.`).
        ttl_hours: Cache TTL.

    Returns:
        Number of rows written.
    """
    config_hash = get_config_hash(config)
    visit_count = len(form_dicts)
    expires_at = timezone.now() + timedelta(hours=ttl_hours)

    # Wipe prior rows for this (opp, config) so re-runs don't accumulate
    # — same semantics the SQL backend uses for the computed cache.
    ComputedVisitCache.objects.filter(opportunity_id=opportunity_id, config_hash=config_hash).delete()

    rows = []
    for fd in form_dicts:
        # CCHQ form payload: `{"id": ..., "form": {...}, "received_on": ..., ...}`
        # extract_json_path_multi consumes whatever dict we hand it, so paths
        # starting with `form.` resolve into fd["form"][...]. That matches what
        # the SQL builder does against form_json.
        visit_id = str(fd.get("id") or fd.get("instanceID") or "")
        if not visit_id:
            continue

        form_data = fd.get("form", {}) if isinstance(fd, dict) else {}
        meta = form_data.get("meta", {}) if isinstance(form_data, dict) else {}
        username = meta.get("username", "") or meta.get("userID", "")

        computed = {}
        for f in config.fields:
            # `extractor` takes precedence over path-based extraction. It
            # receives the full form dict so it can access multiple paths
            # (e.g., v1's MBW age = years-since-mother_dob if set, else
            # age_in_years_rounded, else mothers_age — three paths in one
            # field). Used only on the cchq side; the SQL builders ignore
            # extractor on aggregated queries.
            if f.extractor and callable(f.extractor):
                try:
                    value = f.extractor(fd)
                except Exception:
                    value = None
            else:
                paths = f.get_paths()
                value = extract_json_path_multi(fd, paths) if paths else None
                if f.transform and callable(f.transform):
                    try:
                        value = f.transform(value)
                    except Exception:
                        value = None
            computed[f.name] = value

        rows.append(
            ComputedVisitCache(
                opportunity_id=opportunity_id,
                config_hash=config_hash,
                visit_count=visit_count,
                expires_at=expires_at,
                visit_id=visit_id,
                username=username,
                computed_fields=computed,
            )
        )

    if rows:
        ComputedVisitCache.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)

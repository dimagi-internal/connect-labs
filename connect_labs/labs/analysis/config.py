"""
Configuration classes for declarative analysis setup.

Supports complex JSON path extraction like:
    form_json -> 'form' -> 'additional_case_info' ->> 'childs_age_in_month'

Becomes:
    FieldComputation(
        name="child_age_months",
        path="form.additional_case_info.childs_age_in_month",
        aggregation="first"  # or "avg", "sum", etc.
    )
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, get_args

logger = logging.getLogger(__name__)

AggregationType = Literal[
    "sum",
    "avg",
    "count",
    "min",
    "max",
    "list",
    "first",
    "last",
    "count_unique",
    "count_distinct",
    "median",
    "mode",
    "mode_share",
    "dup_share",
]

# Single source of truth for valid aggregation names — derived from the
# Literal so adding a new aggregation in one place stays in sync.
VALID_AGGREGATIONS = frozenset(get_args(AggregationType))


# Comparison operations available on FieldComputation.filter_path/filter_value.
# - `eq`: exact equality (default; preserves prior behaviour).
# - `contains_word`: filter_path's value is treated as a whitespace-separated
#   token list; matches if filter_value is one of the tokens. Mirrors V1
#   logic like `if "ebf" in bf_status.split()` for multi-select form fields.
FilterOp = Literal["eq", "contains_word"]

VALID_FILTER_OPS = frozenset(get_args(FilterOp))


# Base columns on labs_raw_visit_cache that ship with every visit. A FieldComputation
# whose `name` collides with one of these silently shadows the base column in the
# extraction query — the result is the JSONB-extracted value (typically a string)
# instead of the typed base column. That breaks downstream consumers that assume
# the typed shape (e.g. VisitRow.to_dict() calling .isoformat() on visit_date).
# AnalysisPipelineConfig.__post_init__ raises on collision rather than letting the
# silent override happen — the fix is to namespace the custom field (e.g. rename
# `visit_date` → `form_visit_date` if it extracts a different value than the base).
RAW_VISIT_BASE_COLUMNS = frozenset(
    {
        "visit_id",
        "username",
        "deliver_unit",
        "deliver_unit_id",
        "entity_id",
        "entity_name",
        "visit_date",
        "status",
        "review_status",
        "flagged",
        "location",
        "form_json",
    }
)


class CacheStage(Enum):
    """
    Pipeline stages for analysis caching.

    Determines which stage is the "terminal" output for a given analysis:
    - VISIT_LEVEL: VisitAnalysisResult is the final output (one row per visit)
    - AGGREGATED: FLWAnalysisResult is the final output (one row per FLW, GROUP BY username)
    - ENTITY: EntityAnalysisResult is the final output (one row per entity, GROUP BY linking_field)

    ENTITY is for analyses whose unit of interest is a tracked thing — a beneficiary case,
    a child, a household — rather than the worker who served them. The pipeline groups raw
    visits by `AnalysisPipelineConfig.linking_field` (which must be set when terminal_stage
    is ENTITY) and applies the same field/histogram aggregation vocabulary used at FLW stage.
    """

    VISIT_LEVEL = "visit_level"
    AGGREGATED = "aggregated"
    ENTITY = "entity"


@dataclass
class DataSourceConfig:
    """
    Configuration for where a pipeline fetches its raw data.

    Attributes:
        type: Data source type. "connect_csv" fetches from the Connect production
              paginated JSON export (the literal token name predates the v2 migration
              and is retained for stable identifiers across pipeline templates).
              "cchq_forms" fetches from CommCare HQ Form API.
              "ocs_sessions" fetches from Open Chat Studio sessions API.
              "connect_export" fetches from a Connect production export endpoint
              (e.g. audit_reports, audit_report_entries, assigned_tasks).
              "cchq_cases" fetches from CommCare HQ Case API v2 (e.g. work-area cases).
        form_name: (cchq_forms only) Form name for xmlns discovery,
                   e.g., "Register Mother", "Gold Standard Visit Checklist"
        app_id: (cchq_forms only) Explicit CommCare app ID.
        app_id_source: (cchq_forms only) "opportunity" = derive from opportunity metadata.
        gs_app_id: (cchq_forms only) Explicit GS supervisor app ID.
        experiment_id: (ocs_sessions only) OCS experiment UUID.
        api_key: (ocs_sessions only) OCS API key for bulk reads. When set the
            fetcher uses X-API-KEY auth instead of the user's OAuth Bearer token,
            so session data is accessible regardless of which OCS team the viewer
            belongs to. Store only in pipeline schemas that are already
            access-controlled via the LabsRecord permission model.
        endpoint: (connect_export only) The export endpoint path segment,
            e.g. "audit_reports", "audit_report_entries", "assigned_tasks".
        case_type: (cchq_cases only) CommCare HQ case type to fetch via the
            Case API v2, e.g. "work-area". Each case becomes one visit dict
            with the raw case nested under form_json["case"], so field paths
            follow "case.properties.<prop>" / "case.owner_id".
    """

    type: str = "connect_csv"
    form_name: str = ""
    app_id: str = ""
    app_id_source: str = ""
    gs_app_id: str = ""
    experiment_id: str = ""
    api_key: str = ""
    endpoint: str = ""
    case_type: str = ""

    def __post_init__(self):
        if self.type not in ("connect_csv", "cchq_forms", "ocs_sessions", "connect_export", "cchq_cases"):
            raise ValueError(f"Invalid data source type: {self.type}")


@dataclass
class FieldComputation:
    """
    Configuration for extracting and aggregating a field from UserVisit data.

    Supports three extraction modes:
    1. Path-based: Extract from form_json using dot notation (e.g., "form.case.update.field")
    2. Multi-path: Try multiple paths in order until a value is found
    3. Custom extractor: A function that receives the full visit dict and returns a value

    Examples:
        # Simple path extraction
        FieldComputation(
            name="buildings_visited",
            path="form.building_count",
            aggregation="sum",
            default=0
        )

        # Multiple fallback paths (for different form structures)
        FieldComputation(
            name="muac_cm",
            path="form.case.update.soliciter_muac_cm",
            paths=[
                "form.case.update.soliciter_muac_cm",   # opp 814
                "form.subcase_0.case.update.soliciter_muac",  # opp 822
            ],
            aggregation="avg"
        )

        # Path with transform
        FieldComputation(
            name="avg_accuracy",
            path="metadata.location",
            aggregation="avg",
            transform=lambda loc: float(loc.split()[3]) if loc and len(loc.split()) > 3 else None
        )

        # Custom extractor (receives full visit dict)
        FieldComputation(
            name="images_with_questions",
            extractor=extract_images_with_question_ids,  # fn(visit_dict) -> Any
            aggregation="first",
        )
    """

    name: str
    path: str = ""
    aggregation: AggregationType = "first"
    default: Any = None
    transform: Callable[[Any], Any] | None = None
    description: str = ""
    paths: list[str] | None = None
    extractor: Callable[[dict], Any] | None = None  # Custom extractor receives full visit dict
    filter_path: str = ""  # Optional: path for FILTER (WHERE ...) clause
    # Optional: list of paths to try via COALESCE (mirrors `paths` for the value
    # extraction). Use this when the field already coalesces multiple paths AND
    # the filter must apply to the SAME coalesced value. Mutually exclusive with
    # filter_path. Required for v1 fidelity on MBW's EBF metric, where v1 reads
    # a coalesced bf_status and checks `if "ebf" in bf_status.split()` against
    # that exact value, not against any one path.
    filter_paths: list[str] | None = None
    filter_value: str = ""  # Optional: value to compare against in filter
    # Filter comparison kind. "eq" is exact equality; "contains_word" treats the
    # filter_path's value as a whitespace-separated token list and matches if
    # filter_value is one of the tokens. Used for multi-select form fields like
    # MBW's bf_status, where v1 logic is `if "ebf" in bf_status.split()`.
    filter_op: str = "eq"
    # Two-pass aggregation. When set, the field is computed as a nested
    # aggregation: rows are first grouped by `pre_aggregate_by` (an inner
    # JSON path, typically `mother_case_id` or another secondary key) and
    # collapsed using `pre_aggregation`; the resulting per-pre-group values
    # are then grouped by the pipeline's outer grouping_key and aggregated
    # using `aggregation`.
    #
    # Concrete example — parity concentration per FLW:
    #   pre_aggregate_by = "form.parents.parent.case.@case_id"  # mother
    #   pre_aggregation = "first"                                # per-mother first parity
    #   aggregation     = "mode_share"                           # per-FLW concentration
    # ⇒ SQL groups visits by (FLW, mother), takes one parity per mother,
    # then computes mode_share of the per-mother parities per FLW.
    #
    # Mirrors v1's chained-loop quality computations without needing a full
    # multi-stage pipeline cache refactor. Empty values mean single-pass
    # aggregation as before.
    pre_aggregate_by: str = ""
    pre_aggregation: str = "first"
    # When set to "last_username", the pre_group is attributed to a single
    # outer group (the FLW whose visit was the LAST one for that pre_group key),
    # rather than appearing under every FLW that visited it. Mirrors v1's
    # `mother_to_username[mid] = row.username` last-write-wins assignment in
    # `build_followup_from_pipeline.Step 2`.
    #
    # Concrete: for fraud-detection metrics like phone_dup_share, a mother
    # visited by FLWs A and B is counted only under whichever FLW visited her
    # LAST. Without this, a "shared" mother with a duplicated phone inflates
    # both A and B's dup_share — making the metric noisier as a fraud signal
    # and disagreeing with v1.
    #
    # Empty (default): per-visit attribution (each pre_group counted under
    # every outer group that visited it).
    pre_aggregate_attribute_to: str = ""

    def __post_init__(self):
        """Validate configuration."""
        if not self.name:
            raise ValueError("Field name is required")
        if not self.path and not self.paths and not self.extractor:
            raise ValueError("Field requires path, paths, or extractor")
        if self.aggregation not in VALID_AGGREGATIONS:
            raise ValueError(f"Invalid aggregation type: {self.aggregation}")
        if self.pre_aggregate_by and self.pre_aggregation not in VALID_AGGREGATIONS:
            raise ValueError(f"Invalid pre_aggregation type: {self.pre_aggregation}")
        if self.pre_aggregate_attribute_to and self.pre_aggregate_attribute_to != "last_username":
            raise ValueError(
                f"pre_aggregate_attribute_to: {self.pre_aggregate_attribute_to!r} is not a valid value. "
                "Valid: '' or 'last_username'."
            )
        if self.pre_aggregate_attribute_to and not self.pre_aggregate_by:
            raise ValueError(
                f"FieldComputation {self.name!r}: pre_aggregate_attribute_to requires pre_aggregate_by to be set."
            )
        if self.filter_op not in VALID_FILTER_OPS:
            raise ValueError(f"Invalid filter_op: {self.filter_op}. Valid: {sorted(VALID_FILTER_OPS)}")
        if self.filter_paths and self.filter_path:
            raise ValueError(f"FieldComputation {self.name!r}: filter_paths and filter_path are mutually exclusive")

    def get_paths(self) -> list[str]:
        """Get list of paths to try (paths if set, otherwise [path])."""
        if self.paths:
            return self.paths
        return [self.path] if self.path else []

    @property
    def uses_extractor(self) -> bool:
        """Check if this field uses a custom extractor."""
        return self.extractor is not None


@dataclass
class HistogramComputation:
    """
    Configuration for creating a histogram/sparkline from numeric values.

    Bins values from a numeric field and produces:
    - Individual bin counts as separate fields (e.g., muac_9_5_10_5_visits)
    - A sparkline string showing the distribution
    - Summary statistics (mean, std, etc.)

    Supports multiple fallback paths for handling different form structures.

    Example:
        HistogramComputation(
            name="muac_distribution",
            path="form.case.update.soliciter_muac_cm",
            paths=[
                "form.case.update.soliciter_muac_cm",  # opp 814
                "form.subcase_0.case.update.soliciter_muac",  # opp 822
            ],
            lower_bound=9.5,
            upper_bound=21.5,
            num_bins=12,
            bin_name_prefix="muac",
        )

        Produces fields like:
        - muac_9_5_10_5_visits: 5
        - muac_10_5_11_5_visits: 12
        - ... etc for each bin
    """

    name: str
    path: str
    lower_bound: float
    upper_bound: float
    num_bins: int
    bin_name_prefix: str = ""
    transform: Callable[[Any], Any] | None = None
    description: str = ""
    include_out_of_range: bool = True  # Count values outside bounds in first/last bin
    paths: list[str] | None = None  # Optional list of fallback paths to try in order

    def __post_init__(self):
        """Validate configuration."""
        if not self.name:
            raise ValueError("Histogram name is required")
        if not self.path and not self.paths:
            raise ValueError("Field path or paths is required")
        if self.lower_bound >= self.upper_bound:
            raise ValueError("lower_bound must be less than upper_bound")
        if self.num_bins < 1:
            raise ValueError("num_bins must be at least 1")

    def get_paths(self) -> list[str]:
        """Get list of paths to try (paths if set, otherwise [path])."""
        if self.paths:
            return self.paths
        return [self.path] if self.path else []

    @property
    def bin_width(self) -> float:
        """Calculate the width of each bin."""
        return (self.upper_bound - self.lower_bound) / self.num_bins

    def get_bin_edges(self) -> list[float]:
        """Get the edges of all bins."""
        width = self.bin_width
        return [self.lower_bound + i * width for i in range(self.num_bins + 1)]

    def get_bin_names(self) -> list[str]:
        """Generate field names for each bin."""
        edges = self.get_bin_edges()
        prefix = self.bin_name_prefix or self.name
        names = []
        for i in range(self.num_bins):
            low = edges[i]
            high = edges[i + 1]
            # Format as prefix_X_Y_visits (replacing . with _)
            low_str = str(low).replace(".", "_")
            high_str = str(high).replace(".", "_")
            names.append(f"{prefix}_{low_str}_{high_str}_visits")
        return names

    def value_to_bin_index(self, value: float) -> int | None:
        """
        Get the bin index for a value.

        Returns None if value is out of range and include_out_of_range is False.
        """
        if value < self.lower_bound:
            return 0 if self.include_out_of_range else None
        if value >= self.upper_bound:
            return self.num_bins - 1 if self.include_out_of_range else None

        # Calculate bin index
        index = int((value - self.lower_bound) / self.bin_width)
        # Handle edge case where value == upper_bound exactly
        return min(index, self.num_bins - 1)


# Base columns on labs_raw_visit_cache available without extraction. Used for
# WindowFieldComputation reference validation — partition_by/order_by can name
# either a base column (visit_date, visit_id, username, etc.) or an extracted
# field declared in fields[].
_BASE_VISIT_COLUMNS = frozenset(
    {
        "visit_id",
        "username",
        "visit_date",
        "visit_datetime",
        "status",
        "flagged",
        "location",
        "deliver_unit",
        "deliver_unit_id",
        "entity_id",
        "entity_name",
    }
)


# Window operations supported by WindowFieldComputation.
# - `lag_haversine`: haversine distance in meters between this row's GPS and
#   the previous row's GPS in the same partition. Requires lat_field and
#   lon_field to name extracted/base columns containing the latitude and
#   longitude as floats. Returns NULL on the first row of each partition or
#   when either coordinate is NULL (sparse-GPS-friendly).
WindowOperation = Literal["lag_haversine"]

VALID_WINDOW_OPERATIONS = frozenset(get_args(WindowOperation))


@dataclass
class WindowFieldComputation:
    """Per-row window-function field evaluated AFTER per-row extraction.

    Each WindowFieldComputation produces one extra column per visit row, computed
    via a SQL window function over the extraction subquery. The operation accesses
    other already-extracted fields by name and applies a partition/order spec.

    Use case: GPS distance between consecutive visits to the same mother. The
    visits are in `labs_raw_visit_cache`; the extracted lat/lon columns are
    declared in fields[]; this WindowFieldComputation says "for each visit,
    compute haversine to the prev visit in the same mother_case_id partition,
    ordered by visit_datetime."

    Attributes:
        name: Output column name (e.g., "distance_from_prev_case_visit_m").
        operation: Which window op to apply. Currently only "lag_haversine".
        partition_by: Name of an extracted field or base column to partition over.
            For per-mother revisit distance: "mother_case_id". For per-FLW-day
            travel chain: would need a tuple — not supported in this primitive.
        order_by: Name of an extracted field or base column to order within
            each partition. Typically "visit_datetime".
        lat_field: (lag_haversine only) Name of the latitude field.
        lon_field: (lag_haversine only) Name of the longitude field.
        description: Optional human-readable description for docs/UI.
    """

    name: str
    operation: WindowOperation = "lag_haversine"
    partition_by: str = ""
    order_by: str = ""
    lat_field: str = ""
    lon_field: str = ""
    description: str = ""

    def __post_init__(self):
        if not self.name:
            raise ValueError("WindowFieldComputation name is required")
        if self.operation not in VALID_WINDOW_OPERATIONS:
            raise ValueError(
                f"WindowFieldComputation {self.name!r}: unknown operation {self.operation!r}. "
                f"Valid: {sorted(VALID_WINDOW_OPERATIONS)}"
            )
        if self.operation == "lag_haversine":
            if not self.lat_field or not self.lon_field:
                raise ValueError(
                    f"WindowFieldComputation {self.name!r}: lag_haversine requires lat_field and lon_field"
                )
            if not self.partition_by:
                raise ValueError(f"WindowFieldComputation {self.name!r}: lag_haversine requires partition_by")
            if not self.order_by:
                raise ValueError(f"WindowFieldComputation {self.name!r}: lag_haversine requires order_by")


@dataclass
class JoinConfig:
    """
    Cross-pipeline JOIN spec.

    Pulls fields from another pipeline's already-cached visit-level rows
    (`labs_computed_visit_cache` filtered by that pipeline's `config_hash`)
    into THIS pipeline's row scope. Joined fields become accessible via the
    existing JSONB-path extraction under `joined.<from_alias>.<field>`, so
    aggregations, filters, and pre_aggregate_by all work without any new
    field kind.

    Concrete use case — visits ⋈ registrations:
        JoinConfig(
            from_alias="registrations",
            local_key="form.parents.parent.case.@case_id",
            remote_key_field="mother_case_id",
            fields=[
                {"name": "phone_number", "from": "phone_number"},
                {"name": "age_recorded", "from": "age_recorded"},
                {"name": "eligible_full_intervention_bonus",
                 "from": "eligible_full_intervention_bonus"},
            ],
        )
    Then a visits-pipeline FieldComputation can target
    `joined.registrations.phone_number` and aggregate it normally.

    Attributes:
        from_alias: Logical alias of the joined pipeline. The schema parser
            resolves this to a concrete `config_hash` by looking up the
            sibling pipeline in the same workflow definition.
        local_key: JSONB path on THIS pipeline's `form_json` whose value is
            the join key (e.g., the mother's `@case_id` on a visit form).
        remote_key_field: Field NAME inside the joined pipeline's
            `computed_fields` JSON whose value matches `local_key`.
        fields: List of `{"name": <output>, "from": <remote field name>}`
            dicts. The output name is used both as the alias inside
            `joined.<from_alias>.<name>` and is what aggregations reference.
        resolved_config_hash: Populated by the orchestration layer after
            schema parsing; the SQL builder reads it. Empty until resolved.
    """

    from_alias: str
    local_key: str
    remote_key_field: str
    fields: list[dict] = field(default_factory=list)
    resolved_config_hash: str = ""

    def __post_init__(self):
        if not self.from_alias:
            raise ValueError("JoinConfig.from_alias is required")
        if not self.local_key:
            raise ValueError(f"JoinConfig({self.from_alias!r}): local_key is required")
        if not self.remote_key_field:
            raise ValueError(f"JoinConfig({self.from_alias!r}): remote_key_field is required")
        if not self.fields:
            raise ValueError(f"JoinConfig({self.from_alias!r}): fields[] is required")
        for i, f in enumerate(self.fields):
            if not isinstance(f, dict) or "name" not in f or "from" not in f:
                raise ValueError(
                    f"JoinConfig({self.from_alias!r}).fields[{i}] must be a dict with 'name' and 'from' keys"
                )


@dataclass
class AnalysisPipelineConfig:
    """
    Unified configuration for analysis computation and pipeline behavior.

    Combines:
    - What fields to extract and how to aggregate them
    - How to group visits
    - Pipeline metadata for caching (experiment name, terminal stage)

    Attributes:
        grouping_key: Field to group by (e.g., "username", "user_id", "deliver_unit_id")
        fields: List of FieldComputations to apply
        histograms: List of HistogramComputations to apply
        filters: Optional dict of filters to apply to visits
        date_field: Field name for date filtering (default: "visit_date")
        experiment: Name of the experiment/project (e.g., "chc_nutrition", "coverage")
        terminal_stage: Which stage is the final output for LabsRecord caching
        linking_field: Field to use for linking visits to entities (children, beneficiaries).
                      Default "entity_id" uses the base field from Connect.
                      Can be set to a computed field name (e.g., "beneficiary_case_id")
                      for cases where entity_id doesn't correctly identify unique entities.
                      Required when terminal_stage=CacheStage.ENTITY — its path expression
                      becomes the GROUP BY column for entity-stage aggregation.

    Example:
        config = AnalysisPipelineConfig(
            grouping_key="username",
            fields=[
                FieldComputation(
                    name="total_muac_measurements",
                    path="form.case.update.soliciter_muac_cm",
                    aggregation="count"
                ),
            ],
            histograms=[
                HistogramComputation(
                    name="muac_distribution",
                    path="form.case.update.soliciter_muac_cm",
                    lower_bound=9.5,
                    upper_bound=21.5,
                    num_bins=12,
                    bin_name_prefix="muac",
                )
            ],
            filters={"status": ["approved"]},
            experiment="chc_nutrition",
            terminal_stage=CacheStage.AGGREGATED,
        )
    """

    grouping_key: str
    fields: list[FieldComputation] = field(default_factory=list)
    histograms: list[HistogramComputation] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)
    date_field: str = "visit_date"

    # Optional half-open visit-date window [date_from, date_to) applied at
    # QUERY time. Like `filters`, this is deliberately EXCLUDED from the config
    # hash (see get_config_hash) so a single cache serves every window — the
    # bound is just a WHERE clause on the cached visits. Empty string = no
    # bound on that side. Used by saved-runs snapshots that must reflect the
    # run's period instead of the all-time aggregate (ace#764). Dates are ISO
    # strings (YYYY-MM-DD or full ISO datetime; only the date part is used).
    date_from: str = ""
    date_to: str = ""

    # Pipeline metadata (optional, backwards compatible with defaults)
    experiment: str = ""
    terminal_stage: CacheStage = CacheStage.AGGREGATED

    # Entity linking configuration
    linking_field: str = "entity_id"

    # Data source configuration
    data_source: DataSourceConfig = field(default_factory=DataSourceConfig)

    # Window-function fields evaluated AFTER per-row extraction. Each
    # WindowFieldComputation references already-extracted fields by name and
    # produces one extra value per visit (e.g., distance from previous visit
    # to the same mother). Backwards-compatible: empty list means no window
    # processing, identical to today's behaviour.
    window_fields: list["WindowFieldComputation"] = field(default_factory=list)

    # Cross-pipeline JOINs. Each JoinConfig pulls fields from another pipeline's
    # already-cached visit-level rows (labs_computed_visit_cache filtered by
    # that pipeline's config_hash) into THIS pipeline's row scope. Joined
    # fields become accessible to aggregations via JSONB paths under
    # `joined.<from_alias>.<field>` — no new field kind, just the existing
    # path-based extraction working over a virtually-extended form_json.
    #
    # Concrete use case: visits ⋈ registrations on mother_case_id ↔ case_id
    # to pull phone, age, eligible_full_intervention_bonus, etc., per visit
    # so aggregations can compute phone_dup_pct, age_concentration, etc.
    # per FLW (with pre_aggregate_by mother_case_id for v1 fidelity).
    joins: list["JoinConfig"] = field(default_factory=list)

    # Post-extraction filters: list of {"field": <name>, "op": "is_not_null"}.
    # Applied AFTER extraction (so they can reference extracted columns) and
    # BEFORE window functions (so the LAG/etc. only sees passing rows).
    # Required for v1 fidelity on metrics like avg_case_distance_km, where v1
    # filters visits to GPS-valid BEFORE pairing consecutive visits — without
    # this filter, v3's LAG would land on non-GPS rows and produce NULL
    # distances where v1 successfully pairs the next-valid visit.
    extracted_filters: list[dict] = field(default_factory=list)

    # Pipeline-id discriminator for the raw-visit cache. Multiple pipelines
    # for the same opportunity (e.g. connect_csv visits + cchq_forms
    # registrations + cchq_forms gs_forms in the MBW V2 workflow) need
    # isolated raw caches — without this, each pipeline's `store_raw_visits`
    # used to wholesale DELETE+INSERT for the opp and clobber the previous
    # pipeline's rows. See incident on opp 765 (issue #116). The SQL backend
    # uses this to scope every raw-cache read and write.
    # Optional: legacy callers without a workflow definition id (one-off
    # tests, ad-hoc analyses) can leave it None — the cache then behaves
    # as it did before #116, namely shared across all such callers for
    # the same opp.
    pipeline_id: int | None = None

    def __post_init__(self):
        """Validate configuration."""
        if not self.grouping_key:
            raise ValueError("Grouping key is required")
        if self.terminal_stage == CacheStage.ENTITY and not self.linking_field:
            raise ValueError("linking_field is required when terminal_stage is ENTITY")

        # Warn on FieldComputation names that collide with raw_visit_cache base columns.
        # See RAW_VISIT_BASE_COLUMNS for why this matters — silent shadowing causes
        # downstream type-shape bugs (most notably VisitRow.to_dict crashing on a
        # string `visit_date` because it expects a date with `.isoformat()`). This is
        # a warning rather than a hard error because several existing pipelines
        # (KMC, RUTF, MBW custom-analyses) have pre-existing collisions and would
        # break if we raised. Audit and rename them, then promote to a hard raise.
        collisions = sorted({f.name for f in self.fields} & RAW_VISIT_BASE_COLUMNS)
        if collisions:
            logger.warning(
                "FieldComputation name(s) %s collide with base columns on "
                "labs_raw_visit_cache (experiment=%r). Custom fields silently shadow "
                "the typed base column with their JSONB-extracted (typically string) "
                "value, breaking downstream consumers that assume the base shape "
                "(e.g. VisitRow.to_dict). Rename to namespace the custom field "
                "(e.g. `visit_date` -> `form_visit_date`).",
                collisions,
                self.experiment or "<unnamed>",
            )

        # Note: Empty fields/histograms is valid for basic caching scenarios

        # Validate window fields reference existing extracted fields by name.
        # We do this in __post_init__ rather than at template-load time so the
        # error is raised against the constructed config (close to the failure).
        field_names = {f.name for f in self.fields}
        for wf in self.window_fields:
            for ref_name, ref_value in (
                ("partition_by", wf.partition_by),
                ("order_by", wf.order_by),
                ("lat_field", wf.lat_field),
                ("lon_field", wf.lon_field),
            ):
                if ref_value and ref_value not in field_names and ref_value not in _BASE_VISIT_COLUMNS:
                    raise ValueError(
                        f"WindowFieldComputation {wf.name!r} references "
                        f"{ref_name}={ref_value!r}, but no extracted field or base column has that name."
                    )

    def add_field(self, field_comp: FieldComputation) -> None:
        """Add a field computation to the config."""
        self.fields.append(field_comp)

    def add_histogram(self, hist_comp: HistogramComputation) -> None:
        """Add a histogram computation to the config."""
        self.histograms.append(hist_comp)

    def get_field(self, name: str) -> FieldComputation | None:
        """Get a field computation by name."""
        for field_comp in self.fields:
            if field_comp.name == name:
                return field_comp
        return None

    def get_histogram(self, name: str) -> HistogramComputation | None:
        """Get a histogram computation by name."""
        for hist_comp in self.histograms:
            if hist_comp.name == name:
                return hist_comp
        return None


# Backwards compatibility alias
AnalysisConfig = AnalysisPipelineConfig

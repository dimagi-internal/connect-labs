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

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

AggregationType = Literal[
    "sum", "avg", "count", "min", "max", "list", "first", "last", "count_unique", "count_distinct"
]


class CacheStage(Enum):
    """
    Pipeline stages for analysis caching.

    Determines which stage is the "terminal" output for a given analysis:
    - VISIT_LEVEL: VisitAnalysisResult is the final output (one row per visit)
    - AGGREGATED: FLWAnalysisResult is the final output (one row per FLW/entity)
    """

    VISIT_LEVEL = "visit_level"
    AGGREGATED = "aggregated"


@dataclass
class DataSourceConfig:
    """
    Configuration for where a pipeline fetches its raw data.

    Attributes:
        type: Data source type. "connect_csv" fetches from the Connect production
              paginated JSON export (the literal token name predates the v2 migration
              and is retained for stable identifiers across pipeline templates).
              "cchq_forms" fetches from CommCare HQ Form API.
        form_name: (cchq_forms only) Form name for xmlns discovery,
                   e.g., "Register Mother", "Gold Standard Visit Checklist"
        app_id: (cchq_forms only) Explicit CommCare app ID.
        app_id_source: (cchq_forms only) "opportunity" = derive from opportunity metadata.
        gs_app_id: (cchq_forms only) Explicit GS supervisor app ID.
    """

    type: str = "connect_csv"
    form_name: str = ""
    app_id: str = ""
    app_id_source: str = ""
    gs_app_id: str = ""

    def __post_init__(self):
        if self.type not in ("connect_csv", "cchq_forms"):
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
    filter_value: str = ""  # Optional: value to compare against in filter

    def __post_init__(self):
        """Validate configuration."""
        if not self.name:
            raise ValueError("Field name is required")
        if not self.path and not self.paths and not self.extractor:
            raise ValueError("Field requires path, paths, or extractor")
        if self.aggregation not in [
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
        ]:
            raise ValueError(f"Invalid aggregation type: {self.aggregation}")

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

    # Pipeline metadata (optional, backwards compatible with defaults)
    experiment: str = ""
    terminal_stage: CacheStage = CacheStage.AGGREGATED

    # Entity linking configuration
    linking_field: str = "entity_id"

    # Data source configuration
    data_source: DataSourceConfig = field(default_factory=DataSourceConfig)

    def __post_init__(self):
        """Validate configuration."""
        if not self.grouping_key:
            raise ValueError("Grouping key is required")
        # Note: Empty fields/histograms is valid for basic caching scenarios

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

"""
Analysis configuration for Visit Inspector.

Simple config that downloads raw visit data without any field computations.
Used to populate the SQL cache for ad-hoc querying.
"""

from connect_labs.labs.analysis.config import AnalysisPipelineConfig, CacheStage

# Configuration for raw visit data download
# No field computations needed - we just want the raw form_json cached in SQL
VISIT_INSPECTOR_CONFIG = AnalysisPipelineConfig(
    grouping_key="username",
    fields=[],  # No computed fields needed
    histograms=[],  # No histograms needed
    filters={},  # No filters - download all visits
    date_field="visit_date",
    experiment="explorer_visit_inspector",
    terminal_stage=CacheStage.VISIT_LEVEL,  # We need visit-level data, not aggregated
)

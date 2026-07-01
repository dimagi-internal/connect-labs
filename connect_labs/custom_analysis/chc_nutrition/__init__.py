"""
CHC Nutrition Analysis

Analyzes nutrition-related data from CHC (Community Health Center) programs.
Focuses on child health metrics, MUAC measurements, and diligence checks.
"""

from connect_labs.custom_analysis.chc_nutrition.analysis_config import CHC_NUTRITION_CONFIG
from connect_labs.labs.analysis.config_registry import register_config

# Register config so the FLW analysis API can look it up by name (?config=chc_nutrition)
register_config("chc_nutrition", CHC_NUTRITION_CONFIG)

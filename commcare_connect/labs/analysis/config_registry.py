"""
Analysis config registry for the FLW analysis API.

Lets `/labs/api/analysis/flw/?config=<name>` (and any other named-config lookup)
resolve an AnalysisPipelineConfig by string name. Configs register themselves
at import time (typically in an app's __init__.py) and the FLW analysis API
view looks them up by name.

Usage:
    # Register a config (typically in the analysis module's __init__.py)
    from commcare_connect.labs.analysis.config_registry import register_config
    register_config("chc_nutrition", CHC_NUTRITION_CONFIG)

    # Look up a config by name
    from commcare_connect.labs.analysis.config_registry import get_config
    config = get_config("chc_nutrition")
"""

import logging

from commcare_connect.labs.analysis.config import AnalysisPipelineConfig

logger = logging.getLogger(__name__)

# Registry mapping config names to config objects
_CONFIG_REGISTRY: dict[str, AnalysisPipelineConfig] = {}


def register_config(name: str, config: AnalysisPipelineConfig) -> None:
    """
    Register an analysis config by name.

    Args:
        name: Unique name for the config (used in URL params)
        config: AnalysisPipelineConfig object to register
    """
    if name in _CONFIG_REGISTRY:
        logger.warning(f"Overwriting existing config registration: {name}")
    _CONFIG_REGISTRY[name] = config


def get_config(name: str) -> AnalysisPipelineConfig | None:
    """
    Look up a config by name.

    Args:
        name: Config name to look up

    Returns:
        AnalysisPipelineConfig if found, None otherwise
    """
    return _CONFIG_REGISTRY.get(name)


def list_configs() -> list[str]:
    """
    List all registered config names.

    Returns:
        List of registered config names
    """
    return list(_CONFIG_REGISTRY.keys())

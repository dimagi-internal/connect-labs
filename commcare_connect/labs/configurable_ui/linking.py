"""
Child linking service for grouping visits by beneficiary across opportunities.

This module provides functionality to link multiple visits to the same child/beneficiary
even when visits come from different opportunities or form types.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class LinkingConfig:
    """Configuration for how to link visits to children."""

    identifier_field: str  # Human-readable name (e.g., "kmc_beneficiary_case_id")
    identifier_paths: list[str]  # Form JSON paths to check (in priority order)
    opportunities: list[int]  # Which opportunities to include


class ChildLinkingService:
    """Links visits across opportunities to build child timelines."""

    def __init__(self, config: LinkingConfig):
        self.config = config

    def link_visits(self, visits: list[Any]) -> dict[str, list[Any]]:
        """
        Group visits by child identifier.

        Args:
            visits: List of UserVisit objects to link

        Returns:
            Dictionary mapping child_id to sorted list of visits
        """
        children = {}
        for visit in visits:
            child_id = self._extract_identifier(visit)
            if child_id:
                children.setdefault(child_id, []).append(visit)

        # Sort each child's visits by date
        for visits_list in children.values():
            visits_list.sort(key=lambda v: v.visit_date)

        return children

    def get_child_id_from_visit(self, visit: Any) -> str | None:
        """
        Get child_id for a single visit (for external linking).

        Args:
            visit: UserVisit to extract identifier from

        Returns:
            Child identifier string or None if not found
        """
        return self._extract_identifier(visit)

    def _extract_identifier(self, visit: Any) -> str | None:
        """Extract identifier from visit form JSON using configured paths."""
        form_json = visit.form_json
        for path in self.config.identifier_paths:
            value = self._get_nested(form_json, path)
            if value:
                return str(value)
        return None

    def _get_nested(self, obj: dict, path: str) -> Any:
        """
        Get nested dict value by dot notation path.

        Args:
            obj: Dictionary to navigate
            path: Dot-separated path (e.g., "form.case.@case_id")

        Returns:
            Value at path or None if not found
        """
        keys = path.split(".")
        for key in keys:
            if isinstance(obj, dict):
                obj = obj.get(key)
            else:
                return None
        return obj

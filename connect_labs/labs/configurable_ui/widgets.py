"""
Widget configuration system for composable UI components.

This module provides a framework for defining reusable UI widgets that extract
and display data from form JSON. Widgets can be composed into different layouts
for different programs without code changes.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FieldExtractor:
    """Configuration for extracting a single field from form JSON."""

    display_name: str  # Human-readable label for the field
    form_paths: list[str]  # Paths to try (in priority order)
    transform: str | None = None  # Optional transformation: "float", "date", "kg_to_g"


@dataclass
class WidgetConfig:
    """Configuration for a widget instance."""

    widget_id: str  # Unique identifier (e.g., "visit_history", "weight_chart")
    widget_type: str  # Widget type (e.g., "visit_history", "line_chart", "map", "detail_panel")
    title: str  # Display title for the widget
    field_extractors: dict[str, FieldExtractor]  # field_name -> extractor config
    options: dict[str, Any] = field(default_factory=dict)  # Widget-specific options


@dataclass
class TimelineLayoutConfig:
    """Defines which widgets appear in which columns."""

    left_widgets: list[str]  # Widget IDs for left column
    center_widgets: list[str]  # Widget IDs for center column
    right_widgets: list[str]  # Widget IDs for right column


class BaseWidget:
    """Base widget that extracts data from visits using configured field extractors."""

    def __init__(self, config: WidgetConfig):
        self.config = config

    def extract_field(self, form_json: dict, field_name: str) -> Any:
        """
        Extract a single field from form JSON.

        Args:
            form_json: The form JSON dictionary
            field_name: Name of the field to extract

        Returns:
            Extracted and transformed value, or None if not found
        """
        extractor = self.config.field_extractors.get(field_name)
        if not extractor:
            return None

        # Try each path in priority order
        for path in extractor.form_paths:
            value = self._get_nested(form_json, path)
            if value is not None:
                return self._transform(value, extractor.transform)
        return None

    def extract_all_fields(self, form_json: dict) -> dict[str, Any]:
        """
        Extract all configured fields from form JSON.

        Args:
            form_json: The form JSON dictionary

        Returns:
            Dictionary mapping field names to extracted values
        """
        return {
            field_name: self.extract_field(form_json, field_name) for field_name in self.config.field_extractors.keys()
        }

    def _get_nested(self, obj: dict, path: str) -> Any:
        """
        Navigate nested dictionary using dot notation.

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

    def _transform(self, value: Any, transform: str | None) -> Any:
        """
        Apply transformation to extracted value.

        Args:
            value: Raw value from form
            transform: Transformation type

        Returns:
            Transformed value
        """
        if not transform:
            return value

        if transform == "float":
            try:
                return float(value) if value else None
            except (ValueError, TypeError):
                return None

        if transform == "kg_to_g":
            # KMC weights are already in grams, just convert to int
            try:
                return int(value) if value else None
            except (ValueError, TypeError):
                return None

        if transform == "date":
            return str(value) if value else None

        return value

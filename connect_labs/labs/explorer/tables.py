"""
Django Tables2 table definitions for Labs Data Explorer
"""

import django_tables2 as tables
from django.urls import reverse
from django.utils.html import format_html

from connect_labs.labs.explorer.utils import truncate_json_preview


class LabsRecordTable(tables.Table):
    """Table for displaying LabsRecord data."""

    # Checkbox column for bulk selection
    select = tables.CheckBoxColumn(
        accessor="id",
        attrs={
            "th__input": {
                "@click": "toggleSelectAll()",
                "x-model": "selectAll",
                "name": "select_all",
                "type": "checkbox",
                "class": "checkbox",
            },
            "td__input": {
                "x-model": "selected",
                "name": "record_select",
                "type": "checkbox",
                "class": "checkbox",
            },
        },
        orderable=False,
    )

    id = tables.Column(verbose_name="ID", attrs={"td": {"class": "text-nowrap"}})
    experiment = tables.Column(verbose_name="Experiment")
    type = tables.Column(verbose_name="Type")
    username = tables.Column(verbose_name="User", empty_values=())
    opportunity_id = tables.Column(verbose_name="Opp", attrs={"td": {"class": "text-nowrap"}})
    organization_id = tables.Column(verbose_name="Org", empty_values=(), attrs={"td": {"class": "text-nowrap"}})
    program_id = tables.Column(verbose_name="Prog", empty_values=(), attrs={"td": {"class": "text-nowrap"}})
    data_preview = tables.Column(
        verbose_name="Data",
        accessor="data",
        orderable=False,
        empty_values=(),
    )

    actions = tables.Column(
        verbose_name="Actions",
        empty_values=(),
        orderable=False,
        attrs={"td": {"class": "text-nowrap"}},
    )

    class Meta:
        attrs = {
            "class": "table table-striped",
            "x-data": "{selected: [], selectAll: false}",
            "@change": "updateSelectAll()",
        }
        sequence = (
            "select",
            "id",
            "experiment",
            "type",
            "username",
            "opportunity_id",
            "organization_id",
            "program_id",
            "data_preview",
            "actions",
        )
        empty_text = "No records found."
        orderable = False

    def render_username(self, value, record):
        """Render username field with truncation."""
        if not value:
            return "—"
        # Truncate long usernames
        if len(value) > 20:
            return format_html('<span title="{}">{}&hellip;</span>', value, value[:17])
        return value

    def render_opportunity_id(self, value):
        """Render opportunity ID."""
        return value if value else "—"

    def render_organization_id(self, value):
        """Render organization ID."""
        return value if value else "—"

    def render_program_id(self, value):
        """Render program ID."""
        return value if value else "—"

    def render_data_preview(self, value):
        """Render truncated JSON preview."""
        preview = truncate_json_preview(value, max_length=40)
        return format_html('<code class="text-xs">{}</code>', preview)

    def render_actions(self, record):
        """Render action buttons."""
        edit_url = reverse("explorer:edit", kwargs={"pk": record.id})
        return format_html('<a href="{}" class="btn btn-sm btn-primary">Edit</a>', edit_url)

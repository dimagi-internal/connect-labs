import django_tables2 as tables
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _


class AuditTable(tables.Table):
    """Styled table for displaying experiment-based audit sessions."""

    record_id = tables.Column(
        verbose_name=_("ID"),
        accessor="pk",
        orderable=True,
        attrs={"td": {"class": "text-sm text-gray-600 font-mono"}},
    )

    title = tables.Column(
        verbose_name=_("Title"),
        attrs={
            "td": {
                "class": "text-sm text-brand-deep-purple font-medium whitespace-normal break-words",
            }
        },
    )

    opportunity_name = tables.Column(
        verbose_name=_("Opportunity"),
        orderable=True,
        attrs={"td": {"class": "text-sm text-gray-700 whitespace-normal break-words max-w-xs"}},
    )

    description = tables.Column(
        verbose_name=_("Description"),
        orderable=False,
        attrs={"td": {"class": "text-sm text-gray-600 whitespace-normal break-words max-w-md"}},
    )

    status = tables.Column(
        verbose_name=_("Status"),
        orderable=True,
        attrs={"td": {"class": "whitespace-nowrap"}},
    )

    overall_result = tables.Column(
        verbose_name=_("Result"),
        orderable=True,
        attrs={"td": {"class": "whitespace-nowrap"}},
    )

    visit_count = tables.Column(
        verbose_name=_("Visits"),
        accessor="visit_ids",
        orderable=False,
        attrs={"td": {"class": "text-sm text-gray-600 text-center"}},
    )

    progress = tables.Column(
        verbose_name=_("Progress"),
        accessor="visit_results",
        orderable=False,
        attrs={"td": {"class": "text-center"}},
        empty_values=(),
    )

    actions = tables.Column(
        verbose_name="",
        orderable=False,
        empty_values=(),
        attrs={"td": {"class": "text-right whitespace-nowrap"}},
    )

    class Meta:
        # Note: AuditSessionRecord is not a Django model, it's a Python class
        # So we don't specify model= here
        fields = (
            "record_id",
            "title",
            "opportunity_name",
            "description",
            "status",
            "overall_result",
            "visit_count",
            "progress",
            "actions",
        )
        sequence = fields
        order_by = ("-pk",)
        attrs = {
            "class": "base-table-full",
        }
        empty_text = _("No audit sessions yet. Create your first audit to get started.")

    def render_title(self, value, record):
        """Display title with optional tag."""
        if not value:
            value = _("Untitled Audit")

        tag = record.tag

        if tag:
            return format_html(
                '<div class="flex flex-col">'
                '<span class="text-sm font-semibold text-brand-deep-purple">{}</span>'
                '<span class="text-xs text-gray-500">Tag: {}</span>'
                "</div>",
                value,
                tag,
            )

        return value

    def render_opportunity_name(self, value, record):
        """Display opportunity name, falling back to ID if name not available."""
        if value:
            return value
        if record.opportunity_id:
            return format_html(
                '<span class="text-gray-500">Opportunity #{}</span>',
                record.opportunity_id,
            )
        return "-"

    def render_description(self, value, record):
        """Display the audit description."""
        if value:
            return value
        return format_html('<span class="text-gray-400 italic">No description</span>')

    def render_status(self, value, record):
        """Render status as a badge."""
        status_map = {
            "completed": ("badge badge-sm bg-green-50 text-green-700", _("Completed")),
            "in_progress": ("badge badge-sm bg-orange-50 text-orange-700", _("In Progress")),
        }

        badge_class, text = status_map.get(value, ("badge badge-sm bg-slate-100 text-slate-700", value or "-"))
        return format_html('<span class="{}">{}</span>', badge_class, text)

    def render_overall_result(self, value, record):
        """Render overall result badge."""
        if not value:
            return "-"

        value_lower = value.lower()
        if value_lower == "pass":
            badge_class = "badge badge-sm bg-green-50 text-green-700"
            icon = '<i class="fa-solid fa-thumbs-up mr-1"></i>'
            text = _("Pass")
        elif value_lower == "fail":
            badge_class = "badge badge-sm bg-red-50 text-red-700"
            icon = '<i class="fa-solid fa-thumbs-down mr-1"></i>'
            text = _("Fail")
        else:
            badge_class = "badge badge-sm bg-slate-100 text-slate-700"
            icon = ""
            text = value

        return format_html('<span class="{}">{}{}</span>', badge_class, mark_safe(icon), text)

    def render_visit_count(self, value, record):
        """Display visit count."""
        count = len(value or [])
        return format_html('<span class="text-sm font-medium text-brand-indigo">{}</span>', count)

    def render_progress(self, value, record):
        """Display progress percentage based on assessments."""
        visit_ids = record.visit_ids or []
        visit_results = record.visit_results or {}

        total_visits = len(visit_ids)
        audited_count = 0

        for visit_id in visit_ids:
            visit_key = str(visit_id)
            visit_data = visit_results.get(visit_key)
            if visit_data and visit_data.get("result") in {"pass", "fail"}:
                audited_count += 1

        percentage = round((audited_count / total_visits) * 100, 1) if total_visits else 0

        percentage_str = f"{percentage:.1f}"

        return format_html(
            '<div class="text-center">'
            '<div class="text-sm font-medium text-brand-deep-purple">{}%</div>'
            '<div class="text-xs text-gray-500">({} of {})</div>'
            "</div>",
            percentage_str,
            audited_count,
            total_visits,
        )

    def render_actions(self, record):
        """Render action buttons for the audit session."""
        from urllib.parse import urlencode

        bulk_url = reverse("audit:bulk_assessment", kwargs={"pk": record.pk})
        task_url = reverse("tasks:new")

        # Include opportunity_id in URLs to avoid searching all opportunities
        if record.opportunity_id:
            bulk_url = f"{bulk_url}?opportunity_id={record.opportunity_id}"

            # Build task URL with FLW info from audit session
            # Only pass username if there's exactly one unique FLW in the session
            task_params = {"audit_session_id": record.pk}

            # Collect unique usernames from visit_images
            visit_images = record.data.get("visit_images", {})
            unique_usernames = set()
            for images in visit_images.values():
                for img in images:
                    username = img.get("username")
                    if username:
                        unique_usernames.add(username)

            # Only pass username if exactly one FLW
            if len(unique_usernames) == 1:
                task_params["username"] = unique_usernames.pop()

            task_url = f"{task_url}?{urlencode(task_params)}"

        button_label = _("Review") if record.status == "completed" else _("Open")
        return format_html(
            '<div class="flex gap-2 justify-end">'
            '<a href="{}" class="button button-sm primary-light">'
            '<i class="fa-solid fa-arrow-up-right-from-square mr-1"></i>{}'
            "</a>"
            '<a href="{}" class="button button-sm outline-style text-green-700 border-green-300 hover:bg-green-50">'
            '<i class="fa-solid fa-clipboard-list mr-1"></i>{}'
            "</a>"
            "</div>",
            bulk_url,
            button_label,
            task_url,
            _("Create Task"),
        )

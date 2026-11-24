"""Saved audit setups: an audit configured once in the creation wizard and reused.

A setup is what the wizard collects about HOW to audit -- which visits (a count per
worker, or a window of recent days), which photos and the AI reviewer on each, the
fields shown beside them, the pass mark -- with nothing about WHO: no workers, no
visit ids. Something that wants an audit (an audit review's "AI image audit", today)
names a setup and the workers, and ``setups.run_kwargs`` turns the two into one
``run_audit_creation`` call.

Real labs-DB rows (configuration, no personal data), like ``PriorAuditVerdict``.
Re-exported from ``models.py`` because Django only discovers models reachable from it.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class AuditSetup(models.Model):
    """One reusable audit configuration, made with the creation wizard."""

    name = models.CharField(max_length=200)
    #: The opportunities the setup was built on: its photo paths and fields are those
    #: apps' form paths. Shown beside the setup; never limits where it may be used.
    opportunity_ids = models.JSONField(default=list, blank=True)
    #: Which visits, as ``setups.clean`` keeps them: ``audit_type`` (``last_n_per_flw`` or
    #: ``recent_days``), ``count_per_flw`` / ``days``, ``sample_percentage``,
    #: ``deliver_unit_types``, ``visit_statuses``, ``exclude_prior_audited``,
    #: ``pass_threshold``, ``tag``.
    criteria = models.JSONField(default=dict)
    #: ``[{image_path, reviewers: [{agent_id, config, auto_apply_actions}]}]``, as the wizard builds them.
    image_audits = models.JSONField(default=list, blank=True)
    #: ``[{image_path, field_path, label}]``: fields shown beside each photo.
    context_fields = models.JSONField(default=list, blank=True)
    archived = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_by_name = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self) -> str:
        return f"Audit setup {self.pk}: {self.name}"

"""One config document per scope and namespace, and every change to it.

In the labs DB, not LabsRecords: this set-up has no Connect counterpart, the
supply header reads it on every request (a LabsRecord read is an HTTP call to
Connect for a real programme), and it is authorised the way labs' other owned
data is (`labs/access/scopes.may_use`).
"""

from django.db import models

SCOPE_TYPES = ("organization", "program", "opportunity", "user")


class ScopeConfig(models.Model):
    scope_type = models.CharField(max_length=20, choices=[(t, t) for t in SCOPE_TYPES])
    # An organisation's slug (labs-only organisations have nothing else), a
    # programme's or opportunity's id as text, or a username.
    scope_key = models.CharField(max_length=200)
    namespace = models.CharField(max_length=60)
    data = models.JSONField(default=dict)
    version = models.PositiveIntegerField(default=0)
    updated_by = models.CharField(max_length=150, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["scope_type", "scope_key", "namespace"], name="uniq_scope_config"),
        ]

    def __str__(self):
        return f"{self.namespace} @ {self.scope_type} {self.scope_key} (v{self.version})"


class ScopeConfigChange(models.Model):
    """Append-only. Undo writes a new change whose `after` is an earlier `before`."""

    config = models.ForeignKey(ScopeConfig, on_delete=models.CASCADE, related_name="changes")
    version = models.PositiveIntegerField()
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    changed_by = models.CharField(max_length=150, blank=True, default="")
    changed_at = models.DateTimeField(auto_now_add=True)
    # "settings", "mcp:<tool>", "operation:<name>", "migration", "undo"
    via = models.CharField(max_length=80, blank=True, default="")

    class Meta:
        ordering = ["-id"]

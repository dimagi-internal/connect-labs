"""Saved queries: a person's query on the Data page, kept to run again.

A saved query is the QUERY, never its rows (Scout: "never store query result rows"):
its SQL, what it reads (the page's selection: programs and opportunities, or a
workflow), its row cap and how its result is shown (the agent's ``display``:
``labsdata.display``). Opening one runs it again, live, as the person opening it
(``service.query``, which resolves the selection as them), so it shows
what they may see now. A result card's Save opens the Data page on its query to save
it there (``views.DataView``): the kept result's 24-hour life is untouched.

Saving under a name already saved adds a version (``version`` + 1, ``parent`` the one
before), as Scout versions an artifact; the list shows the latest of each name.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from .validator import DEFAULT_MAX_ROWS


class SavedQuery(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_queries")
    name = models.CharField(max_length=200)
    sql = models.TextField()
    # What it reads, as the Data page selects it: programs and/or opportunities, or one
    # workflow (``{id, opportunity_id | program_id}``), whose pipelines are tables too.
    opportunity_ids = models.JSONField(default=list)
    program_ids = models.JSONField(default=list, blank=True)
    workflow = models.JSONField(default=dict, blank=True)
    max_rows = models.PositiveIntegerField(default=DEFAULT_MAX_ROWS)
    display = models.JSONField(default=dict, blank=True)
    version = models.PositiveIntegerField(default=1)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="next_versions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "name", "version"], name="labsdata_saved_query_version"),
        ]

    def __str__(self) -> str:
        return f"{self.name} (v{self.version})"

    def as_dict(self) -> dict:
        return {
            "id": self.pk,
            "name": self.name,
            "sql": self.sql,
            "selection": {
                "opportunity_ids": self.opportunity_ids,
                "program_ids": self.program_ids,
                "workflow": self.workflow or None,
            },
            "max_rows": self.max_rows,
            "display": self.display or None,
            "version": self.version,
            "created_at": self.created_at.isoformat(),
        }

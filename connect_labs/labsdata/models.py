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

**Data spaces** (``DataSpace``; the design: docs/superpowers/specs/2026-10-24-data-spaces-design.md)
are a named, FIXED set of sources -- opportunities, programs frozen to the opportunities they
had when added, or one workflow -- and the saved queries built on them. A saved query with a
``space`` belongs to the space: anyone who may open the space sees it and may save a new
version of it; without one it is personal and owner-only, as before. Who may open a space is
decided on every read by ``spaces.open`` and nothing else -- there is no membership table and
no grant stored here.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from .validator import DEFAULT_MAX_ROWS


class DataSpace(models.Model):
    """A named, fixed set of sources and the things built on them. Never rows.

    ``opportunity_ids`` is the frozen union of every source's opportunities: a program
    source adds its id to ``program_ids`` and the opportunities it had when added to
    ``opportunity_ids``; a workflow source (``{id, opportunity_id | program_id}``, as
    ``SavedQuery.workflow``) freezes the opportunities it read. Opening a space needs every
    one of them held now (``spaces.open``), so everyone who can open it sees the same
    numbers under the same name. Read it only through ``spaces`` (a test pins that).
    """

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    opportunity_ids = models.JSONField(default=list)
    program_ids = models.JSONField(default=list, blank=True)
    workflow = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="data_spaces"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class SavedQuery(models.Model):
    # With a space, "saved by"; without one, the owner (only they see it).
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_queries")
    space = models.ForeignKey(DataSpace, null=True, blank=True, on_delete=models.CASCADE, related_name="queries")
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
        # Versions count per (owner, name) for a personal query and per (space, name) in a
        # space, where teammates add versions of one another's queries.
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name", "version"],
                condition=models.Q(space__isnull=True),
                name="labsdata_saved_query_version",
            ),
            models.UniqueConstraint(
                fields=["space", "name", "version"],
                condition=models.Q(space__isnull=False),
                name="labsdata_space_query_version",
            ),
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
            "space_id": self.space_id,
            "saved_by": getattr(self.owner, "username", "") if self.space_id else None,
            "created_at": self.created_at.isoformat(),
        }

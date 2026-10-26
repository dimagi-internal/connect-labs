"""Saved queries: a person's query on the Data page, kept to run again.

A saved query is the QUERY, never its rows (Scout: "never store query result rows"):
its SQL, what it reads (the page's selection: programs and opportunities, or a
workflow), its row cap and how its result is shown (the agent's ``display``:
``labsdata.display``). Opening one runs it again, live, as the person opening it
(over a space ``spaces`` generates from its selection, as them), so it shows
what they may see now. A result card's Save opens the Data page on its query to save
it there (``views.DataView``): the kept result's 24-hour life is untouched.

Saving under a name already saved adds a version (``version`` + 1, ``parent`` the one
before), as Scout versions an artifact; the list shows the latest of each name.

**Data spaces** (``DataSpace``; the design: docs/superpowers/specs/2026-10-24-data-spaces-design.md)
are a named, FIXED set of sources -- opportunities, programs frozen to the opportunities they
had when added, or one workflow -- and the saved queries built on them. A saved query with a
``space`` belongs to the space: anyone who may open the space sees it and may save a new
version of it; without one it is personal and owner-only, as before. Who may open a space is
decided on every read by ``spaces.open`` and nothing else: the data rule (every source held
now), then the space's ``audience``, which can only narrow it. ``managers`` are a role inside
the space (who may change its sources, audience and managers), never a grant: a manager is
held to both rules like anyone else.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from .validator import DEFAULT_MAX_ROWS


class DataSpace(models.Model):
    #: Who, among the people who hold every source, may open the space (``spaces.AUDIENCES``).
    HOLDERS = "holders"
    PRIVATE = "private"
    AUDIENCE_CHOICES = [
        (HOLDERS, "Anyone with access to the sources"),
        (PRIVATE, "Only managers"),
    ]

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
    #: Narrows who may open the space, after the data rule; it can never widen it.
    audience = models.CharField(max_length=20, choices=AUDIENCE_CHOICES, default=HOLDERS)
    #: Who may change the sources, audience and managers and archive the space. A role, not
    #: a grant: ``spaces.open`` holds a manager to the data rule like anyone else.
    managers = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="managed_data_spaces")

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


class SpaceArtifact(models.Model):
    """A small labs page kept in a data space: render code and the named queries it reads.

    ``code`` is JSX render code, stored as a workflow's ``render_code`` is (the source text,
    nothing compiled), and drawn ONLY by the workflow sandbox (the opaque-origin frame,
    ``workflow/sandbox.py``, ``components/workflow/sandbox/``) -- never on the Data page
    itself. ``queries`` is ``[{name, sql, max_rows}]``: each checked against the space as a
    run would be when it is saved (``service.artifact_queries``) and again every time the
    artifact is opened, when the host runs them over the space, as the person opening it,
    and hands the frame their rows as the render's ``pipelines`` (the same prop a workflow's
    render code reads). **No rows are stored**: opening an artifact runs it again, live.

    It belongs to the space and inherits the space's rules, as a saved query does: listed,
    opened, saved and deleted only through ``spaces.open``; any member may add a version
    (per space and name, like ``SavedQuery``), a member deletes one whose every version they
    saved, a manager any.
    """

    space = models.ForeignKey(DataSpace, on_delete=models.CASCADE, related_name="artifacts")
    name = models.CharField(max_length=200)
    version = models.PositiveIntegerField(default=1)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="next_versions")
    #: JSX render code, as a workflow's render_code.
    code = models.TextField()
    #: ``[{name, sql, max_rows}]``: what the render code reads, as ``pipelines[name]``.
    queries = models.JSONField(default=list)
    saved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="saved_space_artifacts"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["space", "name", "version"], name="labsdata_space_artifact_version"),
        ]

    def __str__(self) -> str:
        return f"{self.name} (v{self.version})"

    def as_dict(self, *, code: bool = True) -> dict:
        out = {
            "id": self.pk,
            "kind": "artifact",
            "name": self.name,
            "version": self.version,
            "queries": [dict(q) for q in self.queries or []],
            "space_id": self.space_id,
            "saved_by": getattr(self.saved_by, "username", "") or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if code:
            out["code"] = self.code
        return out


class SpaceConversation(models.Model):
    """A POINTER to a canopy chat session, kept in a data space ("Saved conversations").

    Exactly what it says and nothing more: canopy's session id, a label the saver typed,
    who saved it and when. **No transcript, title or words of the conversation are ever
    stored here** -- a private conversation's words are sealed to its people (canopy
    itself cannot read them) and labs is not one of them. The default label is
    "Conversation, <date>", never canopy's title.

    It belongs to the space and inherits the space's rules: listed and saved only through
    ``spaces.open`` (the data rule, then the audience); any member saves one, a member
    deletes their own, a manager any. A pointer grants nothing: opening it asks canopy to
    open the session in the panel, as the person, and canopy's session ACL decides.
    """

    space = models.ForeignKey(DataSpace, on_delete=models.CASCADE, related_name="conversations")
    #: canopy's id for the chat session.
    session_id = models.UUIDField()
    label = models.CharField(max_length=200)
    saved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="saved_space_conversations"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["space", "session_id"], name="labsdata_space_conversation_once"),
        ]

    def __str__(self) -> str:
        return self.label

    def as_dict(self) -> dict:
        return {
            "id": self.pk,
            "session_id": str(self.session_id),
            "label": self.label,
            "saved_by": getattr(self.saved_by, "username", "") or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class PageHold(models.Model):
    """What one drawn page lets its agent read: the page's own selection (``page_holds``).

    Written when the canopy panel on that page mints a grant, from what labs signed into the
    panel's token URL when it drew the page -- never from anything the agent sends -- and, on
    the plain Data page, when the person changes the selection in their own session. Keyed by
    one drawing of the page (a SHA-256 of its page token and the selection signed beside it:
    ``page_holds.drawing``), so it belongs to one drawing of one page for one person.
    Ids only, never rows.
    """

    page_token_checksum = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    #: The page key the token was signed for (``labs.canopy.PAGE_SCOPES``).
    page_key = models.CharField(max_length=100)
    #: A data space's page: the space it is.
    space_id = models.IntegerField(null=True, blank=True)
    #: A workflow run page (or a Data page on a workflow): ``{id, opportunity_id | program_id}``.
    workflow = models.JSONField(default=dict, blank=True)
    #: The opportunities the page reads, resolved as the person; ``None`` only for a Data
    #: page on a workflow, whose opportunities are the workflow's own (resolved per call).
    opportunity_ids = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.page_key} for user {self.user_id}"


class PageGrant(models.Model):
    """One grant canopy was given on a page: the ID-JAG's ``jti``, which the delegated token
    it is redeemed for keeps as ``grant_jti`` (the canopy SDK's ``DelegatedToken``). The MCP
    looks the call's page up through it."""

    grant_jti = models.CharField(max_length=64, unique=True)
    hold = models.ForeignKey(PageHold, on_delete=models.CASCADE, related_name="grants")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"grant {self.grant_jti[:8]} on {self.hold_id}"


# --- de-identified exports (dictionary.py, exports.py) -----------------------------------
#
# A data space's DICTIONARY says, for every field its visits carry, what it means and what
# an export does with it; an EXPORT is one run of that dictionary over the space's visits,
# checked and signed off before anyone can download it. Both belong to the space and are
# read only through ``spaces.open`` (``dictionary.for_space`` / ``exports.for_space``).
# Design: docs/superpowers/specs/2026-10-26-deidentified-exports-design.md


def _new_secret() -> bytes:
    import secrets

    return secrets.token_bytes(32)


class DataDictionary(models.Model):
    """What each field of a space's visits means, and what an export does with it.

    ``secret`` keys every pseudonym and date shift (``labs.deid.transforms``): the same case
    gets the same code in every export of this dictionary, and nobody without the key can
    reverse one. It never leaves labs and is never shown."""

    space = models.OneToOneField(DataSpace, on_delete=models.CASCADE, related_name="dictionary")
    secret = models.BinaryField(default=_new_secret, editable=False)
    #: The semantic registry (its LabsRecord id) whose properties inherit settings from
    #: these fields (``lineage.py``). Optional.
    registry_id = models.IntegerField(null=True, blank=True)
    scanned_at = models.DateTimeField(null=True, blank=True)
    scanned_visits = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"dictionary of space {self.space_id}"


class DictionaryEntry(models.Model):
    """One data element: a form question (every spelling of it, in every app build), or
    one column of ``visits``. Exported only once a person has reviewed it: until then it
    is dropped, whatever its setting says ("drop unless allowed")."""

    VISIT = "visit"
    CASE = "case"
    LEVELS = [(VISIT, "Visit"), (CASE, "Case")]

    dictionary = models.ForeignKey(DataDictionary, on_delete=models.CASCADE, related_name="entries")
    #: ``fieldscan.normalise`` of the path, or ``@<column>`` for a visits column.
    key = models.CharField(max_length=500)
    label = models.CharField(max_length=500)
    #: A visits column (``engine.VISIT_COLUMNS``) rather than a form field.
    column = models.CharField(max_length=100, blank=True, default="")
    #: What the values look like (``labs.deid.classify.KINDS``), from the last scan.
    kind = models.CharField(max_length=20)
    meaning = models.TextField(blank=True, default="")
    level = models.CharField(max_length=10, choices=LEVELS, default=VISIT)
    setting = models.CharField(max_length=20, default="drop")
    params = models.JSONField(default=dict, blank=True)
    #: What the classifier suggests for ``kind`` (``classify.default_setting``).
    suggested_setting = models.CharField(max_length=20, default="drop")
    suggested_params = models.JSONField(default=dict, blank=True)
    #: Can single someone out in combination with other fields (a date, a worker).
    quasi = models.BooleanField(default=False)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["column", "key"]
        constraints = [models.UniqueConstraint(fields=["dictionary", "key"], name="labsdata_dictionary_entry_key")]

    def __str__(self) -> str:
        return self.label

    @property
    def reviewed(self) -> bool:
        return self.reviewed_at is not None


class DictionarySource(models.Model):
    """One spelling of an entry's question in one form: the exact path, the deliver unit
    it was submitted under, and the app builds it was seen in. Never a value."""

    entry = models.ForeignKey(DictionaryEntry, on_delete=models.CASCADE, related_name="sources")
    path = models.CharField(max_length=500)
    deliver_unit = models.CharField(max_length=200, blank=True, default="")
    builds = models.JSONField(default=list, blank=True)
    visits = models.PositiveIntegerField(default=0)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["path", "deliver_unit"]
        constraints = [
            models.UniqueConstraint(fields=["entry", "path", "deliver_unit"], name="labsdata_dictionary_source_once")
        ]

    def __str__(self) -> str:
        return f"{self.path} ({self.deliver_unit or 'any form'})"


class DictionaryFlag(models.Model):
    """Something a scan found that a person should look at: a new field, a new spelling of
    a known one, a field a newer app build stopped sending, or values that now look more
    sensitive than the setting assumes. Paths and build numbers only, never values."""

    NEW_FIELD = "new_field"
    NEW_SPELLING = "new_spelling"
    GONE = "gone"
    KIND_CHANGED = "kind_changed"
    KINDS = [
        (NEW_FIELD, "New field"),
        (NEW_SPELLING, "New spelling of a known field"),
        (GONE, "No longer sent by the newest app build"),
        (KIND_CHANGED, "Values look more sensitive than before"),
    ]

    dictionary = models.ForeignKey(DataDictionary, on_delete=models.CASCADE, related_name="flags")
    entry = models.ForeignKey(DictionaryEntry, null=True, blank=True, on_delete=models.CASCADE, related_name="flags")
    kind = models.CharField(max_length=20, choices=KINDS)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class DictionaryChange(models.Model):
    """Who changed what in a dictionary, and from what. One row per change, never edited."""

    dictionary = models.ForeignKey(DataDictionary, on_delete=models.CASCADE, related_name="changes")
    entry_key = models.CharField(max_length=500, blank=True, default="")
    action = models.CharField(max_length=30)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]


class SpaceExport(models.Model):
    """One de-identified export of a space: the dictionary as it stood when asked for,
    the visits it covered, what the checks found, and who signed it off.

    Nothing is downloadable until a manager of the space other than the person who asked
    signs it off, and then only for ``EXPIRY`` -- after which the file is deleted. The
    file never holds a value the dictionary did not allow (``exports.build``)."""

    QUEUED = "queued"
    BUILDING = "building"
    BLOCKED = "blocked"  # untagged fields, or a check failed: nothing to sign
    READY = "ready"  # built and checked: waiting for sign-off
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"
    EXPIRED = "expired"
    STATUSES = [
        (QUEUED, "Queued"),
        (BUILDING, "Building"),
        (BLOCKED, "Blocked"),
        (READY, "Waiting for sign-off"),
        (APPROVED, "Signed off"),
        (REJECTED, "Rejected"),
        (FAILED, "Failed"),
        (EXPIRED, "Expired"),
    ]

    space = models.ForeignKey(DataSpace, on_delete=models.CASCADE, related_name="exports")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    #: The opportunities the requester held when they asked (the space's sources then), as
    #: ``{id, name, llo}``: exactly what the build reads, checked as them when they asked.
    opportunities = models.JSONField(default=list)
    #: The dictionary's entries as they stood when asked for: what this export applies.
    entries = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=STATUSES, default=QUEUED)
    source_rows = models.PositiveIntegerField(default=0)
    rows = models.PositiveIntegerField(default=0)
    checks = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")
    file_name = models.CharField(max_length=300, blank=True, default="")
    built_at = models.DateTimeField(null=True, blank=True)
    signed_off_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    signed_off_at = models.DateTimeField(null=True, blank=True)
    signoff_note = models.TextField(blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"export {self.pk} of space {self.space_id}"

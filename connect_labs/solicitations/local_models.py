"""Solicitations and responses that live in the labs database.

The solicitations app was built for exactly this and has never been used in
anger, for one reason: ``ResponseRecord.llo_entity_id`` is a free string. There
was no real organisation to point it at, because most applicants never reach
Connect — an organisation that answers an EOI and is not selected gets no
Connect row at all. Making that a foreign key is what turns application history
into something a directory can join on.

**Two stores, one vocabulary.** ``models.py`` holds proxy models over the prod
LabsRecord API and keeps serving live programme solicitations, unchanged. These
are local tables for the rounds the marketplace owns: historical EOIs imported
from Google Forms, where there is no programme and no Connect membership to
scope a prod write by. The *field names are deliberately identical*, so there is
one EOI vocabulary in this codebase rather than two, and so a round can migrate
between stores without anything downstream being rewritten.

Provenance rides alongside the shared vocabulary rather than inside it, so the
shape stays portable when intake eventually moves off Google Forms.
"""

from django.db import models

from connect_labs.labs.models import LabsOrg

SOLICITATION_TYPES = [("eoi", "Expression of Interest"), ("rfp", "Request for Proposal")]
STATUSES = [("draft", "Draft"), ("active", "Active"), ("closed", "Closed")]

ACCESS_UNKNOWN = "unknown"
ACCESS_OK = "ok"
ACCESS_DENIED = "denied"
ACCESS_MISSING = "missing"
ACCESS_STATES = [
    (ACCESS_UNKNOWN, "not checked"),
    (ACCESS_OK, "readable"),
    (ACCESS_DENIED, "no access"),
    (ACCESS_MISSING, "no response sheet recorded"),
]


class Solicitation(models.Model):
    """One EOI or RFP round — one *form*, not one announcement.

    A single announcement can run several forms: the Malaria RFI ran four, and
    the 2026 Readers round ran an English and a French one. Modelling the
    announcement as the round made those invisible, because a round with no
    responses of its own is not a round.
    """

    slug = models.SlugField(max_length=120, unique=True)

    # --- the shared solicitation vocabulary (see SolicitationRecord) ---
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True, default="")
    scope_of_work = models.TextField(blank=True, default="")
    solicitation_type = models.CharField(max_length=8, choices=SOLICITATION_TYPES, default="eoi")
    status = models.CharField(max_length=16, choices=STATUSES, default="closed")
    application_deadline = models.DateField(null=True, blank=True)
    expected_start_date = models.DateField(null=True, blank=True)
    expected_end_date = models.DateField(null=True, blank=True)
    estimated_scale = models.CharField(max_length=200, blank=True, default="")
    contact_email = models.EmailField(max_length=320, blank=True, default="")
    questions = models.JSONField(default=list, blank=True)
    evaluation_criteria = models.JSONField(default=list, blank=True)

    # --- provenance: alongside, never inside ---
    published_on = models.DateField(null=True, blank=True)
    decision_on = models.DateField(null=True, blank=True)
    target_countries = models.CharField(max_length=500, blank=True, default="")
    announcement_url = models.CharField(max_length=1000, blank=True, default="")
    form_url = models.CharField(max_length=1000, blank=True, default="")
    response_spreadsheet_id = models.CharField(max_length=120, blank=True, default="")
    response_tab = models.CharField(max_length=120, blank=True, default="")
    column_map = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True, default="")

    # Written by `marketplace_import --check-access`, which verifies a real read
    # rather than trusting a claim. A round that has never been read says so,
    # instead of rendering as an unpopular one.
    sa_access_state = models.CharField(max_length=12, choices=ACCESS_STATES, default=ACCESS_UNKNOWN)
    sa_access_checked_at = models.DateTimeField(null=True, blank=True)
    last_ingested_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_on", "title"]

    def __str__(self) -> str:
        return f"{self.title} ({self.get_solicitation_type_display()})"

    @property
    def type_label(self) -> str:
        return dict(SOLICITATION_TYPES).get(self.solicitation_type, self.solicitation_type.upper())

    @property
    def was_ingested(self) -> bool:
        """Whether responses have ever been read for this round.

        The distinction the directory must show: "nobody applied" and "we have
        never been able to open the sheet" look identical otherwise, and only
        one of them is a fact about the world.
        """
        return self.last_ingested_at is not None


class SolicitationResponse(models.Model):
    """One submission to a round.

    ``responses`` keeps the whole submitted row verbatim, keyed to the round's
    derived question list. The extracted fields beside it are an index for
    matching and filtering, not the record: across the rounds inspected no two
    forms share a question set, one sheet has two blank column headers and
    another asks for "Email Address" twice, so any schema invented to fit them
    would be wrong for the next round — and a dropped answer cannot be
    re-collected.
    """

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="responses")

    # The fix this whole project turns on: a real relation, not a free string.
    llo_entity = models.ForeignKey(
        LabsOrg, null=True, blank=True, on_delete=models.SET_NULL, related_name="solicitation_responses"
    )
    llo_entity_name = models.CharField(max_length=300, blank=True, default="")
    org_name = models.CharField(max_length=300, blank=True, default="")

    responses = models.JSONField(default=dict, blank=True)
    submitted_by_name = models.CharField(max_length=200, blank=True, default="")
    submitted_by_email = models.CharField(max_length=500, blank=True, default="")
    submission_date = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, default="submitted")

    # --- provenance ---
    source_row = models.IntegerField()
    source_url = models.CharField(max_length=1000, blank=True, default="")
    country_as_submitted = models.CharField(max_length=300, blank=True, default="")
    website_as_submitted = models.CharField(max_length=500, blank=True, default="")

    MATCH_UNMATCHED = "unmatched"
    MATCH_EMAIL = "email"
    MATCH_NAME = "name"
    MATCH_HUMAN = "human"
    # Not an absence but a decision: some submissions are not organisations at
    # all, and without a way to say so they sit in the review queue for ever,
    # indistinguishable from work nobody has got to yet.
    MATCH_NOT_LLO = "not_an_llo"
    MATCH_STATES = [
        (MATCH_UNMATCHED, "not matched"),
        (MATCH_EMAIL, "matched on a contact email"),
        (MATCH_NAME, "matched on organisation name"),
        (MATCH_HUMAN, "attributed by a person"),
        (MATCH_NOT_LLO, "not an organisation — dismissed by a person"),
    ]
    match_state = models.CharField(max_length=12, choices=MATCH_STATES, default=MATCH_UNMATCHED)
    match_basis = models.TextField(blank=True, default="")

    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submission_date", "source_row"]
        constraints = [
            models.UniqueConstraint(fields=["solicitation", "source_row"], name="uniq_response_per_source_row"),
        ]
        indexes = [models.Index(fields=["match_state"])]

    def __str__(self) -> str:
        return f"{self.org_name or self.llo_entity_name or 'a submission'} → {self.solicitation.slug}"

"""Solicitations, responses and reviews: one store, the labs database.

Solicitations used to be LabsRecords on prod Connect, scoped by whichever
programme was selected when one was created. That store could not keep a
response confidential to the organisation that asked for it (a respondent is not
a member of the owner's programme, so every response was written public), and
it could not name an owner that has no Connect organisation. These tables can:
a call is owned by a ``LabsOrg``, and who may do what to it is decided in one
place, ``solicitations/access.py``.

Three kinds of row share the tables, told apart by ``source``:

* ``directory`` -- the marketplace's EOI rounds, imported from the LLO
  Directory sheet (``marketplace_import --eoi``). The marketplace pages read
  only these, because they show applicants to every signed-in user.
* ``labs`` -- calls created in labs (the web form, MCP, agents).
* ``labs_record`` -- reserved for calls brought over from the old prod
  LabsRecords, keyed by ``legacy_record_id``. None were: the records there were
  ACE test output, left where they were (decided 2026-10-15).

``ResponseRecord.llo_entity_id`` was a free string; here it is a real foreign
key to the respondent's organisation, which is what turns application history
into something a directory can join on. Provenance rides alongside the shared
vocabulary rather than inside it.

See ``docs/superpowers/specs/2026-10-15-solicitations-owned-by-organisations-design.md``.
"""

import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from connect_labs.labs.models import LabsOrg
from connect_labs.solicitations import vocabulary

SOLICITATION_TYPES = [("eoi", "Expression of Interest"), ("rfp", "Request for Proposal")]
STATUSES = [("draft", "Draft"), ("active", "Active"), ("closed", "Closed"), ("awarded", "Awarded")]

SOURCE_DIRECTORY = "directory"
SOURCE_LABS = "labs"
SOURCE_LABS_RECORD = "labs_record"
SOURCES = [
    (SOURCE_DIRECTORY, "EOI round from the LLO Directory sheet"),
    (SOURCE_LABS, "created in labs"),
    (SOURCE_LABS_RECORD, "imported from a prod LabsRecord"),
]

RESPONSE_STATUSES = [
    ("draft", "Draft"),
    ("submitted", "Submitted"),
    ("under_review", "Under review"),
    ("awarded", "Awarded"),
    ("rejected", "Rejected"),
]
AWARD_STAGES = ("verification", "full")

# Prod LabsRecord ids are reused as /solicitations/<id>/ URLs for imported calls
# (``legacy_record_id``), so local ids start above every prod id. Migration 0014
# moves the id sequences up to this floor; the import raises them further if a
# prod id ever exceeds it.
LOCAL_ID_FLOOR = 1_000_000


class CallableBool:
    """A bool that can also be called, so ``x.can_accept_responses`` and
    ``x.can_accept_responses()`` both work -- the same bridge Django used when
    ``is_authenticated`` became a property. The proxies exposed a method; the
    model exposes a property; old callers keep working."""

    def __init__(self, value: bool):
        self.value = bool(value)

    def __bool__(self):
        return self.value

    def __call__(self):
        return self.value

    def __eq__(self, other):
        return self.value == bool(other)

    def __hash__(self):
        return hash(self.value)

    def __repr__(self):
        return f"CallableBool({self.value})"


def _iso(value):
    return value.isoformat() if value else None


class SolicitationQuerySet(models.QuerySet):
    def directory_rounds(self):
        """The marketplace's EOI rounds only -- never a call made or imported for an owner."""
        return self.filter(source=SOURCE_DIRECTORY)

    def calls(self):
        """Everything that is not a directory round: the solicitations app's own calls."""
        return self.exclude(source=SOURCE_DIRECTORY)


class SolicitationResponseQuerySet(models.QuerySet):
    def directory_rounds(self):
        """Submissions to the marketplace's EOI rounds only.

        A response to an owned call is confidential to its owner; the
        marketplace shows applications to every signed-in user, so it reads
        only these.
        """
        return self.filter(solicitation__source=SOURCE_DIRECTORY)


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

    objects = SolicitationQuerySet.as_manager()

    # Generated from the title for a call made in labs (see `save`); the
    # directory import sets its own.
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    source = models.CharField(max_length=16, choices=SOURCES, default=SOURCE_DIRECTORY, db_index=True)

    # --- ownership: who asked, and therefore who may read the answers ---
    # PROTECT: deleting an organisation must not silently delete its calls and
    # every applicant's answers with them. Null only for historical rounds
    # (directory rounds, and prod calls whose programme had no runner): those
    # only staff manage.
    owner_org = models.ForeignKey(
        LabsOrg, null=True, blank=True, on_delete=models.PROTECT, related_name="owned_solicitations"
    )
    # The Connect programme this call is for, if any: microplan links, fund
    # allocation and `list_solicitations(program_id=)` filter on it. Not a scope.
    program_id = models.IntegerField(null=True, blank=True, db_index=True)
    program_name = models.CharField(max_length=300, blank=True, default="")
    # Appears in the public directories. Unlisted calls are still readable by
    # their owner and by anyone who already responded to them.
    listed = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    fund_id = models.IntegerField(null=True, blank=True, db_index=True)
    connect_opportunity_id = models.IntegerField(null=True, blank=True)
    # ISO 4217 code for the award amount ("" = not stated). The award page
    # shows the call's own currency, never an assumed one.
    currency = models.CharField(max_length=8, blank=True, default="")
    plans = models.JSONField(default=list, blank=True)
    source_program_id = models.IntegerField(null=True, blank=True)
    source_group_id = models.IntegerField(null=True, blank=True)
    source_plan_ids = models.JSONField(default=list, blank=True)
    # Set once the rubric is reviewed and locked: from then on the weighted
    # criteria cannot change (enforced in data_access), so "how you'll be
    # scored" on the public call is how scoring runs.
    criteria_locked_at = models.DateTimeField(null=True, blank=True)
    # The prod LabsRecord this call was imported from. /solicitations/<id>/
    # resolves a legacy id first, so links ACE already sent keep working.
    legacy_record_id = models.IntegerField(null=True, blank=True, unique=True)

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
    # A marketplace round's criteria are written after its submissions are
    # read, not published with the call: the Google Form went out without
    # them. `rubric_version` changes whenever they are replaced, so a review
    # can say which version it scored against; `criteria_basis` says how they
    # were arrived at and what was considered and dropped.
    rubric_version = models.CharField(max_length=40, blank=True, default="")
    criteria_basis = models.TextField(blank=True, default="")
    # The round's written summary (a shortlist report, usually a Google Doc):
    # the reasoning lives there, so the round links to it rather than copying it.
    summary_url = models.URLField(max_length=500, blank=True, default="")

    # Connect's own `delivery_type` — the same vocabulary the pulse spine tags
    # every opportunity with, so "applied to a malaria round" and "delivered
    # malaria" are the same word and can be compared. Blank means nobody has
    # decided yet; `PROGRAM_NONE` means a human decided it is not a delivery
    # program at all (a matching grant is a funding instrument, not a service).
    delivery_type = models.CharField(max_length=48, blank=True, default="")

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

    @property
    def program_label(self) -> str:
        """Connect's name for the program this round is for, or "" if untagged.

        A property rather than a stored string: the tag is the slug, and the
        name for a slug is one decision held in one place. Imported lazily so
        that solicitations keeps no import-time dependency on marketplace.
        """
        from connect_labs.marketplace.programs import label

        return label(self.delivery_type) if self.delivery_type else ""

    def __str__(self) -> str:
        return f"{self.title} ({self.get_solicitation_type_display()})"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self.unique_slug(self.title)
        super().save(*args, **kwargs)

    @classmethod
    def unique_slug(cls, title: str) -> str:
        """A slug from the title with a short random tail, so two calls can share a title."""
        base = (slugify(title or "") or "call")[:100].strip("-") or "call"
        while True:
            slug = f"{base}-{secrets.token_hex(3)}"
            if not cls.objects.filter(slug=slug).exists():
                return slug

    @property
    def type_label(self) -> str:
        return dict(SOLICITATION_TYPES).get(self.solicitation_type, self.solicitation_type.upper())

    # --- the names the prod proxy (SolicitationRecord) exposed, kept so
    # templates, agents and MCP serializers read the same words ---

    @property
    def is_public(self) -> bool:
        return bool(self.listed)

    @property
    def criteria_locked(self) -> bool:
        return self.criteria_locked_at is not None

    @property
    def owner_name(self) -> str:
        return self.owner_org.name if self.owner_org_id else ""

    @property
    def created_by_username(self) -> str:
        return self.created_by.username if self.created_by_id else ""

    @property
    def deadline_passed(self) -> bool:
        return bool(self.application_deadline and self.application_deadline < timezone.localdate())

    @property
    def can_accept_responses(self) -> CallableBool:
        """Open for responses: active, and the deadline (if any) is today or later."""
        return CallableBool(self.status == "active" and not self.deadline_passed)

    @property
    def status_label(self) -> str:
        if self.status == "active" and self.deadline_passed:
            return "Closed — deadline passed"
        return dict(STATUSES).get(self.status, self.status)

    @property
    def public_id(self) -> int:
        """The id this call is addressed by in URLs: its prod id if imported, else its pk."""
        return self.legacy_record_id or self.pk

    def to_data(self) -> dict:
        """The record's application fields, in the shape the prod ``data`` blob had."""
        return {
            "title": self.title,
            "description": self.description,
            "scope_of_work": self.scope_of_work,
            "solicitation_type": self.solicitation_type,
            "status": self.status,
            "application_deadline": _iso(self.application_deadline),
            "expected_start_date": _iso(self.expected_start_date),
            "expected_end_date": _iso(self.expected_end_date),
            "estimated_scale": self.estimated_scale,
            "contact_email": self.contact_email,
            "program_name": self.program_name,
            "connect_opportunity_id": self.connect_opportunity_id,
            "fund_id": self.fund_id,
            "currency": self.currency,
            "questions": self.questions or [],
            "evaluation_criteria": self.evaluation_criteria or [],
            "created_by": self.created_by_username,
            "plans": self.plans or [],
            "source_program_id": self.source_program_id,
            "source_group_id": self.source_group_id,
            "source_plan_ids": self.source_plan_ids or [],
            "criteria_locked": self.criteria_locked,
            "criteria_locked_at": _iso(self.criteria_locked_at),
        }

    def to_record_dict(self) -> dict:
        """Flat dict in the shape the MCP tools always returned (envelope + data)."""
        owner_slug = self.owner_org.slug if self.owner_org_id else ""
        return {
            "id": self.pk,
            "experiment": str(self.program_id) if self.program_id else owner_slug,
            "type": "solicitation",
            "program_id": self.program_id,
            "labs_record_id": None,
            **self.to_data(),
            "is_public": self.is_public,
            "owner_org": owner_slug,
            "owner_org_name": self.owner_name,
            "legacy_record_id": self.legacy_record_id,
        }

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

    objects = SolicitationResponseQuerySet.as_manager()

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
    status = models.CharField(max_length=16, choices=RESPONSE_STATUSES, default="submitted")

    # The signed-in user who submitted it: the respondent, who may read and edit
    # it (until submitted) and may never review or award it. Null for directory
    # imports, prod imports whose user is unknown, and responses an owner
    # entered on a firm's behalf.
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="solicitation_responses_submitted",
    )
    selected_plan_ids = models.JSONField(default=list, blank=True)
    selected_plan_names = models.JSONField(default=list, blank=True)
    # Invented by the call's owner to exercise the call end to end (an agent's
    # test run): never a real firm's answer. Set only when the owner enters the
    # response on an unlisted call, at creation, and never changed after. Only
    # a synthetic response may be given a demonstration award, and it is never
    # given any other kind.
    synthetic = models.BooleanField(default=False)
    # Per-criterion AI suggestions, hidden until the reviewer scores that
    # criterion: {criterion_id: {"score": 1-10, "rationale": str}}.
    ai_proposed_scores = models.JSONField(default=dict, blank=True)

    # --- the award ---
    reward_budget = models.IntegerField(null=True, blank=True)
    award_stage = models.CharField(max_length=16, blank=True, default="verification")
    awarded_at = models.DateTimeField(null=True, blank=True)
    # True only when the award email was actually handed to the mail path.
    awardee_notified = models.BooleanField(default=False)
    # A demonstration award: an owner's explicitly requested award of a
    # `synthetic` response on an unlisted call, the one award allowed on the
    # awarder's own side (`access.can_demonstrate`). It sends no mail and
    # allocates no fund; it says so wherever the award is shown.
    demonstration_award = models.BooleanField(default=False)
    # The Connect organisation id the awarder named for the contract.
    org_id = models.CharField(max_length=120, blank=True, default="")

    legacy_record_id = models.IntegerField(null=True, blank=True, unique=True)
    created_at = models.DateTimeField(default=timezone.now)

    # --- provenance ---
    # The directory-sheet row a round's submission came from. Null for web
    # submissions; the (solicitation, source_row) constraint still holds for
    # imports, because NULLs never collide.
    source_row = models.IntegerField(null=True, blank=True)
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
            # One draft per person per call: a second save edits the first,
            # even when two requests race (`create_response` re-fetches).
            models.UniqueConstraint(
                fields=["solicitation", "submitted_by"],
                condition=models.Q(status="draft"),
                name="uniq_draft_per_respondent",
            ),
        ]
        indexes = [models.Index(fields=["match_state"])]

    def __str__(self) -> str:
        return f"{self.org_name or self.llo_entity_name or 'a submission'} → {self.solicitation.slug}"

    # --- the names the prod proxy (ResponseRecord) exposed ---

    @property
    def submitted_by_username(self) -> str:
        return self.submitted_by.username if self.submitted_by_id else ""

    @property
    def is_awarded(self) -> bool:
        return self.status == "awarded"

    @property
    def blind_label(self) -> str:
        """Identity-free label used everywhere scoring happens."""
        return f"Response #{self.pk}"

    @property
    def display_name(self) -> str:
        """A real name for the respondent, never a placeholder."""
        return (
            (self.llo_entity.name if self.llo_entity_id else "")
            or (self.llo_entity_name or "").strip()
            or (self.org_name or "").strip()
            or (self.submitted_by_name or "").strip()
            or (self.submitted_by_email or "").strip()
        )

    @property
    def account_email(self) -> str:
        """Where mail about this response goes: the respondent's own account.

        Never the address typed on the form (``submitted_by_email``), which is a
        contact for the owner to read and could be anyone's. A response entered
        on a firm's behalf has no account, and its address was typed by the
        owner, so that one is used.
        """
        if self.submitted_by_id:
            return self.submitted_by.email or ""
        return self.submitted_by_email or ""

    # The owner's working fields: what an AI reviewer proposed, and the Connect
    # organisation the awarder named for the contract. Never shown to anyone
    # who cannot manage the call, the respondent included.
    OWNER_ONLY_FIELDS = ("ai_proposed_scores", "org_id")

    def to_data(self, *, owner_view: bool = False) -> dict:
        """The response's fields. `owner_view` adds the owner's working fields:
        pass it only for a caller who can manage the call."""
        data = {
            "solicitation_id": self.solicitation_id,
            "llo_entity_id": self.llo_entity.slug if self.llo_entity_id else "",
            "llo_entity_name": self.llo_entity_name,
            "responses": self.responses or {},
            "status": self.status,
            "submitted_by_name": self.submitted_by_name,
            "submitted_by_email": self.submitted_by_email,
            "submitted_by_username": self.submitted_by_username,
            "org_id": self.org_id,
            "org_name": self.org_name,
            "submission_date": _iso(self.submission_date),
            "selected_plan_ids": self.selected_plan_ids or [],
            "selected_plan_names": self.selected_plan_names or [],
            "reward_budget": self.reward_budget,
            "award_stage": self.award_stage,
            "awarded_at": _iso(self.awarded_at),
            "awardee_notified": self.awardee_notified,
            "demonstration_award": self.demonstration_award,
            "synthetic": self.synthetic,
            "ai_proposed_scores": self.ai_proposed_scores or {},
        }
        if not owner_view:
            for name in self.OWNER_ONLY_FIELDS:
                data.pop(name)
        return data

    def to_record_dict(self, *, owner_view: bool = False) -> dict:
        """Flat dict in the shape the MCP tools always returned (envelope + data)."""
        data = self.to_data(owner_view=owner_view)
        return {
            "id": self.pk,
            "experiment": data["llo_entity_id"] or "individual",
            "type": "solicitation_response",
            "program_id": self.solicitation.program_id,
            "labs_record_id": self.solicitation_id,
            **data,
            "is_public": False,
            "legacy_record_id": self.legacy_record_id,
        }


REVIEW_AI = "ai"
REVIEW_HUMAN = "human"
REVIEW_KINDS = [(REVIEW_AI, "AI"), (REVIEW_HUMAN, "Person")]

# A reviewer's reaction to the review theirs started from (`based_on`).
STANCES = [
    ("agrees", "Agrees"),
    ("amended", "Amended it"),
    ("adds", "Added to it"),
    ("disagrees", "Disagrees"),
]

# Where a marketplace round's applicant lands. Calls run on labs keep
# `recommendation` and the award flow; a round is decided outside labs.
OUTCOMES = [(key, meaning["label"]) for key, meaning in vocabulary.OUTCOMES.items()]


class RubricVersion(models.Model):
    """One version of a round's criteria, kept when the next replaces it.

    The round itself carries only the current criteria (`evaluation_criteria`,
    `rubric_version`); each version, current included, has a row here, so a
    review scored on an earlier version can still be read against the criteria
    it was given on, and two versions compared.
    """

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="rubric_versions")
    version = models.CharField(max_length=40)
    criteria = models.JSONField(default=list, blank=True)
    basis = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [models.UniqueConstraint(fields=["solicitation", "version"], name="uniq_rubric_version")]

    def __str__(self) -> str:
        return f"{self.solicitation_id} criteria {self.version}"


class CurrentReviews(models.Manager):
    """Reviews that stand: not replaced by a re-score on newer criteria."""

    def get_queryset(self):
        return super().get_queryset().filter(superseded_at__isnull=True)


class SolicitationReview(models.Model):
    """One reviewer's scoring of one response.

    A review is a person's or an AI's (`kind`). Several people may review the
    same response. A person can start from another review (`based_on`): either
    say whether they agree with it (`stance`, little else filled in), copy it
    and edit the copy as their own, or add to it (`adds`: the review stores
    only the additions, and is read as the original plus them). The review they started from is never
    changed, so what the AI said stays readable beside what people made of it.
    """

    response = models.ForeignKey(SolicitationResponse, on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # Who the review says reviewed it, for imported reviews whose user labs
    # does not know. A live review carries its reviewer's username.
    reviewer_name = models.CharField(max_length=200, blank=True, default="")
    kind = models.CharField(max_length=8, choices=REVIEW_KINDS, default=REVIEW_HUMAN)
    based_on = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="derived_reviews"
    )
    stance = models.CharField(max_length=12, choices=STANCES, blank=True, default="")
    criteria_scores = models.JSONField(default=dict, blank=True)
    # criterion id -> the reason for its score, citing the answer.
    criteria_notes = models.JSONField(default=dict, blank=True)
    outcome = models.CharField(max_length=12, choices=OUTCOMES, blank=True, default="")
    strengths = models.TextField(blank=True, default="")
    concerns = models.TextField(blank=True, default="")
    # What to ask the applicant before deciding.
    ask_next = models.TextField(blank=True, default="")
    # Short markers: "generic-or-ai-drafted", "cost-outlier", "safety", ...
    flags = models.JSONField(default=list, blank=True)
    # For an AI review: model, skill and version, rubric version, sources.
    provenance = models.JSONField(default=dict, blank=True)
    # Its statements, each with the response text it rests on (`evidence.py`).
    claims = models.JSONField(default=list, blank=True)
    # 1-100. Computed from the weighted criteria when there are any
    # (`weighted_score`); otherwise the score the reviewer gave.
    score = models.IntegerField(null=True, blank=True)
    recommendation = models.CharField(max_length=32, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    tags = models.CharField(max_length=500, blank=True, default="")
    review_date = models.DateTimeField(default=timezone.now)
    legacy_record_id = models.IntegerField(null=True, blank=True, unique=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Set when a re-score on newer criteria took its place. A superseded review
    # is history: read only through `all_versions`, never by the round's pages
    # or tools unless they ask for that version.
    superseded_at = models.DateTimeField(null=True, blank=True)
    # A demonstration review: of a `synthetic` response on the reviewer's own
    # side, explicitly requested (`access.can_demonstrate`).
    demonstration = models.BooleanField(default=False)

    # The first manager is the default, so related managers (`response.reviews`)
    # and every query see only the reviews that stand.
    objects = CurrentReviews()
    all_versions = models.Manager()

    class Meta:
        ordering = ["review_date", "pk"]
        base_manager_name = "all_versions"
        constraints = [
            # One standing review of each kind per reviewer: an AI pass run as a
            # person never takes the place of that person's own review.
            models.UniqueConstraint(
                fields=["response", "reviewer", "kind"],
                condition=models.Q(superseded_at__isnull=True),
                name="uniq_review_per_reviewer_kind",
            ),
        ]

    @property
    def rubric_version(self) -> str:
        return str((self.provenance or {}).get("rubric_version") or "")

    def __str__(self) -> str:
        return f"review of response {self.response_id} by {self.reviewer_username or 'unknown'}"

    @property
    def reviewer_username(self) -> str:
        return self.reviewer.username if self.reviewer_id else self.reviewer_name

    @property
    def llo_entity_id(self) -> str:
        return self.response.llo_entity.slug if self.response.llo_entity_id else ""

    def to_data(self) -> dict:
        return {
            "response_id": self.response_id,
            "llo_entity_id": self.llo_entity_id,
            "score": self.score,
            "recommendation": self.recommendation,
            "notes": self.notes,
            "tags": self.tags,
            "reviewer_username": self.reviewer_username,
            "criteria_scores": self.criteria_scores or {},
            "review_date": _iso(self.review_date),
            "kind": self.kind,
            "based_on": self.based_on_id,
            "stance": self.stance,
            "criteria_notes": self.criteria_notes or {},
            "outcome": self.outcome,
            "strengths": self.strengths,
            "concerns": self.concerns,
            "ask_next": self.ask_next,
            "flags": list(self.flags or []),
            "provenance": self.provenance or {},
            "claims": list(self.claims or []),
            "demonstration": self.demonstration,
        }

    def to_record_dict(self) -> dict:
        data = self.to_data()
        return {
            "id": self.pk,
            "experiment": data["llo_entity_id"] or "individual",
            "type": "solicitation_review",
            "labs_record_id": self.response_id,
            **data,
            "legacy_record_id": self.legacy_record_id,
        }


def weighted_score(criteria_scores: dict, evaluation_criteria: list) -> int | None:
    """1-100 from per-criterion 1-10 scores, weighted by the rubric.

    Criteria without a score are left out (and their weight with them), so a
    partial review is scored on what it scored. None when nothing scored.
    """
    total = weight_sum = 0.0
    weights = {c.get("id"): float(c.get("weight") or 0) for c in (evaluation_criteria or []) if c.get("id")}
    for criterion_id, raw in (criteria_scores or {}).items():
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        weight = weights.get(criterion_id, 0.0)
        if weight <= 0:
            continue
        total += value * weight
        weight_sum += weight
    if not weight_sum:
        return None
    return max(1, min(100, round(total / weight_sum * 10)))

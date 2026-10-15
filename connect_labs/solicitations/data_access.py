"""Data access for solicitations -- the single choke point.

Every read and write of a solicitation, response or review goes through
`SolicitationsDataAccess`, which runs as one caller (a request, or a user for
MCP tools and agents) and enforces that caller's standing here, once
(`solicitations/access.py`). Views, MCP tools and AI agents do not check
permissions themselves; they get `None`/`[]` for what the caller may not see and
`PermissionDenied` for what they may not do.

The store is the labs database (`local_models.py`). Returned objects are the
models themselves, which keep the property names the old LabsRecord proxies
exposed (`title`, `is_public`, `criteria_locked`, `solicitation_id`,
`llo_entity_name`, `reward_budget`, `reviewer_username` ...).

Edits MERGE: a key the caller does not send is never cleared.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest
from django.utils import timezone

from connect_labs.solicitations import access
from connect_labs.solicitations.local_models import (
    AWARD_STAGES,
    SOURCE_LABS,
    Solicitation,
    SolicitationResponse,
    SolicitationReview,
    weighted_score,
)
from connect_labs.solicitations.validation import validate_solicitation_payload

logger = logging.getLogger(__name__)

# The old LabsRecord types, named by the prod import.
SOLICITATION_TYPE = "solicitation"
RESPONSE_TYPE = "solicitation_response"
REVIEW_TYPE = "solicitation_review"

_TEXT_FIELDS = (
    "title",
    "description",
    "scope_of_work",
    "solicitation_type",
    "status",
    "estimated_scale",
    "contact_email",
    "program_name",
    "currency",
)
_JSON_LIST_FIELDS = ("questions", "evaluation_criteria", "plans", "source_plan_ids")
_DATE_FIELDS = ("application_deadline", "expected_start_date", "expected_end_date")
_INT_FIELDS = ("connect_opportunity_id", "fund_id", "source_program_id", "source_group_id")
# Not data fields: who owns a call and where it is listed travel beside the payload.
_ENVELOPE_KEYS = ("is_public", "org_slug", "owner_org", "program_id", "organization_id")

_RESPONDENT_FIELDS = (
    "responses",
    "submitted_by_name",
    "submitted_by_email",
    "org_name",
    "llo_entity_name",
    "selected_plan_ids",
    "selected_plan_names",
)
_RESPONDENT_STATUSES = ("draft", "submitted")
_OWNER_RESPONSE_STATUSES = ("submitted", "under_review", "rejected")


class HasResponses(Exception):
    """A delete that would destroy responses or reviews, without `force`."""

    def __init__(self, responses: int, reviews: int):
        self.responses, self.reviews = responses, reviews
        super().__init__(f"{responses} responses + {reviews} reviews would be destroyed")


def _parse_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _parse_datetime(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _int_or_none(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def by_public_id(queryset, record_id):
    """An imported row by its prod id first (links already sent), then by pk."""
    record_id = _int_or_none(record_id)
    if record_id is None:
        return None
    row = queryset.filter(legacy_record_id=record_id).first()
    return row if row is not None else queryset.filter(pk=record_id).first()


class SolicitationsDataAccess:
    """Solicitations, responses and reviews, as one caller.

    Args:
        program_id: Default programme for a new call (and its owner, via the
            programme's running organisation, when no owner is named).
        organization_id: Default owner for a new call (a LabsOrg slug, Connect
            slug or Connect id).
        access_token: Connect OAuth token. Only fund allocation on award needs
            it (funds are prod records); everything else is local.
        request: The request; its user is the caller and its session supplies
            the token and Connect organisations.
        user: The caller when there is no request (MCP tools, Celery).
    """

    def __init__(
        self,
        program_id: str | int | None = None,
        organization_id: str | int | None = None,
        access_token: str | None = None,
        request: HttpRequest | None = None,
        user=None,
    ):
        labs_context = getattr(request, "labs_context", None) or {}
        self.program_id = _int_or_none(program_id) or _int_or_none(labs_context.get("program_id"))
        self.organization_id = organization_id or labs_context.get("organization_id")
        self.request = request

        if not access_token and request is not None:
            labs_oauth = (getattr(request, "session", None) or {}).get("labs_oauth", {}) or {}
            if timezone.now().timestamp() < (labs_oauth.get("expires_at") or 0):
                access_token = labs_oauth.get("access_token")
        self.access_token = access_token

        if user is None and request is not None:
            user = getattr(request, "user", None)
        self.caller = access.Caller(user=user, request=request)

    @property
    def user(self):
        return self.caller.user if self.caller.is_authenticated else None

    # =========================================================================
    # Solicitations
    # =========================================================================

    def _calls(self):
        return Solicitation.objects.calls().select_related("owner_org", "created_by")

    def get_solicitations(
        self,
        status: str | None = None,
        solicitation_type: str | None = None,
        *,
        org=None,
        program_id: int | None = None,
        include_listed: bool = False,
    ) -> list[Solicitation]:
        """The calls this caller manages (staff: every call), newest first.

        `org` narrows to one organisation's calls: all of them for someone who
        manages it, its publicly visible ones for anyone else. `include_listed`
        adds every publicly visible call.
        """
        qs = self._calls()
        if org is not None:
            org = access.resolve_org(org)
            if org is None:
                return []
            qs = qs.filter(owner_org=org)
            if not access.can_manage_org(self.caller, org):
                qs = qs.filter(listed=True).exclude(status="draft")
        elif not self.caller.is_operator:
            scope = Q(owner_org__in=[o.pk for o in access.manageable_orgs(self.caller)])
            if include_listed:
                scope |= Q(listed=True) & ~Q(status="draft")
            qs = qs.filter(scope)
        if program_id not in (None, ""):
            qs = qs.filter(program_id=_int_or_none(program_id))
        if status:
            qs = qs.filter(status=status)
        if solicitation_type:
            qs = qs.filter(solicitation_type=solicitation_type)
        return list(qs.order_by("-created_at", "-pk"))

    def get_public_solicitations(
        self,
        solicitation_type: str | None = None,
        *,
        open_only: bool = False,
        owner=None,
    ) -> list[Solicitation]:
        """Listed, published calls -- readable by anyone, signed in or not.

        `open_only`: still taking responses (active, deadline today or later).
        """
        qs = self._calls().filter(listed=True).exclude(status="draft")
        if solicitation_type:
            qs = qs.filter(solicitation_type=solicitation_type)
        if owner is not None:
            org = access.resolve_org(owner)
            if org is None:
                return []
            qs = qs.filter(owner_org=org)
        if open_only:
            today = timezone.localdate()
            qs = qs.filter(status="active").filter(
                Q(application_deadline__isnull=True) | Q(application_deadline__gte=today)
            )
        return list(qs.order_by("application_deadline", "-created_at"))

    def get_solicitations_by_fund_id(self, fund_id: int) -> list[Solicitation]:
        """Calls linked to a fund that this caller can see."""
        qs = self._calls().filter(fund_id=_int_or_none(fund_id))
        return [s for s in qs if access.can_read(self.caller, s)]

    def get_solicitation_by_id(self, solicitation_id: int) -> Solicitation | None:
        """A call by its public id (prod id if imported, else pk), or None if missing or not readable."""
        solicitation = by_public_id(Solicitation.objects.select_related("owner_org", "created_by"), solicitation_id)
        if solicitation is None or not access.can_read(self.caller, solicitation):
            return None
        return solicitation

    def standing(self, solicitation) -> str:
        return access.standing(self.caller, solicitation)

    def can_manage(self, solicitation) -> bool:
        return access.can_manage(self.caller, solicitation)

    def manageable_orgs(self):
        return access.manageable_orgs(self.caller)

    def count_responses(self, solicitation) -> int:
        """Submitted (non-draft) responses -- what an owner sees in their inbox."""
        return solicitation.responses.exclude(status="draft").count()

    def _resolve_owner(self, data: dict, owner_org=None):
        for key in (owner_org, data.get("org_slug"), data.get("owner_org"), data.get("organization_id")):
            if key not in (None, ""):
                org = access.resolve_org(key)
                if org is None:
                    raise ValidationError({"org_slug": f"no organisation {key!r} in labs"})
                return org
        if self.organization_id:
            org = access.resolve_org(self.organization_id)
            if org is not None:
                return org
        program_id = _int_or_none(data.get("program_id")) or self.program_id
        if program_id is not None:
            from connect_labs.supply_chain.runner import running_org

            # A declared runner owns its programme's calls; whether this caller
            # may create one is asked by create_solicitation, as for any owner.
            org = running_org(program_id)
            if org is not None:
                return org
            # Otherwise the programme's own organisation -- but only for someone
            # who could manage it anyway: seeing a programme is not owning it.
            org = access.programme_owner(self.caller, program_id)
            if org is not None and access.can_manage_org(self.caller, org):
                return org
            reason = (
                f"program {program_id} belongs to {org.name}, which you are not a member of"
                if org is not None
                else f"program {program_id} has no running organisation and no organisation labs knows"
            )
            raise ValidationError(
                {"org_slug": f"{reason}, so it cannot own this call. Name the owning organisation with org_slug."}
            )
        raise ValidationError({"org_slug": "Name the organisation that owns this call (org_slug)."})

    def create_solicitation(self, data: dict, *, owner_org=None) -> Solicitation:
        """Create a call owned by an organisation the caller manages.

        The owner is, in order: `owner_org`, `org_slug`/`owner_org`/
        `organization_id` in `data`, this access's `organization_id`, or the
        running organisation of `program_id` (in `data`, else this access's).
        Raises ValidationError for drift or a missing owner, PermissionDenied
        if the caller does not manage the owner.
        """
        data = dict(data)
        owner = self._resolve_owner(data, owner_org)
        access.require(
            access.can_manage_org(self.caller, owner),
            f"You are not a member of {owner.name}, so you cannot create a call for it.",
        )
        payload = {k: v for k, v in data.items() if k not in _ENVELOPE_KEYS}
        validate_solicitation_payload(payload)

        solicitation = Solicitation(
            source=SOURCE_LABS,
            owner_org=owner,
            program_id=_int_or_none(data.get("program_id")) or self.program_id,
            listed=bool(data.get("is_public", False)),
            created_by=self.user,
        )
        self._apply_fields(solicitation, payload)
        if payload.get("criteria_locked"):
            solicitation.criteria_locked_at = _parse_datetime(payload.get("criteria_locked_at")) or timezone.now()
        solicitation.save()
        return solicitation

    def update_solicitation(self, solicitation_id: int, data: dict) -> Solicitation:
        """Merge `data` into a call the caller manages. Unsent fields are kept.

        The rubric lock holds here, whatever the caller: once locked, the
        evaluation criteria cannot change and the lock cannot be lifted.
        """
        solicitation = self._managed_solicitation(solicitation_id)
        data = dict(data)
        payload = {k: v for k, v in data.items() if k not in _ENVELOPE_KEYS}
        payload.pop("created_by", None)  # provenance, never edited
        payload.pop("criteria_locked_at", None)  # stamped here, never sent

        if solicitation.criteria_locked:
            if "evaluation_criteria" in payload and (payload["evaluation_criteria"] or []) != (
                solicitation.evaluation_criteria or []
            ):
                raise ValidationError(
                    {"evaluation_criteria": "the rubric is locked: its criteria cannot change after publishing"}
                )
            if "criteria_locked" in payload and not payload["criteria_locked"]:
                raise ValidationError({"criteria_locked": "the rubric is locked and cannot be unlocked"})

        merged = {k: v for k, v in solicitation.to_data().items() if k != "created_by"}
        merged.update(payload)
        validate_solicitation_payload(merged, partial=True)

        new_owner = None
        for key in ("org_slug", "owner_org"):
            if data.get(key) not in (None, ""):
                new_owner = access.resolve_org(data[key])
                if new_owner is None:
                    raise ValidationError({"org_slug": f"no organisation {data[key]!r} in labs"})
        if new_owner is not None and new_owner.pk != solicitation.owner_org_id:
            access.require(
                access.can_manage_org(self.caller, new_owner),
                f"You are not a member of {new_owner.name}, so you cannot hand this call to it.",
            )
            solicitation.owner_org = new_owner

        self._apply_fields(solicitation, payload)
        if payload.get("criteria_locked") and not solicitation.criteria_locked_at:
            solicitation.criteria_locked_at = timezone.now()
        if "is_public" in data:
            solicitation.listed = bool(data["is_public"])
        if "program_id" in data:
            solicitation.program_id = _int_or_none(data["program_id"])
        solicitation.save()
        return solicitation

    def delete_solicitation(self, solicitation_id: int, *, force: bool = False) -> dict:
        """Delete a call and, with `force`, its responses and reviews. Returns the counts."""
        solicitation = self._managed_solicitation(solicitation_id)
        responses = solicitation.responses.count()
        reviews = SolicitationReview.objects.filter(response__solicitation=solicitation).count()
        if (responses or reviews) and not force:
            raise HasResponses(responses, reviews)
        solicitation.delete()  # responses and reviews cascade
        return {"solicitations": 1, "responses": responses, "reviews": reviews}

    def _managed_solicitation(self, solicitation_id) -> Solicitation:
        solicitation = by_public_id(Solicitation.objects.select_related("owner_org"), solicitation_id)
        if solicitation is None or not access.can_read(self.caller, solicitation):
            raise Solicitation.DoesNotExist(f"Solicitation {solicitation_id} not found")
        access.require(
            access.can_manage(self.caller, solicitation),
            "Only the organisation that owns this call (or platform support) can change it.",
        )
        return solicitation

    @staticmethod
    def _apply_fields(solicitation, payload: dict) -> None:
        for name in _TEXT_FIELDS:
            if name in payload:
                setattr(solicitation, name, payload[name] or "")
        for name in _JSON_LIST_FIELDS:
            if name in payload:
                setattr(solicitation, name, payload[name] or [])
        for name in _DATE_FIELDS:
            if name in payload:
                setattr(solicitation, name, _parse_date(payload[name]))
        for name in _INT_FIELDS:
            if name in payload:
                setattr(solicitation, name, _int_or_none(payload[name]))
        solicitation.currency = (solicitation.currency or "").upper()

    # =========================================================================
    # Responses
    # =========================================================================

    def get_responses_for_solicitation(self, solicitation_id: int) -> list[SolicitationResponse]:
        """Every submitted response for an owner/operator; the caller's own otherwise."""
        solicitation = self.get_solicitation_by_id(solicitation_id)
        if solicitation is None:
            return []
        qs = solicitation.responses.select_related("llo_entity", "submitted_by", "solicitation")
        if access.can_manage(self.caller, solicitation):
            qs = qs.filter(~Q(status="draft") | Q(submitted_by_id=self.caller.user_pk))
        elif self.caller.user_pk is not None:
            qs = qs.filter(submitted_by_id=self.caller.user_pk)
        else:
            return []
        return list(qs.order_by("submission_date", "pk"))

    def get_response_by_id(self, response_id: int) -> SolicitationResponse | None:
        response = by_public_id(
            SolicitationResponse.objects.select_related(
                "solicitation", "solicitation__owner_org", "llo_entity", "submitted_by"
            ),
            response_id,
        )
        if response is None or not access.can_read_response(self.caller, response):
            return None
        return response

    def get_my_response(self, solicitation_id: int) -> SolicitationResponse | None:
        """The caller's draft if they have one, else their latest response."""
        if self.caller.user_pk is None:
            return None
        solicitation = self.get_solicitation_by_id(solicitation_id)
        if solicitation is None:
            return None
        mine = solicitation.responses.filter(submitted_by_id=self.caller.user_pk)
        return mine.filter(status="draft").order_by("-pk").first() or mine.order_by("-pk").first()

    def create_response(
        self,
        solicitation_id: int,
        llo_entity_id: str | int | None = None,
        data: dict | None = None,
        *,
        on_behalf: bool = False,
    ) -> SolicitationResponse:
        """Respond to a call, as the caller and the organisation they name.

        One draft per (call, user): saving a draft again, or submitting one,
        edits it in place. Refused once the call stops accepting responses.

        `on_behalf=True` is for an owner or operator entering a firm's response
        (seeding, a paper submission): it has no respondent user, may name any
        organisation, and ignores the deadline.
        """
        data = dict(data or {})
        solicitation = self.get_solicitation_by_id(solicitation_id)
        if solicitation is None:
            raise Solicitation.DoesNotExist(f"Solicitation {solicitation_id} not found")
        if on_behalf:
            access.require(
                access.can_manage(self.caller, solicitation),
                "Only the organisation that owns this call can enter a response on a firm's behalf.",
            )
        else:
            access.require(self.caller.is_authenticated, "Sign in to respond.")
            access.require(
                bool(solicitation.can_accept_responses),
                "This call is not accepting responses"
                + (" — its deadline has passed." if solicitation.deadline_passed else "."),
            )

        status = data.get("status") or "submitted"
        if status not in _RESPONDENT_STATUSES:
            raise ValidationError({"status": f"{status!r} must be one of {list(_RESPONDENT_STATUSES)}"})

        with transaction.atomic():
            response = None
            if not on_behalf:
                response = (
                    solicitation.responses.select_for_update()
                    .filter(submitted_by_id=self.caller.user_pk, status="draft")
                    .order_by("-pk")
                    .first()
                )
            if response is None:
                response = SolicitationResponse(
                    solicitation=solicitation, submitted_by=None if on_behalf else self.user, status="draft"
                )
            self._apply_respondent_fields(response, data, llo_entity_id, on_behalf=on_behalf)
            if on_behalf:
                if "ai_proposed_scores" in data:
                    response.ai_proposed_scores = data["ai_proposed_scores"] or {}
                if data.get("org_id"):
                    response.org_id = str(data["org_id"])
            response.status = status
            if status == "submitted" or data.get("submission_date"):
                response.submission_date = _parse_datetime(data.get("submission_date")) or timezone.now()
            response.save()

        if status == "submitted" and not on_behalf:
            self._notify_submitted(response)
        return response

    def update_response(self, response_id: int, data: dict) -> SolicitationResponse:
        """Merge `data` into a response.

        The respondent edits their own draft (answers, names, plans, and
        `status: submitted` to send it). An owner or operator moves a submitted
        response along (`under_review`, `rejected`) and may attach
        `ai_proposed_scores`. Awarding is `award_response`, never this.
        """
        response = self._response_or_missing(response_id)
        data = dict(data)
        if access.can_edit_response(self.caller, response):
            status = data.get("status", response.status)
            if status not in _RESPONDENT_STATUSES:
                raise ValidationError({"status": f"{status!r} must be one of {list(_RESPONDENT_STATUSES)}"})
            if status == "submitted":
                access.require(
                    bool(response.solicitation.can_accept_responses),
                    "This call is not accepting responses any more.",
                )
            self._apply_respondent_fields(response, data, data.get("llo_entity_id"), on_behalf=False)
            response.status = status
            if status == "submitted":
                response.submission_date = timezone.now()
            response.save()
            if status == "submitted":
                self._notify_submitted(response)
            return response

        access.require(
            access.can_manage(self.caller, response.solicitation) and response.status != "draft",
            "Only the respondent can change this response.",
        )
        if "status" in data and data["status"] != response.status:
            access.require(
                access.can_review(self.caller, response), "You cannot move your own organisation's response along."
            )
            if data["status"] not in _OWNER_RESPONSE_STATUSES:
                allowed = list(_OWNER_RESPONSE_STATUSES)
                raise ValidationError({"status": f"{data['status']!r} must be one of {allowed} (award separately)"})
            response.status = data["status"]
        if "ai_proposed_scores" in data:
            response.ai_proposed_scores = data["ai_proposed_scores"] or {}
        response.save()
        return response

    def _response_or_missing(self, response_id) -> SolicitationResponse:
        response = self.get_response_by_id(response_id)
        if response is None:
            raise SolicitationResponse.DoesNotExist(f"Response {response_id} not found")
        return response

    def _apply_respondent_fields(self, response, data: dict, llo_entity_key, *, on_behalf: bool) -> None:
        for name in _RESPONDENT_FIELDS:
            if name in data:
                value = data[name]
                if name == "responses":
                    value = value or {}
                elif name in ("selected_plan_ids", "selected_plan_names"):
                    value = list(value or [])
                else:
                    value = (value or "").strip()
                setattr(response, name, value)
        if self.user is not None and not on_behalf:
            response.submitted_by_email = response.submitted_by_email or (self.user.email or "")
            response.submitted_by_name = response.submitted_by_name or (
                getattr(self.user, "name", "") or self.user.username
            )

        org_key = data.get("llo_entity", llo_entity_key)
        if org_key in (None, "", "individual"):
            return
        org = access.resolve_org(org_key)
        if org is None:
            # A name nobody has registered: keep it as typed; the directory
            # import's exact matching can attach it later.
            response.org_name = response.org_name or str(org_key)
            return
        if not on_behalf:
            access.require(
                self.caller.is_member(org),
                f"You can respond only for an organisation you belong to, and you are not a member of {org.name}.",
            )
        response.llo_entity = org
        response.llo_entity_name = response.llo_entity_name or org.name
        response.org_name = response.org_name or org.name

    def award_response(
        self,
        response_id: int,
        reward_budget: int,
        org_id: str = "",
        award_stage: str = "verification",
        *,
        fund_id: int | None = None,
        allocate_fund: bool = True,
    ) -> SolicitationResponse:
        """Award a response: record the decision and tell the awardee.

        `awardee_notified` is set only if the award email was handed to the
        mail path (labs email enabled and an address to send to). Allocates
        from `fund_id`, or the call's own fund when `allocate_fund`; a failed
        allocation is logged and never undoes the award. `program_id` on the
        allocation is the call's, and may be None.
        """
        response = self._response_or_missing(response_id)
        solicitation = response.solicitation
        access.require(
            access.can_award(self.caller, response),
            "Only the organisation that owns this call (or platform support) can award it, "
            "and never a response from your own side.",
        )
        response.status = "awarded"
        response.reward_budget = _int_or_none(reward_budget)
        response.org_id = str(org_id or "")
        response.award_stage = award_stage if award_stage in AWARD_STAGES else "verification"
        response.awarded_at = timezone.now()
        response.awardee_notified = self._notify_awarded(response)
        response.save()
        if solicitation.status in ("active", "closed"):
            solicitation.status = "awarded"
            solicitation.save(update_fields=["status", "updated_at"])

        target_fund = fund_id or (solicitation.fund_id if allocate_fund else None)
        if target_fund:
            self._allocate(target_fund, solicitation, response)
        return response

    def _allocate(self, fund_id, solicitation, response) -> None:
        if not self.access_token:
            logger.warning("award of response %s: no Connect token, fund %s not allocated", response.pk, fund_id)
            return
        try:
            from connect_labs.funder_dashboard.data_access import FunderDashboardDataAccess

            FunderDashboardDataAccess(access_token=self.access_token).add_allocation(
                fund_id=fund_id,
                allocation={
                    "program_id": solicitation.program_id,
                    "program_name": solicitation.program_name,
                    "amount": response.reward_budget,
                    "type": "award",
                    "solicitation_id": solicitation.public_id,
                    "response_id": response.pk,
                    "org_id": response.org_id,
                    "org_name": response.display_name,
                    "notes": f"Award from {solicitation.title}",
                },
            )
        except Exception:
            logger.exception("Failed to allocate fund %s for awarded response %s", fund_id, response.pk)

    # =========================================================================
    # Reviews
    # =========================================================================

    def get_reviews_for_response(self, response_id: int) -> list[SolicitationReview]:
        """Reviews are the owner's working notes: only an owner or operator reads them."""
        response = self.get_response_by_id(response_id)
        if response is None or not access.can_manage(self.caller, response.solicitation):
            return []
        return list(response.reviews.select_related("reviewer").order_by("review_date", "pk"))

    def get_review_by_id(self, review_id: int) -> SolicitationReview | None:
        review = by_public_id(
            SolicitationReview.objects.select_related("response", "response__solicitation", "reviewer"), review_id
        )
        if review is None or not access.can_manage(self.caller, review.response.solicitation):
            return None
        return review

    def get_my_review(self, response_id: int) -> SolicitationReview | None:
        response = self.get_response_by_id(response_id)
        if response is None or self.caller.user_pk is None:
            return None
        return response.reviews.filter(reviewer_id=self.caller.user_pk).first()

    def create_review(self, response_id: int, data: dict) -> SolicitationReview:
        """Score a response. One review per reviewer: a second call updates the first."""
        response = self._response_or_missing(response_id)
        access.require(
            access.can_review(self.caller, response),
            "Only the organisation that owns this call (or platform support) can review it, "
            "and never a response from your own side.",
        )
        review = response.reviews.filter(reviewer_id=self.caller.user_pk).first() if self.user else None
        if review is None:
            review = SolicitationReview(response=response, reviewer=self.user)
        self._apply_review_fields(review, dict(data), response)
        review.save()
        if response.status == "submitted":
            response.status = "under_review"
            response.save(update_fields=["status", "imported_at"])
        return review

    def update_review(self, review_id: int, data: dict) -> SolicitationReview:
        review = self.get_review_by_id(review_id)
        if review is None:
            raise SolicitationReview.DoesNotExist(f"Review {review_id} not found")
        access.require(
            access.can_review(self.caller, review.response)
            and (review.reviewer_id == self.caller.user_pk or self.caller.is_operator),
            "Only the reviewer (or platform support) can change this review.",
        )
        self._apply_review_fields(review, dict(data), review.response)
        review.save()
        return review

    def _apply_review_fields(self, review, data: dict, response) -> None:
        for name in ("recommendation", "notes", "tags"):
            if name in data:
                setattr(review, name, data[name] or "")
        if "criteria_scores" in data:
            review.criteria_scores = data["criteria_scores"] or {}
        if "review_date" in data:
            review.review_date = _parse_datetime(data["review_date"]) or timezone.now()
        if data.get("reviewer_username"):
            review.reviewer_name = str(data["reviewer_username"])[:200]
        elif not review.reviewer_name and self.user is not None:
            review.reviewer_name = self.user.username
        computed = weighted_score(review.criteria_scores, response.solicitation.evaluation_criteria)
        if computed is not None:
            review.score = computed
        elif "score" in data:
            review.score = _int_or_none(data["score"])

    # =========================================================================
    # Email
    # =========================================================================

    def _notify_submitted(self, response) -> None:
        solicitation = response.solicitation
        self._send(
            f"We received your response to {solicitation.title}",
            (
                f"Thank you for responding to {solicitation.title}"
                + (f" from {solicitation.owner_name}" if solicitation.owner_name else "")
                + ".\n\nYour response has been submitted. The organisation reviews the responses "
                "and will contact you about the outcome."
            ),
            [response.submitted_by_email],
        )
        self._send(
            f"New response to {solicitation.title}",
            f"{response.display_name or 'An organisation'} has responded to {solicitation.title}.",
            [solicitation.contact_email],
        )

    def _notify_awarded(self, response) -> bool:
        solicitation = response.solicitation
        amount = ""
        if response.reward_budget:
            amount = f" The award is {response.reward_budget:,}" + (
                f" {solicitation.currency}." if solicitation.currency else "."
            )
        return self._send(
            f"Your response to {solicitation.title} has been selected",
            (
                f"{solicitation.owner_name or 'The organisation'} has selected your response to "
                f"{solicitation.title}.{amount}\n\nThey will contact you about next steps."
                + (f" Questions: {solicitation.contact_email}" if solicitation.contact_email else "")
            ),
            [response.submitted_by_email],
        )

    @staticmethod
    def _send(subject: str, message: str, recipients) -> bool:
        """True only when the mail was handed to the delivery path."""
        from connect_labs.utils.email import email_enabled, send_labs_email

        recipients = [r for r in (recipients or []) if r and "@" in r]
        if not recipients or not email_enabled():
            return False
        try:
            return send_labs_email(subject, message, recipients) is not None
        except Exception:
            logger.exception("Could not queue solicitation email %r", subject)
            return False

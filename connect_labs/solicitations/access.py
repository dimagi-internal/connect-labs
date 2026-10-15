"""Who may do what to a solicitation -- decided here and nowhere else.

A call is owned by an organisation (``Solicitation.owner_org``). Standing on a
call is one of:

    owner       a member of the owning organisation BY GRANT: edit, publish,
                close, read responses, review, award.
    operator    Django staff who is not a member: platform support. Everything an
                owner may do, recorded as themselves.
    public      everyone else, signed in or not: read listed calls, respond as
                their own organisation.

On a response there is one more: the **respondent**, the user who submitted it.
They read their own response and edit it until it is submitted; they never
review or award it, whatever else they are.

Membership is Supply's (`supply_chain.runner.is_member`): an `OrgMembership`
granted for the organisation, Connect's own membership of an organisation
Connect knows, or a Dimagi address for Dimagi. An organisation being *visible*
in someone's labs context is not membership. An unowned historical round has
no owner, so only staff manage it -- there is no Dimagi fallback.

Every check fails closed: a membership that cannot be established is none.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.core.exceptions import PermissionDenied
from django.db.models import F

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain import runner

logger = logging.getLogger(__name__)

OWNER = "owner"
OPERATOR = "operator"
PUBLIC = "public"


@dataclass
class Caller:
    """The person asking, shaped like the access objects Supply's checks read.

    `supply_chain.runner.is_member` reads `.user`, and (through
    `supply_chain.identity`) `.request` for the session's Connect organisations,
    falling back to the user's stored Connect token. `program_id` is always
    None: standing on a call comes from its owner, never from a programme.
    """

    user: object = None
    request: object = None
    program_id: object = None
    caller: object = None
    _members: dict = field(default_factory=dict, repr=False)

    @property
    def is_authenticated(self) -> bool:
        return bool(self.user is not None and getattr(self.user, "is_authenticated", False))

    @property
    def user_pk(self):
        return getattr(self.user, "pk", None) if self.is_authenticated else None

    @property
    def is_operator(self) -> bool:
        return self.is_authenticated and runner.is_operator(self.user)

    def is_member(self, org) -> bool:
        """Membership of `org` by grant, cached for the life of this caller."""
        if org is None or not self.is_authenticated:
            return False
        key = org.pk
        if key not in self._members:
            try:
                self._members[key] = bool(runner.is_member(self, org))
            except Exception:  # noqa: BLE001 -- an unknowable membership grants nothing
                logger.info("solicitations: could not establish membership of %s for %s", org, self.user)
                self._members[key] = False
        return self._members[key]


def caller_for(subject) -> Caller:
    """A `Caller` from a request, a user, an existing caller, or None (anonymous)."""
    if isinstance(subject, Caller):
        return subject
    if subject is None:
        return Caller()
    if hasattr(subject, "session") or hasattr(subject, "META"):
        return Caller(user=getattr(subject, "user", None), request=subject)
    return Caller(user=subject)


def standing(subject, solicitation) -> str:
    """`owner`, `operator` or `public` on this call."""
    caller = caller_for(subject)
    if solicitation.owner_org_id and caller.is_member(solicitation.owner_org):
        return OWNER
    if caller.is_operator:
        return OPERATOR
    return PUBLIC


def can_manage(subject, solicitation) -> bool:
    """Edit, publish, close, read every response, review and award."""
    return standing(subject, solicitation) in (OWNER, OPERATOR)


def can_manage_org(subject, org) -> bool:
    """May create and manage calls owned by `org`."""
    caller = caller_for(subject)
    return org is not None and (caller.is_member(org) or caller.is_operator)


def is_publicly_visible(solicitation) -> bool:
    """Readable by anyone: listed, and published (not a draft)."""
    return bool(solicitation.listed) and solicitation.status != "draft"


def is_respondent(subject, response) -> bool:
    caller = caller_for(subject)
    return caller.user_pk is not None and response.submitted_by_id == caller.user_pk


def has_responded(subject, solicitation) -> bool:
    caller = caller_for(subject)
    if caller.user_pk is None:
        return False
    return solicitation.responses.filter(submitted_by_id=caller.user_pk).exists()


def can_read(subject, solicitation) -> bool:
    """See the call itself. A respondent keeps seeing a call that was unlisted after they answered."""
    if is_publicly_visible(solicitation):
        return True
    return can_manage(subject, solicitation) or has_responded(subject, solicitation)


def can_read_response(subject, response) -> bool:
    if is_respondent(subject, response):
        return True
    # An owner sees what was submitted, never someone's unsent draft.
    return response.status != "draft" and can_manage(subject, response.solicitation)


def can_edit_response(subject, response) -> bool:
    """The respondent, until they submit it."""
    return is_respondent(subject, response) and response.status == "draft"


def _is_own_side(subject, response) -> bool:
    """The respondent, or a member of the organisation that responded."""
    caller = caller_for(subject)
    if is_respondent(caller, response):
        return True
    return bool(response.llo_entity_id and caller.is_member(response.llo_entity))


def can_review(subject, response) -> bool:
    """Owner or operator of the call -- and never on their own response."""
    return can_manage(subject, response.solicitation) and not _is_own_side(subject, response)


def can_award(subject, response) -> bool:
    return can_review(subject, response)


def require(allowed: bool, message: str) -> None:
    """Raise `PermissionDenied(message)` unless `allowed`."""
    if not allowed:
        raise PermissionDenied(message)


def manageable_orgs(subject) -> list[LabsOrg]:
    """Organisations whose calls this caller may manage, by name.

    The same rules as `runner.is_member`, evaluated once for every candidate:
    grants, the Connect organisations the caller belongs to (fetched once), and
    Dimagi for a Dimagi address. Staff manage every organisation that owns a
    call, so their workspace switcher lists those.
    """
    from connect_labs.marketplace.models import OrgMembership
    from connect_labs.utils.dimagi_user import is_dimagi_user

    caller = caller_for(subject)
    if not caller.is_authenticated:
        return []
    # A grant counts only for the organisation it was made for: a merge can
    # move `org` underneath a membership (`OrgMembership.granted_for_org_id`).
    ids = set(
        OrgMembership.objects.filter(user=caller.user, org_id=F("granted_for_org_id")).values_list("org_id", flat=True)
    )
    connect_ids = _connect_org_ids(caller)
    if connect_ids:
        ids |= set(
            LabsOrg.objects.filter(connect_organization_id__in=connect_ids, labs_only=False).values_list(
                "pk", flat=True
            )
        )
    if is_dimagi_user(caller.user):
        ids.add(runner.dimagi_org().pk)
    if caller.is_operator:
        from connect_labs.solicitations.local_models import Solicitation

        ids |= set(Solicitation.objects.exclude(owner_org=None).values_list("owner_org_id", flat=True))
    return list(LabsOrg.objects.filter(pk__in=ids).order_by("name"))


def _connect_org_ids(caller) -> set[int]:
    from connect_labs.supply_chain.identity import caller_org_ids

    try:
        return set(caller_org_ids(caller) or ())
    except Exception:  # noqa: BLE001 -- unknowable membership grants nothing
        logger.info("solicitations: could not read Connect organisations for %s", caller.user)
        return set()


def resolve_org(key) -> LabsOrg | None:
    """A `LabsOrg` by slug, alias, Connect slug or Connect id -- the keys callers hold."""
    if key in (None, ""):
        return None
    if isinstance(key, LabsOrg):
        return key
    text = str(key).strip()
    org = LabsOrg.objects.filter(slug=text).first()
    if org is not None:
        return org
    if text.isdigit():
        org = LabsOrg.objects.filter(connect_organization_id=int(text)).first()
        if org is not None:
            return org
    org = LabsOrg.objects.filter(connect_organization_slug=text).first()
    if org is not None:
        return org
    return LabsOrg.objects.filter(aliases__contains=[text]).first()

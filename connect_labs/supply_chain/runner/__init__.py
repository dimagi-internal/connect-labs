"""Who runs a programme, and what that makes each caller in it.

A programme's supply chain is run by one organisation -- the one whose people
tender, award, contract, order, pay and receive. Until #2458 that was Dimagi by
construction, so every other organisation was a partner even in its own
programme. Now it is a declared fact (`ProgrammeRunner`), and Dimagi is one
organisation that runs programmes.

Three standings, and nothing else decides them:

    programme   a member of the running organisation. Acts for it.
    operator    Django staff who is not a member: platform support. Acts for
                the running organisation, and the OperationCall keeps who it was.
                Being staff makes nobody the buyer.
    partner     anyone else who holds the programme. Acts for their own
                organisation, and their word is reported, not witnessed.

Membership is a GRANT, never visibility: a labs-only programme is visible to a
whole email domain, and visibility was the only check on it. See
`docs/superpowers/specs/2026-10-12-supply-running-organisation-design.md`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from connect_labs.labs.models import LabsOrg
from connect_labs.utils.dimagi_user import is_dimagi_user

logger = logging.getLogger(__name__)

PROGRAMME = "programme"
OPERATOR = "operator"
PARTNER = "partner"

# Dimagi, as one organisation across all of labs. Its people are spread across
# many Connect organisations, so it is the one organisation whose membership is
# recognised by email domain -- the trust boundary labs already draws for it
# (`SyntheticOpportunity.is_accessible_to`). That is a membership rule, not a
# privilege: it makes no Dimagi address the programme where Dimagi does not run.
DIMAGI_ORG_SLUG = "dimagi"


def dimagi_org() -> LabsOrg:
    """Dimagi's row, created on first use."""
    org, _ = LabsOrg.objects.get_or_create(slug=DIMAGI_ORG_SLUG, defaults={"name": "Dimagi", "short_name": "Dimagi"})
    return org


def declared_runner(program_id):
    """The declared running organisation, or None."""
    from connect_labs.supply_chain.runner.models import ProgrammeRunner

    try:
        pid = int(program_id)
    except (TypeError, ValueError):
        return None
    row = ProgrammeRunner.objects.select_related("org").filter(program_id=pid).first()
    return row.org if row is not None else None


def running_org(program_id) -> LabsOrg | None:
    """The organisation that runs this programme, or None if nobody has declared one.

    No default. An earlier draft fell back to Dimagi, which made every Dimagi
    address the programme in any labs-only programme another tenant had just
    registered. Undeclared, only staff and the system act as the programme.
    Never derived from a labs-only opportunity's organisation, which anyone
    able to register a synthetic opp can name.
    """
    if program_id in (None, ""):
        return None
    return declared_runner(program_id)


def declare(program_id, org, *, by=None, note="") -> None:
    """Record who runs a programme. For seeders, commands and `programme_runner_set` -- never a partner path."""
    from connect_labs.supply_chain.runner.models import ProgrammeRunner

    ProgrammeRunner.objects.update_or_create(
        program_id=int(program_id), defaults={"org": org, "declared_by": by, "note": note}
    )
    revoke_links_to_runner(program_id, org, by=by)


def revoke_links_to_runner(program_id, org, *, by=None) -> int:
    """Revoke every live update link issued to `org` in this programme. Returns how many.

    `update_link_issue` refuses a link to the running organisation, but a link
    issued before that organisation was declared still works, and whoever holds
    it writes as the programme with no sign-in (design §8, M3). Declaring is the
    moment such a link becomes dangerous, so declaring is what ends it.
    """
    from django.utils import timezone

    from connect_labs.supply_chain.update_links.models import UpdateLink

    return UpdateLink.objects.filter(program_id=int(program_id), org=org, revoked_at__isnull=True).update(
        revoked_at=timezone.now(), revoked_by=by
    )


def open_to_any_domain(program_id) -> bool:
    """Whether a labs-only registration under this programme lets any partner read it.

    A labs-only opportunity with no `allowed_domains` and no workspace is open
    to every signed-in partner (`SyntheticOpportunity.is_accessible_to`), and a
    partner who can read the programme reads its quotes and prices (design §8,
    H3). A programme with no labs-only registration is governed by Connect.
    """
    from django.db.models import Q

    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    pid = int(program_id)
    registrations = SyntheticOpportunity.objects.filter(labs_only=True).filter(
        Q(program_id=pid) | Q(program_id__isnull=True, opportunity_id=pid)
    )
    return any(not opp.allowed_domains and opp._workspace() is None for opp in registrations)


def declare_dimagi_if_undeclared(program_id, note) -> None:
    """For Dimagi's own demo seeders: the programme they make is Dimagi's, unless someone said otherwise."""
    if declared_runner(program_id) is None:
        declare(program_id, dimagi_org(), note=note)


def is_running_org(org) -> bool:
    """Whether `org` runs any programme (or is Dimagi): a row only staff may change.

    Running a programme is authority, so its row must not be re-keyed, renamed,
    merged or handed a first admin by anyone else. Dimagi is included whether or
    not it has a declaration, because its people are recognised by domain.
    """
    from connect_labs.supply_chain.runner.models import ProgrammeRunner

    if org is None or not getattr(org, "pk", None):
        return False
    return org.slug == DIMAGI_ORG_SLUG or ProgrammeRunner.objects.filter(org_id=org.pk).exists()


def _user_of(access):
    caller = getattr(access, "caller", None)
    return getattr(access, "user", None) or getattr(caller, "user", None)


def is_system(access) -> bool:
    caller = getattr(access, "caller", None)
    return bool(caller is not None and caller.is_system)


def is_operator(user) -> bool:
    """Platform support: the Django staff bit, never an email domain."""
    return bool(user is not None and (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)))


def is_member(access, org) -> bool:
    """Whether the caller belongs to `org` by a grant, not by what they can see.

    - an OrgMembership granted FOR this organisation (one a merge carried
      across is not a grant anyone made for it);
    - for an organisation Connect knows, Connect's own membership of it;
    - for Dimagi, a Dimagi address.

    A labs-only organisation's slug in the caller's org list is NOT membership:
    it is there because the caller can see a synthetic opp filed under that
    name, and a whole email domain can.
    """
    from connect_labs.marketplace.models import OrgMembership

    user = _user_of(access)
    if org is None:
        return False
    if org.slug == DIMAGI_ORG_SLUG:
        # Dimagi's people only, by domain -- never by a membership row or a
        # Connect link, both of which a non-staff path could once create.
        return user is not None and is_dimagi_user(user)
    if (
        user is not None
        and getattr(user, "pk", None)
        and OrgMembership.objects.filter(org=org, user=user, granted_for_org_id=org.pk).exists()
    ):
        return True
    if org.labs_only or org.connect_organization_id is None:
        return False
    from connect_labs.supply_chain.identity import caller_org_ids

    try:
        ids = caller_org_ids(access)
    except Exception:  # noqa: BLE001 -- unknowable membership grants nothing
        logger.info("supply standing: could not read Connect organisations for %s", user)
        return False
    return bool(ids) and org.connect_organization_id in ids


@dataclass(frozen=True)
class Standing:
    role: str
    runner: LabsOrg | None

    @property
    def is_programme(self) -> bool:
        """The programme itself -- its own people, or support acting for it."""
        return self.role in (PROGRAMME, OPERATOR)


def standing(access) -> Standing:
    """What this caller is in the programme `access` is scoped to.

    Cached on the access object, which lives for one request or one tool call.
    Fails closed: anything that cannot be established is a partner.
    """
    cached = getattr(access, "_supply_standing", None)
    if cached is not None:
        return cached
    held = _standing(access)
    try:
        access._supply_standing = held
    except AttributeError:
        pass
    return held


def _standing(access) -> Standing:
    runner = running_org(getattr(access, "program_id", None))
    if runner is not None and is_member(access, runner):
        return Standing(PROGRAMME, runner)
    if is_operator(_user_of(access)):
        return Standing(OPERATOR, runner)
    return Standing(PARTNER, runner)


def refusal(access, held: Standing) -> str:
    """Why this caller may not write here -- naming who may."""
    who = held.runner.name if held.runner is not None else "no organisation yet"
    if held.runner is None:
        return (
            f"program {getattr(access, 'program_id', '?')} has no running organisation, so only platform "
            "support can change it. Ask support to declare one (supply_chain_programme_runner_set)."
        )
    return (
        f"only {who}, which runs program {getattr(access, 'program_id', '?')}, can change it. You can read it; "
        "a partner records its own deliveries through the update link it was sent."
    )

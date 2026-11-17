"""Who may use a CommCare project.

The rule is one function, ``may_use_project``, and everything that reads a project's data
(pipelines, queries, MCP tools, pages) goes through it, cached reads included.

Membership comes from CommCare itself: when a person signs in (or proves access again) the
domains their HQ account lists are written here with a timestamp; a membership older than
``MEMBERSHIP_TTL`` no longer counts. Labs never lets a person claim a domain.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta

from django.utils import timezone

from connect_labs.hqdata.models import HQIdentity, HQProject, HQProjectMember

MEMBERSHIP_TTL = timedelta(hours=24)
# What CommCare allows in a domain name. A domain goes into URL paths (/a/<domain>/api/...) and,
# on a customer store, into object names, so nothing else is ever accepted.
DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,62}$")


class ProjectAccessDenied(PermissionError):
    """The person is not (or is no longer) a member of the project."""


def may_use_project(user, project: HQProject, now: datetime | None = None) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    now = now or timezone.now()
    return HQProjectMember.objects.filter(project=project, user=user, verified_at__gt=now - MEMBERSHIP_TTL).exists()


def require_project(user, project: HQProject, now: datetime | None = None) -> None:
    if not may_use_project(user, project, now):
        raise ProjectAccessDenied(f"{user!r} is not a verified member of project {project.pk}")


def visible_projects(user, now: datetime | None = None):
    """Projects the person may use right now."""
    if user is None or not getattr(user, "is_authenticated", False):
        return HQProject.objects.none()
    now = now or timezone.now()
    return HQProject.objects.filter(members__user=user, members__verified_at__gt=now - MEMBERSHIP_TTL).distinct()


def refresh_memberships(
    user,
    domains: Iterable[str],
    *,
    hq_host: str,
    now: datetime | None = None,
    can_read: Callable[[str], bool] | None = None,
) -> dict[str, list[str]]:
    """Record what CommCare says about this person's domains.

    ``can_read(domain)``, when given, is the same proof ``create_project`` asks for. A domain
    merely LISTED on the account is not enough to keep reading its stored rows: someone demoted
    to a role that lists the domain but cannot view forms must not stay verified. A domain the
    probe rejects is not re-stamped, so the membership ages out like any unproven one (a probe
    cannot tell a revoked role from a network blip, so it never deletes).

    ``domains`` is the list the person's HQ identity returns. Existing memberships for a
    listed domain are re-stamped; one for a domain no longer listed is DELETED, not left to
    expire, because a fresh answer from HQ is the strongest evidence there is. Does not
    create projects or memberships for domains nobody has registered: registering one is
    ``create_project``.
    """
    now = now or timezone.now()
    listed = {d.strip().lower() for d in domains if d and d.strip()}
    restamped, removed = [], []
    for membership in HQProjectMember.objects.filter(user=user, project__hq_host=hq_host).select_related("project"):
        if membership.project.domain.lower() in listed:
            if can_read is not None and not can_read(membership.project.domain):
                continue
            membership.verified_at = now
            membership.save(update_fields=["verified_at"])
            restamped.append(membership.project.domain)
        else:
            removed.append(membership.project.domain)
            membership.delete()
    return {"verified": restamped, "removed": removed}


def create_project(
    user,
    domain: str,
    *,
    hq_host: str,
    can_read_domain: Callable[[], bool],
    name: str = "",
    now: datetime | None = None,
) -> HQProject:
    """Register a CommCare project, or join the one already registered, as proven by HQ.

    ``can_read_domain`` is the proof: it must return True only if the person's own token can
    read forms in the domain right now (``CommCareDataAccess.verify_hq_access``, which needs
    HQ's Edit Data permission). Nothing is created or granted without it. The first person
    in becomes the project's admin and its sync owner (whose token scheduled syncs use).
    """
    domain = domain.strip().lower()
    if not DOMAIN_RE.match(domain):
        raise ValueError("A project needs a CommCare domain: lower-case letters, digits and hyphens.")
    if not can_read_domain():
        raise ProjectAccessDenied(f"CommCare does not let this account read forms in {domain!r}.")
    now = now or timezone.now()
    project, created = HQProject.objects.get_or_create(
        hq_host=hq_host,
        domain=domain,
        defaults={"name": name or domain, "created_by": user, "sync_owner": user},
    )
    HQProjectMember.objects.update_or_create(
        project=project,
        user=user,
        defaults={"verified_at": now, **({"role": HQProjectMember.ROLE_ADMIN} if created else {})},
    )
    return project


def vouch_member(user, project: HQProject, now: datetime | None = None) -> HQProjectMember:
    """Record that CommCare HQ itself says ``user`` belongs to ``project`` (an embed client that vouches).

    Only ever a plain member, and only of the one project named: an existing role is left as it is, so
    HQ's word can neither promote anyone nor reach another project. The membership is the same revocable,
    24-hour record every other path uses, so ``may_use_project`` stays the single place access is decided.
    """
    member, created = HQProjectMember.objects.get_or_create(
        project=project, user=user, defaults={"verified_at": now or timezone.now()}
    )
    if not created:
        HQProjectMember.objects.filter(pk=member.pk).update(verified_at=now or timezone.now())
    return member


def is_commcare_only(user) -> bool:
    """A person who reaches labs only through CommCare: an HQ identity and no Connect token.

    Such a person is confined to ``/hq/`` by the labs session boundary (no ``labs_oauth``), and
    to ``hqdata`` tools elsewhere: anything that acts "as the user" for Connect data has no
    Connect token to act with.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    from connect_labs.labs.models import UserConnectToken

    return HQIdentity.objects.filter(user=user).exists() and not UserConnectToken.objects.filter(user=user).exists()

"""Doing things to a project: one place, used by the pages and by the MCP tools alike."""

from __future__ import annotations

from connect_labs.audit_trail.models import Action
from connect_labs.hqdata.access import DOMAIN_RE, ProjectAccessDenied, create_project
from connect_labs.hqdata.audit import project_event
from connect_labs.hqdata.identity import hq_host, sync_identity
from connect_labs.hqdata.models import HQIdentity, HQProject, HQSource
from connect_labs.labs.integrations.commcare.api_client import CommCareDataAccess


class Refused(Exception):
    """The change was not made. The message is written for the person who asked."""


def ensure_default_sources(project: HQProject) -> None:
    for kind in (HQSource.KIND_FORMS, HQSource.KIND_CASES):
        if not project.sources.filter(kind=kind).exists():
            HQSource.objects.create(project=project, kind=kind)


def register_project(user, domain: str, token: str) -> HQProject:
    """Add a CommCare project, or join the one already added, as proven by CommCare.

    Two things must hold and both are CommCare's word, not the caller's: the domain is among the
    projects the person's own account lists, and that account can read forms in it (the Edit Data
    permission). The domain is checked first, locally, so labs is never made to probe a project
    the person has nothing to do with.
    """
    domain = (domain or "").strip().lower()
    if not DOMAIN_RE.match(domain):
        raise Refused("Enter a CommCare project name: lower-case letters, digits and hyphens.")
    sync_identity(user, token)
    identity = HQIdentity.objects.filter(user=user).first()
    if identity is None or domain not in {d.lower() for d in identity.domains}:
        raise Refused(f"CommCare does not list {domain} among your projects.")
    try:
        project = create_project(
            user,
            domain,
            hq_host=hq_host(),
            can_read_domain=lambda: CommCareDataAccess(None, domain, cchq_access_token=token).verify_hq_access(),
        )
    except ProjectAccessDenied:
        raise Refused(
            f"CommCare would not let your account read forms in {domain}. Reading data needs the Edit Data permission."
        )
    ensure_default_sources(project)
    project_event(Action.CREATE, project.pk, user=user, kind="register")
    return project


def set_storage(project: HQProject, user, mode: str) -> HQProject:
    """Keep the project's data up to date on a schedule (``persistent``), or only when asked (``live``).

    Turning the schedule on makes the person doing it the one whose CommCare sign-in the scheduled
    syncs use: a project is never synced as somebody who did not choose that.
    """
    if mode not in (HQProject.STORAGE_LIVE, HQProject.STORAGE_PERSISTENT):
        raise Refused("Unknown storage choice.")
    updates = {"storage_mode": mode}
    if mode == HQProject.STORAGE_PERSISTENT:
        updates["sync_owner"] = user
        if project.status == HQProject.STATUS_NEEDS_REAUTH:
            updates["status"] = HQProject.STATUS_ACTIVE
        ensure_default_sources(project)
    HQProject.objects.filter(pk=project.pk).update(**updates)
    project.refresh_from_db()
    project_event(Action.UPDATE, project.pk, user=user, kind="storage", metadata={"storage_mode": mode})
    return project

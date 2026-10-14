"""Link the organisation registry to Connect's own organisations.

`LabsOrg` was built to carry Connect's join keys "from the start, so linking to
Connect later is an update rather than a migration". Connect has now made
organisations first class -- a profile on the row, and a merge tool that folds
duplicate workspaces into one and DELETES the other -- so this is that update,
run daily:

  1. **Read** every Connect organisation into `ConnectOrganization`
     (`/export/llo_entity/`, which since Connect retired `LLOEntity` exports
     Organization profiles under its old name), and every recorded merge
     (`/export/organization_merge/`). If the profile export is refused (it needs
     an internal permission the poller account may not hold), fall back to the
     poller's own `opp_org_program_list` -- which can link, but can never say an
     organisation is gone, because it is not the whole list.
  2. **Link** each `LabsOrg`, by the rule its docstring fixes in advance:
     the id is the identity; a slug (or alias, or a directory slug attribution)
     finds a row only while it has no id; an exact name finds one only when the
     name is unique on BOTH sides; and two rows resolving to one Connect
     organisation is a conflict, reported and never merged.
  3. **Follow merges.** A linked organisation whose Connect row was merged away
     is relinked to the survivor, and keeps its old slug as an alias so history
     recorded under it (pulse events, directory attributions) still resolves.
     Settings layers and workspace LLOs keyed by the old slug are reported --
     moving configuration is a person's call.

What this never does: create a `LabsOrg` (most Connect organisations are test
or demo workspaces, and the registry holds organisations a feature needed), or
write a `LabsOrg`'s name, short name or country. The directory import finds its
rows by name, so renaming one from Connect would make the next import mint a
duplicate. Connect's profile values reach the marketplace through
`marketplace.connect_profile` instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx
from django.db import transaction
from django.utils import timezone

from connect_labs.labs.models import ConnectOrganization, LabsOrg

logger = logging.getLogger(__name__)

PROFILE_ENDPOINT = "/export/llo_entity/"
MERGE_ENDPOINT = "/export/organization_merge/"
ORG_LIST_ENDPOINT = "/export/opp_org_program_list/"

# A merge chain longer than this is a cycle in bad data, not a real history.
MAX_MERGE_HOPS = 20


@dataclass
class ConnectRead:
    """What one read of Connect returned."""

    organizations: list[dict]
    merges: list[dict] = field(default_factory=list)
    # True only when `organizations` is Connect's whole list (the profile
    # export). Absence from a partial list proves nothing.
    complete: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class LinkReport:
    linked: list[str] = field(default_factory=list)
    relinked: list[str] = field(default_factory=list)
    slug_refreshed: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)
    orphaned_config: list[str] = field(default_factory=list)
    unlinked_labs: int = 0
    unlinked_connect: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "linked": len(self.linked),
            "relinked_after_merge": len(self.relinked),
            "slug_refreshed": len(self.slug_refreshed),
            "conflicts": len(self.conflicts),
            "gone_from_connect": len(self.gone),
            "orphaned_config": len(self.orphaned_config),
            "unlinked_labs_orgs": self.unlinked_labs,
            "unlinked_connect_orgs": self.unlinked_connect,
        }

    def findings(self) -> list[str]:
        """Everything a person should look at, one line each."""
        return [*self.notes, *self.conflicts, *self.gone, *self.orphaned_config]


# ── read ────────────────────────────────────────────────────────────────


def _status_of(exc: Exception) -> int | None:
    cause = exc.__cause__
    if isinstance(cause, httpx.HTTPStatusError):
        return cause.response.status_code
    return None


def read_connect(client) -> ConnectRead:
    """Read Connect's organisations and merges through an `ExportAPIClient`."""
    from connect_labs.labs.integrations.connect.export_client import ExportAPIError

    notes: list[str] = []
    try:
        organizations = client.fetch_all(PROFILE_ENDPOINT)
        complete = True
    except ExportAPIError as exc:
        if _status_of(exc) != 404:
            raise
        # Connect answers 404 to an account without the internal permission
        # the profile export is gated on. Say so, and link what we can see.
        notes.append(
            f"{PROFILE_ENDPOINT} refused (404): the poller account lacks Connect's "
            "llo_entity_internal_access permission. Linked from its own organisations "
            "only; nothing can be marked gone until it has the permission."
        )
        response = client.http_client.get(f"{client.base_url}{ORG_LIST_ENDPOINT}")
        response.raise_for_status()
        organizations = response.json().get("organizations") or []
        complete = False

    if complete and organizations and "slug" not in organizations[0]:
        # Connect's profile export carried no slug until dimagi/commcare-connect
        # added it for this. Without one, a profile can link only by id or name.
        notes.append(f"{PROFILE_ENDPOINT} rows carry no slug yet; linking by slug is limited to known ids.")

    try:
        merges = client.fetch_all(MERGE_ENDPOINT)
    except ExportAPIError as exc:
        if _status_of(exc) != 404:
            raise
        merges = []
        notes.append(f"{MERGE_ENDPOINT} unavailable (404): merged organisations are reported, not followed.")

    return ConnectRead(organizations=organizations, merges=merges, complete=complete, notes=notes)


# ── cache ───────────────────────────────────────────────────────────────


def _cache_fields(row: dict, *, profile: bool) -> dict:
    fields = {
        "name": (row.get("name") or "")[:255],
        "short_name": (row.get("short_name") or "")[:40],
        "is_test": bool(row.get("is_test")),
        "program_manager": bool(row.get("program_manager")),
        "funder": bool(row.get("funder")),
    }
    if row.get("slug"):
        fields["slug"] = row["slug"][:255]
    if profile:
        fields.update(
            has_profile=True,
            verified=bool(row.get("verified")),
            has_used_connect=bool(row.get("has_used_connect")),
            year_of_establishment=row.get("year_of_establishment"),
            team_size=(row.get("team_size") or "")[:20],
            flws_managed=row.get("flws_managed"),
            countries=[c for c in row.get("countries") or [] if c],
            regions=row.get("regions") or "",
            primary_sectors=[s for s in row.get("primary_sectors") or [] if s],
            website=(row.get("website") or "")[:500],
            office_address=row.get("office_address") or "",
        )
    return fields


def refresh_cache(read: ConnectRead, *, now=None) -> None:
    """Write a read into `ConnectOrganization`. Idempotent."""
    now = now or timezone.now()
    seen: set[int] = set()
    for row in read.organizations:
        raw_id = row.get("id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, int):
            continue  # labs-only synthetic orgs ride the list with slug ids
        seen.add(raw_id)
        fields = _cache_fields(row, profile=read.complete)
        fields.update(last_seen_at=now, gone_at=None, merged_into_id=None)
        ConnectOrganization.objects.update_or_create(connect_id=raw_id, defaults=fields)

    # A record whose target was itself merged away later has lost its target id
    # (Connect nulls the FK) but keeps the target's slug: resolve it through the
    # later merge's source, or a cached row, so A→B→C stays a chain.
    id_by_slug = {m["source_slug"]: m["source_id"] for m in read.merges if m.get("source_slug")}
    id_by_slug.update(
        {r["slug"]: r["id"] for r in read.organizations if r.get("slug") and isinstance(r.get("id"), int)}
    )
    merged_into = {}
    for merge in read.merges:
        target_id = merge.get("target_id")
        if not isinstance(target_id, int):
            slug = merge.get("target_slug") or ""
            target_id = id_by_slug.get(slug)
            if target_id is None and slug:
                cached_target = ConnectOrganization.objects.filter(slug=slug).order_by("-last_seen_at").first()
                target_id = cached_target.connect_id if cached_target else None
        if isinstance(merge.get("source_id"), int) and isinstance(target_id, int):
            merged_into[merge["source_id"]] = target_id

    if read.complete:
        ConnectOrganization.objects.exclude(connect_id__in=seen).filter(gone_at__isnull=True).update(gone_at=now)
    # A merge record is a fact whether or not the read was complete.
    for source_id, target_id in merged_into.items():
        if source_id in seen:
            continue  # recorded, but the source is evidently back (bad data): trust the list
        cached = ConnectOrganization.objects.filter(connect_id=source_id).first()
        if cached is None:
            source = next(m for m in read.merges if m.get("source_id") == source_id)
            ConnectOrganization.objects.create(
                connect_id=source_id,
                slug=(source.get("source_slug") or "")[:255],
                name=(source.get("source_name") or "")[:255],
                last_seen_at=now,
                gone_at=now,
                merged_into_id=target_id,
            )
        else:
            cached.gone_at = cached.gone_at or now
            cached.merged_into_id = target_id
            cached.save(update_fields=["gone_at", "merged_into_id"])


# ── link ────────────────────────────────────────────────────────────────


class _Index:
    """Live Connect organisations by id and slug, plus where merged ones went."""

    def __init__(self):
        rows = list(ConnectOrganization.objects.all())
        self.by_id = {r.connect_id: r for r in rows}
        self.live_by_slug = {r.slug: r for r in rows if r.slug and r.gone_at is None}
        self.gone_by_slug = {r.slug: r for r in rows if r.slug and r.gone_at is not None}

    def survivor(self, connect_id: int) -> ConnectOrganization | None:
        """The live organisation this id is now, following merges; None if none."""
        row = self.by_id.get(connect_id)
        for _ in range(MAX_MERGE_HOPS):
            if row is None:
                return None
            if row.gone_at is None:
                return row
            if row.merged_into_id is None:
                return None
            row = self.by_id.get(row.merged_into_id)
        return None

    def for_slug(self, slug: str) -> ConnectOrganization | None:
        if slug in self.live_by_slug:
            return self.live_by_slug[slug]
        gone = self.gone_by_slug.get(slug)
        return self.survivor(gone.connect_id) if gone else None


def _name_key(name: str) -> str:
    from connect_labs.marketplace.identity import slug_for

    return slug_for(name) if name and name.strip() else ""


def _keys_for(org: LabsOrg, attributions: dict[int, list[str]]) -> set[str]:
    return {k for k in (org.connect_organization_slug, *(org.aliases or []), *attributions.get(org.pk, [])) if k}


def _orphaned_config(slug: str) -> list[str]:
    """Labs state keyed by a Connect slug that no longer exists."""
    from connect_labs.labs.models import WorkspaceLLO
    from connect_labs.scope_config.models import ScopeConfig

    found = []
    for config in ScopeConfig.objects.filter(scope_type="organization", scope_key=slug):
        found.append(f"Settings '{config.namespace}' for organisation {slug!r}")
    for llo in WorkspaceLLO.objects.filter(slug=slug).select_related("workspace"):
        found.append(f"workspace {llo.workspace} LLO {slug!r}")
    return found


def link(*, dry_run: bool = False) -> LinkReport:
    """Reconcile every `LabsOrg` against the cached Connect organisations."""
    from connect_labs.marketplace.models import OrgConnectSlug

    report = LinkReport()
    index = _Index()
    attributions: dict[int, list[str]] = {}
    for attribution in OrgConnectSlug.objects.all():
        attributions.setdefault(attribution.org_id, []).append(attribution.slug)

    orgs = list(LabsOrg.objects.real().order_by("pk"))
    claimed: dict[int, LabsOrg] = {o.connect_organization_id: o for o in orgs if o.is_linked}
    updates: list[tuple[LabsOrg, dict]] = []

    # Linked rows first: the id is the identity, so they hold their claims.
    for org in orgs:
        if not org.is_linked:
            continue
        cached = index.by_id.get(org.connect_organization_id)
        if cached is None:
            continue  # never read (a partial read, or before the first run)
        if cached.gone_at is None:
            if cached.slug and cached.slug != org.connect_organization_slug:
                updates.append((org, {"connect_organization_slug": cached.slug}))
                report.slug_refreshed.append(f"{org.slug}: {org.connect_organization_slug!r} → {cached.slug!r}")
            continue

        survivor = index.survivor(cached.connect_id)
        old_slug = cached.slug or org.connect_organization_slug
        if survivor is None:
            report.gone.append(
                f"{org.slug} ({org.name}) is linked to Connect organisation {cached.connect_id} "
                f"({old_slug!r}), which Connect no longer has and recorded no merge for"
            )
            report.orphaned_config.extend(_orphaned_config(old_slug) if old_slug else [])
            continue
        holder = claimed.get(survivor.connect_id)
        if holder is not None and holder.pk != org.pk:
            report.conflicts.append(
                f"{org.slug} and {holder.slug} are one Connect organisation since Connect merged "
                f"{old_slug!r} into {survivor.slug!r} ({survivor.connect_id}); merge them in labs "
                "(labs/org_merge.py) -- never automatic"
            )
            continue
        aliases = list(org.aliases or [])
        if old_slug and old_slug not in aliases:
            aliases.append(old_slug)
        claimed.pop(org.connect_organization_id, None)
        claimed[survivor.connect_id] = org
        updates.append(
            (
                org,
                {
                    "connect_organization_id": survivor.connect_id,
                    "connect_organization_slug": survivor.slug,
                    "aliases": aliases,
                },
            )
        )
        report.relinked.append(f"{org.slug}: Connect {cached.connect_id} merged into {survivor.connect_id}")
        if old_slug:
            report.orphaned_config.extend(
                f"{finding} -- Connect merged it into {survivor.slug!r}" for finding in _orphaned_config(old_slug)
            )

    # Unlinked rows: their keys, else an exact name unique on both sides.
    live = [r for r in index.by_id.values() if r.gone_at is None]
    connect_names: dict[str, list[ConnectOrganization]] = {}
    for row in live:
        for name in {row.name, row.short_name}:
            key = _name_key(name)
            if key:
                connect_names.setdefault(key, [])
                if row not in connect_names[key]:
                    connect_names[key].append(row)
    labs_names: dict[str, list[LabsOrg]] = {}
    for org in orgs:
        key = _name_key(org.name)
        if key:
            labs_names.setdefault(key, []).append(org)

    proposals: dict[int, list[tuple[LabsOrg, ConnectOrganization, str]]] = {}
    for org in orgs:
        if org.is_linked:
            continue
        keys = _keys_for(org, attributions)
        found = {row.connect_id: row for row in (index.for_slug(k) for k in keys) if row is not None}
        if len(found) > 1:
            slugs = ", ".join(sorted(r.slug for r in found.values()))
            report.conflicts.append(
                f"{org.slug} ({org.name}) answers to {len(found)} Connect organisations ({slugs}): "
                "one partner, several workspaces -- Connect's merge tool is the fix; labs links none"
            )
            continue
        if found:
            row = next(iter(found.values()))
            proposals.setdefault(row.connect_id, []).append((org, row, "slug"))
            continue
        key = _name_key(org.name)
        candidates = [r for r in connect_names.get(key, []) if not r.is_test]
        if key and len(candidates) == 1 and len(labs_names.get(key, [])) == 1:
            proposals.setdefault(candidates[0].connect_id, []).append((org, candidates[0], "name"))

    for connect_id, claims in proposals.items():
        row = claims[0][1]
        holder = claimed.get(connect_id)
        if holder is not None:
            for org, _, how in claims:
                report.conflicts.append(
                    f"{org.slug} ({org.name}) matches Connect {row.slug!r} ({connect_id}) by {how}, "
                    f"which is already linked to {holder.slug}"
                )
            continue
        if len(claims) > 1:
            names = ", ".join(org.slug for org, _, _ in claims)
            report.conflicts.append(f"{names} all match Connect {row.slug!r} ({connect_id}); linked none")
            continue
        org, row, how = claims[0]
        claimed[connect_id] = org
        updates.append((org, {"connect_organization_id": connect_id, "connect_organization_slug": row.slug}))
        report.linked.append(f"{org.slug} → Connect {row.slug!r} ({connect_id}) by {how}")

    if not dry_run:
        with transaction.atomic():
            for org, fields in updates:
                for name, value in fields.items():
                    setattr(org, name, value)
                org.save(update_fields=[*fields, "updated_at"])

    linked_ids = set(claimed)
    report.unlinked_labs = sum(1 for o in orgs if o.connect_organization_id is None and o not in claimed.values())
    report.unlinked_connect = sum(1 for r in live if not r.is_test and r.connect_id not in linked_ids)
    return report


def sync(client, *, dry_run: bool = False) -> LinkReport:
    """Read Connect, refresh the cache, link. The one entry point."""
    read = read_connect(client)
    if dry_run:
        with transaction.atomic():
            refresh_cache(read)
            report = link(dry_run=True)
            transaction.set_rollback(True)
    else:
        refresh_cache(read)
        report = link()
    report.notes = read.notes + report.notes
    for finding in report.findings():
        logger.warning("connect_org_sync: %s", finding)
    return report

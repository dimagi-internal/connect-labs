"""Load the organisation registry from the LLO Directory -- the importer itself.

One implementation behind three callers: the ``marketplace_import`` management
command, the daily beat task, and the ``marketplace_import`` MCP tool. Each of them
used to be "whoever can run a management command on prod", which meant an agent that
had just added organisations to the directory sheet had to ask an engineer, or
dispatch a free-text command workflow that is prod code execution, to make them
appear. The work is a function now, with a structured result, so a caller can read
what happened -- organisations, contacts, rounds, who is still awaiting a verdict --
instead of scraping stdout.

Not destructive by default: organisations absent from the sheet are left alone
unless ``prune`` says the sheet is authoritative. The MCP tool never passes it.

The sheet is never written EXCEPT for two derived tabs labs owns: the data-quality
findings tab, and the access columns on the rounds tab. Import is otherwise a
read-only path over the master registry, so a parsing or matching bug cannot
reshape what people maintain by hand.
"""

from __future__ import annotations

import collections
import logging
from collections.abc import Callable

from django.core.cache import cache
from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace import directory, eoi
from connect_labs.marketplace.identity import ensure_org
from connect_labs.marketplace.models import OrgConnectSlug, OrgContact, OrgProfile
from connect_labs.marketplace.quality import audit, findings_tab_rows
from connect_labs.pulse.hq_location import operating_areas
from connect_labs.pulse.hq_location import resolve as resolve_hq

logger = logging.getLogger(__name__)

#: Held for the length of a real (non-dry) import. Two at once -- the beat firing while
#: someone runs it by hand -- would interleave writes to the same rows and the same
#: findings tab. Longer than any import has taken; a crash frees it by expiry.
LOCK_KEY = "marketplace:import:lock"
LOCK_SECONDS = 20 * 60


class ImportFailed(Exception):
    """The import could not run. ``code`` is stable for a caller to branch on."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


PROFILE_FIELDS = (
    "has_used_connect",
    "year_established",
    "team_size",
    "countries",
    "regions",
    "website",
    "office_address",
    "notes",
    "msa_link",
    "work_order_link",
)


def import_directory(org_rows, contact_rows, date_rows, map_rows, *, prune: bool = False) -> dict:
    """Upsert the registry from four tabs' rows. Returns a stats dict.

    Takes rows rather than a spreadsheet id so the whole of it is testable
    without a network or a service account.
    """
    orgs = directory.parse_organizations(org_rows)
    if not orgs:
        raise ValueError(
            f"'{directory.ORGANIZATIONS_TAB}' yielded no organisations — refusing to "
            "treat that as an empty registry. An empty read is a failed read."
        )

    dates = directory.parse_dates(date_rows)
    known = {o.name for o in orgs}
    mapped, skipped = directory.parse_mapping(map_rows, known)
    contacts = directory.parse_contacts(contact_rows)

    stats: dict = {
        "organisations": len(orgs),
        "contacts": 0,
        "attributions": 0,
        "skipped": list(skipped),
    }
    tiers: collections.Counter = collections.Counter()

    with transaction.atomic():
        by_name = {}
        for row in orgs:
            org = ensure_org(row.name, short_name=row.short_name)
            by_name[row.name] = org

            fields = {name: getattr(row, name) for name in PROFILE_FIELDS}
            fields["source_row"] = row.source_row

            located = resolve_hq(row.raw_countries, row.raw_regions, row.office_address, hq_city=row.hq_city)
            if located:
                fields.update(
                    country_iso3=located.iso3,
                    lat=located.lat,
                    lon=located.lon,
                    location_precision=located.precision,
                    location_label=located.label,
                )
                tiers[located.precision] += 1

            fields["operating_areas"] = operating_areas(row.countries, row.raw_regions)

            joined = dates.get(row.name)
            if joined:
                fields["joined_at"], fields["joined_basis"] = joined

            OrgProfile.objects.update_or_create(org=org, defaults=fields)

        seen_emails: list[str] = []
        for contact in contacts:
            org = by_name.get(contact.org_name)
            if org is None:
                # Never mint an organisation from a contact row: a typo on the
                # Contacts tab would create one that does not exist.
                stats["skipped"].append(
                    f"contact {contact.email} → {contact.org_name!r} (not on the "
                    f"{directory.ORGANIZATIONS_TAB} tab)"
                )
                continue
            # An email is unique per organisation, but a person can move between
            # them. Clear any stale row elsewhere before writing this one.
            OrgContact.objects.filter(email=contact.email).exclude(org=org).delete()
            OrgContact.objects.update_or_create(
                org=org,
                email=contact.email,
                defaults={
                    "full_name": contact.full_name,
                    "role_title": contact.role_title,
                    "is_main_poc": contact.is_main_poc,
                    "phone": contact.phone,
                    "notes": contact.notes,
                    "source_row": contact.source_row,
                },
            )
            seen_emails.append(contact.email)
            stats["contacts"] += 1

        # An organisation the directory never dated still belongs on the network
        # growth curve. Stamp the first date we saw it and then leave it alone:
        # writing it down once, here, is what stops the whole undated cohort
        # sliding forward every day the beat runs. Carried over from
        # pulse_partner_import, where the curve first needed it.
        stamped = OrgProfile.objects.filter(joined_at__isnull=True).update(joined_at=timezone.localdate())
        stats["stamped_with_today"] = stamped

        for slug, (target, why) in mapped.items():
            OrgConnectSlug.objects.update_or_create(slug=slug, defaults={"org": by_name[target], "why": why})
            stats["attributions"] += 1

        if prune:
            stale_orgs = list(LabsOrg.objects.filter(marketplace_profile__isnull=False).exclude(name__in=known))
            stats["pruned_organisations"] = 0
            stats["kept_in_use"] = 0
            for org in stale_orgs:
                # An organisation other domains depend on -- a supplier with
                # quotes and orders, a store's operator, a payee -- is still a
                # company after it leaves the directory. Only the directory's
                # own profile goes; deleting the organisation would either be
                # refused (crashing the import) or cascade into records that
                # are not the directory's.
                try:
                    with transaction.atomic():
                        org.delete()
                    stats["pruned_organisations"] += 1
                except ProtectedError:
                    OrgProfile.objects.filter(org=org).delete()
                    stats["kept_in_use"] += 1

            stats["pruned_contacts"] = OrgContact.objects.exclude(email__in=seen_emails).delete()[0]
            stats["pruned_attributions"] = OrgConnectSlug.objects.exclude(slug__in=mapped).delete()[0]

    stats["located"] = dict(tiers)
    return stats


#: ``emit(level, line)``: ``level`` is "info", "warning" or "success". The command maps
#: them to its terminal styles; the MCP tool drops them (the structured result carries
#: the same facts).
Emit = Callable[[str, str], None]


def _quiet(level: str, line: str) -> None:
    logger.info("marketplace_import: %s", line)


def run_import(
    *,
    spreadsheet_id: str | None = None,
    dry_run: bool = False,
    eoi_rounds: bool = False,
    check_access: bool = False,
    prune: bool = False,
    emit: Emit = _quiet,
) -> dict:
    """Read the LLO Directory and update the registry; return what happened.

    Raises ``ImportFailed`` (never a bare exception) for the failures a caller can act
    on: the sheet cannot be read (``DIRECTORY_UNREADABLE``), it yielded no
    organisations (``DIRECTORY_EMPTY``) or another import is running
    (``IMPORT_IN_PROGRESS``). A dry run writes nothing and takes no lock.
    """
    sid = spreadsheet_id or directory.DIRECTORY_ID
    try:
        org_rows = directory.read_tab(sid, directory.ORGANIZATIONS_TAB)
        contact_rows = directory.read_tab(sid, directory.CONTACTS_TAB)
        date_rows = directory.read_tab(sid, directory.DATES_TAB)
        map_rows = directory.read_tab(sid, directory.MAPPING_TAB)
    except Exception as exc:  # noqa: BLE001 — surfaced verbatim, the causes are many
        raise ImportFailed(
            "DIRECTORY_UNREADABLE",
            f"Could not read the directory: {exc}. Set LABS_SYNTHETIC_GDRIVE_SA_KEY, and "
            "confirm the service account has been shared the sheet.",
        ) from exc

    orgs = directory.parse_organizations(org_rows)
    contacts = directory.parse_contacts(contact_rows)

    if dry_run:
        mapped, skipped = directory.parse_mapping(map_rows, {o.name for o in orgs})
        emit(
            "info",
            f"would import {len(orgs)} organisations, {len(contacts)} contacts, {len(mapped)} slug attributions",
        )
        findings = audit(org_rows, contact_rows, orgs, contacts, skipped)
        return {
            "dry_run": True,
            "organisations": len(orgs),
            "contacts": len(contacts),
            "attributions": len(mapped),
            "skipped": list(skipped),
            "findings": _findings_summary(findings, emit),
        }

    # ``is False``, not ``not``: with Redis down the cache degrades to ``None`` (IGNORE_EXCEPTIONS),
    # and an outage must not read as "an import is running" and block every import.
    if cache.add(LOCK_KEY, timezone.now().isoformat(), LOCK_SECONDS) is False:
        raise ImportFailed(
            "IMPORT_IN_PROGRESS",
            "Another marketplace import is running. Wait for it to finish (a few minutes at most) and call again.",
        )
    try:
        return _import_locked(
            sid, org_rows, contact_rows, date_rows, map_rows, orgs, contacts,
            eoi_rounds=eoi_rounds, check_access=check_access, prune=prune, emit=emit,
        )  # fmt: skip
    finally:
        cache.delete(LOCK_KEY)


def _findings_summary(findings, emit: Emit) -> dict:
    by_kind: dict[str, int] = {}
    for finding in findings:
        by_kind[finding.kind] = by_kind.get(finding.kind, 0) + 1
    if not findings:
        emit("success", "no data-quality findings")
    else:
        emit("warning", f"\n{len(findings)} data-quality findings:")
        for finding in findings:
            emit("info", f"  {finding}")
        emit("info", "  " + ", ".join(f"{n} {kind}" for kind, n in sorted(by_kind.items())))
    return {"count": len(findings), "by_kind": by_kind}


def _import_locked(
    sid, org_rows, contact_rows, date_rows, map_rows, orgs, contacts, *, eoi_rounds, check_access, prune, emit
) -> dict:
    try:
        stats = import_directory(org_rows, contact_rows, date_rows, map_rows, prune=prune)
    except ValueError as exc:
        raise ImportFailed("DIRECTORY_EMPTY", str(exc)) from exc

    findings = audit(org_rows, contact_rows, orgs, contacts, stats["skipped"])
    findings_summary = _findings_summary(findings, emit)
    counts = ", ".join(f"{n} {kind}" for kind, n in sorted(findings_summary["by_kind"].items()))
    # To the sheet as well as to the log: the people who can fix these work in the
    # spreadsheet, and a findings list that only reaches CloudWatch is one nobody who
    # maintains the directory will ever read.
    try:
        directory.write_tab(
            sid,
            directory.FINDINGS_TAB,
            findings_tab_rows(findings, when=timezone.now().strftime("%Y-%m-%d %H:%M UTC"), counts_line=counts),
        )
        emit("info", f"  findings published to the '{directory.FINDINGS_TAB}' tab")
        findings_summary["published"] = True
    except Exception as exc:  # noqa: BLE001 — never fail an import over its own report
        emit("warning", f"  could not publish findings: {exc}")
        findings_summary["published"] = False

    located = ", ".join(f"{n} {tier}" for tier, n in stats["located"].items())
    emit(
        "success",
        f"imported {stats['organisations']} organisations, {stats['contacts']} contacts, "
        f"{stats['attributions']} slug attributions ({located or 'none located'})",
    )

    from connect_labs.pulse.partner_names import invalidate

    invalidate()

    result = {"dry_run": False, **stats, "findings": findings_summary}
    if eoi_rounds or check_access:
        result["rounds"] = _import_rounds(sid, eoi_rounds=eoi_rounds, emit=emit)
    return result


def _import_rounds(sid, *, eoi_rounds: bool, emit: Emit) -> dict:
    """Import the EOI/RFP tab, then each round's submissions."""
    round_rows = directory.read_tab(sid, directory.ROUNDS_TAB)
    rounds, skipped = directory.parse_rounds(round_rows)
    out: dict = {"skipped": list(skipped), "access_problems": []}
    for line in skipped:
        emit("warning", f"  round skipped: {line}")
    stats = eoi.upsert_rounds(rounds)
    for slug in stats["collisions"]:
        emit("warning", f"  round skipped: slug {slug!r} belongs to a solicitation that is not a round")
        out["skipped"].append(f"{slug}: belongs to a solicitation that is not a round")
    for slug in stats["folded"]:
        emit("info", f"  {slug}: was a round of its own, now a form of its group — submissions moved")
    for slug in stats["fold_refused"]:
        emit(
            "warning",
            f"  {slug}: grouped under another round but has reviewing work of its own "
            "(criteria, a summary or scored versions) — left as it is; merge it by hand",
        )
        out["skipped"].append(f"{slug}: grouped under another round but has reviewing work of its own")
    out["rounds"] = stats["rounds"]
    emit("success", f"{stats['rounds']} rounds")

    results = eoi.check_access(directory.read_tab, write_back_to=rounds, spreadsheet_id=sid)
    readable = [r for r in results if r["state"] == "ok"]
    for result in results:
        if result["state"] != "ok":
            emit("warning", f"  {result['slug']}: {result['state']} — {result['detail']}")
            out["access_problems"].append(
                {"slug": result["slug"], "form": result.get("form", result["slug"]), "state": result["state"]}
            )
    out["forms_readable"], out["forms_total"] = len(readable), len(results)
    emit("info", f"{len(readable)} of {len(results)} forms readable")

    if not eoi_rounds:
        return out

    # Verdicts a person already reached, which outrank every inference.
    known = {o.name for o in directory.parse_organizations(directory.read_tab(sid, directory.ORGANIZATIONS_TAB))}
    try:
        verdict_rows = directory.read_tab(sid, directory.RESPONSE_MAPPING_TAB)
    except Exception:  # noqa: BLE001 — the tab is optional until someone needs it
        verdict_rows = []
        emit("info", f"  (no {directory.RESPONSE_MAPPING_TAB} tab yet — nothing to apply)")
    verdicts, verdict_skips = directory.parse_response_mapping(verdict_rows, known)
    for line in verdict_skips:
        emit("warning", f"  verdict refused: {line}")
    if verdicts:
        emit("info", f"  {len(verdicts)} human verdicts to apply")
    out["verdicts_applied"] = len(verdicts)
    out["verdicts_refused"] = list(verdict_skips)

    totals = {"submissions": 0, "matched": 0, "unmatched": 0}
    per_round = []
    # A round ingests every form that could be read; one unreadable form must not hold
    # back the others' submissions.
    readable_forms: dict = {}
    for result in readable:
        readable_forms.setdefault(result["slug"], []).append(result.get("form", result["slug"]))
    for slug, keys in readable_forms.items():
        round_ = eoi.Solicitation.objects.get(slug=slug)
        forms = {f["key"]: f for f in round_.directory_forms}
        rows = {
            key: directory.read_tab(
                forms[key]["response_spreadsheet_id"], forms[key].get("response_tab") or "Form Responses 1"
            )
            for key in keys
            if key in forms
        }
        got = eoi.ingest_round(round_, rows, human_verdicts=verdicts)
        for key in totals:
            totals[key] += got[key]
        per_round.append({"slug": round_.slug, **{key: got[key] for key in totals}})
        emit(
            "info",
            f"  {round_.slug}: {got['submissions']} submissions, "
            f"{got['matched']} matched, {got['unmatched']} unmatched",
        )
        for key, matched in got["structure"].items():
            if isinstance(matched, int):
                emit("info", f"    {key}: {matched} questions take their form's choices and sections")
            else:
                emit("warning", f"    {key}: {matched}; its questions stay text boxes")
    emit(
        "success",
        f"{totals['submissions']} submissions — {totals['matched']} matched to an organisation, "
        f"{totals['unmatched']} awaiting a human verdict",
    )
    out["per_round"] = per_round
    out["totals"] = totals
    return out

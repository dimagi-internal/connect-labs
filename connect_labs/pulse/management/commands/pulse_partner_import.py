"""Load delivery-partner identity from the LLO Directory into the database.

    make manage CMD="pulse_partner_import --dry-run"
    make manage CMD="pulse_partner_import"

The directory is the source of truth for who a partner is. This pulls it in so
that no partner name lives in the repository, where the people who own that
identity cannot review it and where it drifts silently from the sheet.

Reads two tabs:

  Organizations         column A the name, column B the short name.
  Connect Org Mapping   column A a Connect org slug, column I the partner to
                        attribute it to, for the slugs no string rule reaches.
                        Column F carries the reason, which is required — an
                        alias without one is a guess someone will later trust.

Authenticates with the service account already on both task definitions
(``LABS_SYNTHETIC_GDRIVE_SA_KEY``), the same one targeting uses. Nothing new to
provision.

Not destructive by default: partners absent from the sheet are left alone
unless ``--prune`` says the sheet is authoritative, which mirrors
``targeting_import`` and exists for the same reason — a half-loaded sheet
should not silently delete a partner mid-run.
"""

from __future__ import annotations

from urllib.parse import quote

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from connect_labs.pulse.models import PulsePartner, PulsePartnerAlias

DIRECTORY_ID = "19sqU7xpSb_0VX6H_QZK2dcRz0RvXiQ1En9kvSZkEiY8"
ORGANIZATIONS_TAB = "Organizations"
MAPPING_TAB = "Connect Org Mapping"


def _read_tab(spreadsheet_id: str, tab: str) -> list[list[str]]:
    """One tab as rows.

    Talks to the Sheets REST API over httpx with a service-account bearer, the
    same shape ``labs.synthetic.gdrive`` uses for Drive — the Google API client
    library is deliberately not a dependency here. The Sheets API accepts the
    ``auth/drive`` scope the service account already carries, so nothing new
    needs provisioning. Addressing the tab by name matters: a Drive export only
    ever yields the first one.
    """
    import httpx
    from google.auth.transport.requests import Request

    from connect_labs.labs.synthetic.gdrive import _load_credentials

    creds = _load_credentials()
    if not creds.valid:
        creds.refresh(Request())
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{quote(tab)}"
    got = httpx.get(url, headers={"Authorization": f"Bearer {creds.token}"}, timeout=60)
    got.raise_for_status()
    return got.json().get("values", [])


def _cell(row: list[str], index: int) -> str:
    return row[index].strip() if len(row) > index else ""


class Command(BaseCommand):
    help = "Import partner names and slug aliases from the LLO Directory"

    def add_arguments(self, parser):
        parser.add_argument("--spreadsheet-id", default=DIRECTORY_ID)
        parser.add_argument("--dry-run", action="store_true", help="Report what would change, write nothing")
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Treat the sheet as authoritative: delete partners and aliases it does not carry",
        )

    def handle(self, *args, **opts):
        sid = opts["spreadsheet_id"]
        try:
            org_rows = _read_tab(sid, ORGANIZATIONS_TAB)
            map_rows = _read_tab(sid, MAPPING_TAB)
        except Exception as exc:  # noqa: BLE001 — surfaced verbatim, the causes are many
            raise CommandError(
                f"Could not read the directory: {exc}\n"
                "Set LABS_SYNTHETIC_GDRIVE_SA_KEY, and confirm the service account "
                "has been shared the sheet."
            ) from exc

        partners: dict[str, str] = {}
        for row in org_rows[1:]:
            name = _cell(row, 0)
            if name and name not in partners:
                partners[name] = _cell(row, 1)
        if not partners:
            raise CommandError(f"'{ORGANIZATIONS_TAB}' yielded no names — refusing to treat that as an empty roster.")

        aliases: dict[str, tuple[str, str]] = {}
        skipped: list[str] = []
        for row in map_rows[1:]:
            slug, target, why = _cell(row, 0), _cell(row, 8), _cell(row, 5)
            if not slug or not target:
                continue
            if target not in partners:
                skipped.append(f"{slug} → {target!r} (not on the {ORGANIZATIONS_TAB} tab)")
                continue
            if not why:
                skipped.append(f"{slug} → {target!r} (no reason given)")
                continue
            aliases[slug] = (target, why)

        self.stdout.write(f"{len(partners)} partners, {len(aliases)} aliases in the sheet")
        for line in skipped:
            self.stdout.write(self.style.WARNING(f"  skipped {line}"))

        if opts["dry_run"]:
            have = set(PulsePartner.objects.values_list("name", flat=True))
            self.stdout.write(f"  would add {len(set(partners) - have)}, update {len(set(partners) & have)}")
            if opts["prune"]:
                self.stdout.write(f"  would delete {len(have - set(partners))}")
            return

        with transaction.atomic():
            for name, short in partners.items():
                PulsePartner.objects.update_or_create(name=name, defaults={"short": short})
            by_name = {p.name: p for p in PulsePartner.objects.all()}
            for slug, (target, why) in aliases.items():
                PulsePartnerAlias.objects.update_or_create(
                    slug=slug, defaults={"partner": by_name[target], "why": why}
                )
            if opts["prune"]:
                gone = PulsePartner.objects.exclude(name__in=partners).delete()[0]
                stale = PulsePartnerAlias.objects.exclude(slug__in=aliases).delete()[0]
                self.stdout.write(self.style.WARNING(f"pruned {gone} partners, {stale} aliases"))

        self.stdout.write(self.style.SUCCESS(f"imported {len(partners)} partners, {len(aliases)} aliases"))

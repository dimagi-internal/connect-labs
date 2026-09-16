"""Importing EOI/RFP rounds and their submissions.

A round's responses live in a separate spreadsheet that has to be shared with
the labs service account explicitly, per file. That is deliberate — access is a
decision, not a consequence of where a file sits — and it means a new round is
unreadable by default.

The failure mode that matters is therefore **an import that succeeds while
skipping a sheet it could not open**: the round then shows zero applicants and
looks merely unpopular. So an unreadable sheet is recorded as such on the round,
the directory renders "not ingested" rather than "no applicants", and nothing
pretends a silent zero is a finding.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from connect_labs.marketplace import responses as response_reader
from connect_labs.marketplace.matching import match_submission, normalise
from connect_labs.solicitations.local_models import (
    ACCESS_DENIED,
    ACCESS_MISSING,
    ACCESS_OK,
    Solicitation,
    SolicitationResponse,
)

DATE_FIELDS = ("published_on", "application_deadline", "decision_on", "expected_start_date", "expected_end_date")


def _date(raw):
    """A directory date cell, or None. The tab holds "Rolling", "Ongoing",
    "ASAP" and "Open ended" as often as it holds a date."""
    from connect_labs.marketplace.responses import parse_timestamp

    value = (raw or "").strip()
    if not value:
        return None
    for fmt in ("%d %B %Y", "%B %d %Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            import datetime as dt

            return dt.datetime.strptime(value.replace(",", ""), fmt).date()
        except ValueError:
            continue
    stamp = parse_timestamp(value)
    return stamp.date() if stamp else None


def upsert_rounds(rounds) -> dict:
    """Create or update a Solicitation per round row. Never deletes."""
    stats = {"rounds": 0}
    for row in rounds:
        defaults = {
            "title": row.title,
            "solicitation_type": row.solicitation_type,
            "status": row.status,
            "target_countries": row.target_countries,
            "announcement_url": row.announcement_url[:1000],
            "form_url": row.form_url[:1000],
            "response_spreadsheet_id": row.response_spreadsheet_id,
            "response_tab": row.response_tab,
            "column_map": row.column_map,
            "notes": row.notes,
        }
        for name in DATE_FIELDS:
            defaults[name] = _date(getattr(row, name))
        Solicitation.objects.update_or_create(slug=row.slug, defaults=defaults)
        stats["rounds"] += 1
    return stats


ACCESS_LABELS = {
    ACCESS_OK: "OK — readable",
    ACCESS_DENIED: "NO ACCESS — share the response sheet with the labs service account",
    ACCESS_MISSING: "NO SHEET — add a Response Sheet Link",
}


def check_access(read_tab, *, write_back_to=None, spreadsheet_id=None) -> list[dict]:
    """Attempt a real read of every round's response sheet, and record the result.

    Verification, not a claim. The column this writes back to the sheet is only
    worth having if it reports what was actually true at the moment it was
    checked, which means the thing that checks it has to be the thing that
    writes it — a hand-maintained "yes, access granted" box goes stale the
    moment a sheet is moved or re-owned, and fails in the worst direction.

    Labs writes exactly two cells per round and nothing else in the workbook.
    """
    results = []
    for round_ in Solicitation.objects.all():
        if not round_.response_spreadsheet_id:
            state, detail = ACCESS_MISSING, "no response sheet recorded"
        else:
            try:
                read_tab(round_.response_spreadsheet_id, round_.response_tab or "Form Responses 1")
                state, detail = ACCESS_OK, "readable"
            except Exception as exc:  # noqa: BLE001 — the message is the useful part
                state, detail = ACCESS_DENIED, str(exc)[:200]
        round_.sa_access_state = state
        round_.sa_access_checked_at = timezone.now()
        round_.save(update_fields=["sa_access_state", "sa_access_checked_at"])
        results.append({"slug": round_.slug, "state": state, "detail": detail, "source_row": None})

    if write_back_to and spreadsheet_id:
        _write_access_columns(results, write_back_to, spreadsheet_id)
    return results


def _write_access_columns(results, rounds, spreadsheet_id) -> None:
    """Put the verified state in the two columns labs owns on the rounds tab.

    Keyed on the row each round was parsed from, so a reordered sheet cannot
    write a verdict against the wrong round. A round whose row is unknown is
    skipped rather than guessed at.
    """
    from connect_labs.marketplace import directory

    by_slug = {r.slug: r.source_row for r in rounds if r.source_row}
    stamp = timezone.now().strftime("%Y-%m-%d %H:%M UTC")
    updates = []
    for result in results:
        row = by_slug.get(result["slug"])
        if not row:
            continue
        label = ACCESS_LABELS.get(result["state"], result["state"])
        # Columns P and Q: "Labs Access" and "Labs Access Checked".
        updates.append((f"'{directory.ROUNDS_TAB}'!P{row}:Q{row}", [[label, stamp]]))
    directory.update_cells(spreadsheet_id, updates)


def ingest_round(round_: Solicitation, rows, human_verdicts=None) -> dict:
    """Store one round's submissions, matched to organisations where certain."""
    from connect_labs.labs.models import LabsOrg
    from connect_labs.marketplace.models import OrgContact

    questions, submissions = response_reader.parse_submissions(rows, round_.column_map or None)

    by_email = {c.email.lower(): c.org for c in OrgContact.objects.select_related("org")}
    by_name = {}
    for org in LabsOrg.objects.all():
        key = normalise(org.name)
        # A normalised collision between two real organisations must not resolve
        # to either of them, so drop both rather than pick one.
        by_name[key] = None if key in by_name else org

    # The mapping tab names an organisation; the relation needs the row. Resolve
    # once here so the matcher only ever deals in objects, as it does for the
    # email and name indexes. A name that resolves to nothing is dropped rather
    # than guessed at -- the same rule the rest of the matcher follows.
    resolved_verdicts = {}
    for key, (name, verdict, why) in (human_verdicts or {}).items():
        org = LabsOrg.objects.filter(name=name).first() if name else None
        if name and org is None:
            continue
        resolved_verdicts[key] = (org, verdict, why)

    stats = {"submissions": 0, "matched": 0, "unmatched": 0, "dismissed": 0}
    with transaction.atomic():
        round_.questions = [{"id": q.id, "text": q.text, "column": q.column} for q in questions]

        for sub in submissions:
            sub.round_slug = round_.slug
            org, state, basis = match_submission(
                sub, by_email=by_email, by_name={k: v for k, v in by_name.items() if v}, human=resolved_verdicts
            )
            SolicitationResponse.objects.update_or_create(
                solicitation=round_,
                source_row=sub.source_row,
                defaults={
                    "llo_entity": org,
                    "llo_entity_name": sub.org_name[:300],
                    "org_name": sub.org_name[:300],
                    "responses": sub.answers,
                    "submitted_by_name": sub.contact_name[:200],
                    "submitted_by_email": ", ".join(sub.emails)[:500],
                    "submission_date": sub.submitted_at,
                    "country_as_submitted": sub.country[:300],
                    "website_as_submitted": sub.website[:500],
                    "source_url": (
                        f"https://docs.google.com/spreadsheets/d/{round_.response_spreadsheet_id}"
                        f"/edit#gid=0&range=A{sub.source_row}"
                        if round_.response_spreadsheet_id
                        else ""
                    ),
                    "match_state": state,
                    "match_basis": basis,
                },
            )
            stats["submissions"] += 1
            if state == SolicitationResponse.MATCH_NOT_LLO:
                # Decided, not outstanding: it leaves the review queue.
                stats["dismissed"] += 1
            elif org is not None:
                stats["matched"] += 1
            else:
                stats["unmatched"] += 1

        round_.last_ingested_at = timezone.now()
        round_.save(update_fields=["questions", "last_ingested_at"])
    return stats

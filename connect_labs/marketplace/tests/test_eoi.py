"""Importing rounds and their submissions. All data invented."""

import pytest
from django.utils import timezone

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace import eoi
from connect_labs.marketplace.directory import parse_rounds
from connect_labs.marketplace.eoi import check_access, ingest_round, upsert_rounds
from connect_labs.marketplace.models import OrgContact
from connect_labs.solicitations.local_models import (
    ACCESS_DENIED,
    ACCESS_MISSING,
    ACCESS_OK,
    Solicitation,
    SolicitationResponse,
)

HEADER = [
    "Slug",
    "Name",
    "Type",
    "Status",
    "Published Date",
    "Application Deadline",
    "Decision",
    "Start",
    "End",
    "Countries",
    "Announcement",
    "Form",
    "Response Sheet",
    "Response Tab",
    "Column Map",
    "Labs Access",
    "Checked",
    "Notes",
]
SHEET = "https://docs.google.com/spreadsheets/d/1abcDEFghiJKLmnoPQRstuVWxyz0123456789"

ROUND_ROWS = [
    HEADER,
    [
        "demo-2026",
        "Demo round",
        "EOI",
        "Closed",
        "1 March 2026",
        "1 April 2026",
        "",
        "",
        "",
        "Kenya",
        "",
        "",
        SHEET,
        "Form Responses 1",
        "",
        "",
        "",
        "",
    ],
]

RESPONSES = [
    ["Timestamp", "Organization name", "Valid email address", "Country"],
    ["3/4/2026 09:00:00", "Fenwick Trust", "a@example.invalid", "Kenya"],
    ["3/5/2026 09:00:00", "Unknown Body", "z@example.invalid", "Kenya"],
]


@pytest.fixture
def round_(db):
    rounds, _ = parse_rounds(ROUND_ROWS)
    upsert_rounds(rounds)
    return Solicitation.objects.get(slug="demo-2026")


@pytest.mark.django_db
class TestUpsertRounds:
    def test_creates_a_round_with_its_dates_and_sheet(self, round_):
        assert round_.title == "Demo round"
        assert round_.solicitation_type == "eoi"
        assert round_.published_on.isoformat() == "2026-03-01"
        assert round_.response_spreadsheet_id == "1abcDEFghiJKLmnoPQRstuVWxyz0123456789"

    def test_is_idempotent(self, round_):
        rounds, _ = parse_rounds(ROUND_ROWS)
        upsert_rounds(rounds)
        assert Solicitation.objects.count() == 1

    def test_a_free_text_date_becomes_none_rather_than_raising(self, db):
        rows = [
            HEADER,
            [
                "rolling-2026",
                "Rolling",
                "EOI",
                "Closed",
                "Rolling",
                "Open ended",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ],
        ]
        rounds, _ = parse_rounds(rows)
        upsert_rounds(rounds)
        assert Solicitation.objects.get(slug="rolling-2026").published_on is None


@pytest.mark.django_db
class TestCheckAccess:
    def test_records_a_readable_sheet(self, round_):
        [result] = check_access(lambda sid, tab: [["Timestamp"]])
        assert result["state"] == ACCESS_OK
        round_.refresh_from_db()
        assert round_.sa_access_state == ACCESS_OK
        assert round_.sa_access_checked_at is not None

    def test_records_a_refusal_with_its_reason(self, round_):
        def denied(sid, tab):
            raise PermissionError("403 caller does not have permission")

        [result] = check_access(denied)
        assert result["state"] == ACCESS_DENIED
        assert "403" in result["detail"]

    def test_a_round_with_no_sheet_is_missing_not_denied(self, db):
        rows = [
            HEADER,
            ["nosheet", "No sheet", "EOI", "Closed", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
        ]
        rounds, _ = parse_rounds(rows)
        upsert_rounds(rounds)
        results = {r["slug"]: r["state"] for r in check_access(lambda sid, tab: [])}
        assert results["nosheet"] == ACCESS_MISSING


@pytest.mark.django_db
class TestIngestRound:
    def test_stores_submissions_and_matches_a_known_contact(self, round_):
        org = LabsOrg.objects.create(slug="fenwick", name="Fenwick Trust")
        OrgContact.objects.create(org=org, email="a@example.invalid", full_name="A Person")

        stats = ingest_round(round_, RESPONSES)
        assert stats["submissions"] == 2
        assert stats["matched"] == 1
        assert stats["unmatched"] == 1
        assert round_.responses.get(source_row=2).llo_entity == org

    def test_an_unknown_applicant_is_kept_and_left_unmatched(self, round_):
        ingest_round(round_, RESPONSES)
        unmatched = round_.responses.get(source_row=3)
        assert unmatched.llo_entity is None
        assert unmatched.org_name == "Unknown Body"
        assert unmatched.responses  # its answers are kept regardless

    def test_records_the_question_list_on_the_round(self, round_):
        ingest_round(round_, RESPONSES)
        round_.refresh_from_db()
        assert [q["text"] for q in round_.questions][:2] == ["Timestamp", "Organization name"]

    def test_is_idempotent(self, round_):
        ingest_round(round_, RESPONSES)
        ingest_round(round_, RESPONSES)
        assert round_.responses.count() == 2

    def test_marks_the_round_ingested_so_empty_is_distinguishable(self, round_):
        """ "No applicants" and "never read" must not look the same."""
        assert round_.was_ingested is False
        ingest_round(round_, RESPONSES)
        round_.refresh_from_db()
        assert round_.was_ingested is True

    def test_two_organisations_normalising_alike_match_neither(self, round_):
        """Resolving an ambiguous name to whichever row was created first would
        file one organisation's application under another's."""
        LabsOrg.objects.create(slug="fenwick-a", name="Fenwick Trust")
        LabsOrg.objects.create(slug="fenwick-b", name="Fenwick (Trust)")
        ingest_round(round_, RESPONSES)
        assert round_.responses.get(source_row=2).llo_entity is None


@pytest.mark.django_db
class TestEarlierRoundsAreEvidence:
    """An inbox that was matched in one round identifies the same organisation
    in the next, provided the name it gives still agrees."""

    LATER = [
        ["Timestamp", "Organization name", "Valid email address"],
        ["4/1/2026 09:00:00", "EHA CLINIC REACH PROGRAM", "eha@example.invalid"],
    ]

    @pytest.fixture
    def earlier(self, round_):
        org = LabsOrg.objects.create(slug="eha", name="EHA Clinics (REACH Program)")
        old = Solicitation.objects.create(slug="old-2025", title="Old round", status="closed")
        SolicitationResponse.objects.create(
            solicitation=old,
            llo_entity=org,
            source_row=2,
            org_name="EHA Clinics",
            submitted_by_email="eha@example.invalid",
            match_state=SolicitationResponse.MATCH_HUMAN,
        )
        return org

    def test_a_submission_is_matched_on_an_email_from_another_round(self, round_, earlier):
        ingest_round(round_, self.LATER)
        got = round_.responses.get(source_row=2)
        assert got.llo_entity == earlier
        assert got.match_state == SolicitationResponse.MATCH_EMAIL

    def test_a_round_is_never_its_own_evidence(self, round_):
        """Otherwise a match, once made, would re-confirm itself on every
        re-import however it was first reached."""
        org = LabsOrg.objects.create(slug="eha", name="EHA Clinics (REACH Program)")
        SolicitationResponse.objects.create(
            solicitation=round_,
            llo_entity=org,
            source_row=2,
            org_name="EHA CLINIC REACH PROGRAM",
            submitted_by_email="eha@example.invalid",
            match_state=SolicitationResponse.MATCH_EMAIL,
        )
        ingest_round(round_, self.LATER)
        assert round_.responses.get(source_row=2).llo_entity is None

    def test_an_inbox_that_applied_for_two_organisations_matches_neither(self, round_, earlier):
        other = LabsOrg.objects.create(slug="eha2", name="EHA Clinic Reach Programme Kano")
        old = Solicitation.objects.create(slug="old-2024", title="Older round", status="closed")
        SolicitationResponse.objects.create(
            solicitation=old,
            llo_entity=other,
            source_row=5,
            org_name="EHA Kano",
            submitted_by_email="eha@example.invalid",
            match_state=SolicitationResponse.MATCH_HUMAN,
        )
        ingest_round(round_, self.LATER)
        assert round_.responses.get(source_row=2).llo_entity is None


@pytest.mark.django_db
class TestHumanVerdictsCloseTheLoop:
    """Without a way to record a verdict the review queue is decorative: the
    same submissions sit in it after every future import."""

    def test_a_verdict_attributes_a_submission_labs_refused_to_guess(self, round_):
        org = LabsOrg.objects.create(slug="unknown-body", name="Unknown Body")
        ingest_round(round_, RESPONSES, human_verdicts={"demo-2026:3": (org.name, "link", "trading name")})
        resolved = round_.responses.get(source_row=3)
        assert resolved.llo_entity == org
        assert resolved.match_state == SolicitationResponse.MATCH_HUMAN
        assert resolved.match_basis == "trading name"

    def test_a_dismissal_removes_it_from_the_queue_without_attributing_it(self, round_):
        ingest_round(round_, RESPONSES, human_verdicts={"demo-2026:3": (None, "not_an_llo", "an individual")})
        dismissed = round_.responses.get(source_row=3)
        assert dismissed.llo_entity is None
        assert dismissed.match_state == SolicitationResponse.MATCH_NOT_LLO
        assert not SolicitationResponse.objects.filter(
            match_state=SolicitationResponse.MATCH_UNMATCHED, source_row=3
        ).exists()

    def test_a_verdict_survives_re_import(self, round_):
        """The importer runs daily. A verdict that had to be re-entered every
        morning would not be a verdict."""
        org = LabsOrg.objects.create(slug="unknown-body", name="Unknown Body")
        verdicts = {"demo-2026:3": (org.name, "link", "trading name")}
        ingest_round(round_, RESPONSES, human_verdicts=verdicts)
        ingest_round(round_, RESPONSES, human_verdicts=verdicts)
        assert round_.responses.get(source_row=3).llo_entity == org

    def test_dismissals_are_counted_apart_from_outstanding_work(self, round_):
        """Row 2 has nothing to match against in this fixture and is genuinely
        outstanding; row 3 is decided. The counts must tell them apart, or a
        queue that is being worked through looks like one that is not."""
        stats = ingest_round(round_, RESPONSES, human_verdicts={"demo-2026:3": (None, "not_an_llo", "an individual")})
        assert stats["dismissed"] == 1
        assert stats["unmatched"] == 1
        assert stats["submissions"] == 2


@pytest.mark.django_db
class TestAccessWriteBack:
    """Labs writes the access and next-step columns because a hand-maintained
    one goes stale the moment a sheet is moved — and it writes ONLY those.

    The rest of the tab belongs to people. This is the guard on that: it caught
    the next-step column the first time it was added, which is what it is for.
    """

    # Every cell labs is allowed to touch on the rounds tab.
    OWNED = ("!P2:Q2", "!T2")

    def test_writes_the_verified_state_against_the_round_s_own_row(self, round_):
        from connect_labs.marketplace import directory

        written = []
        directory.update_cells = lambda sid, updates: written.extend(updates)  # noqa: E731

        rounds, _ = parse_rounds(ROUND_ROWS)
        check_access(lambda sid, tab: [["Timestamp"]], write_back_to=rounds, spreadsheet_id="sheet-id")

        by_range = {rng.split("!")[1]: values for rng, values in written}
        assert set(by_range) == {"P2:Q2", "T2"}
        state, stamp = by_range["P2:Q2"][0]
        assert "OK" in state
        assert stamp

    def test_touches_no_other_column(self, round_):
        from connect_labs.marketplace import directory

        written = []
        directory.update_cells = lambda sid, updates: written.extend(updates)  # noqa: E731
        rounds, _ = parse_rounds(ROUND_ROWS)
        check_access(lambda sid, tab: [["Timestamp"]], write_back_to=rounds, spreadsheet_id="sheet-id")

        for rng, _values in written:
            assert any(rng.endswith(owned) for owned in self.OWNED), f"labs wrote outside its own columns: {rng}"

    def test_a_round_whose_row_is_unknown_is_skipped_not_guessed(self, round_):
        from connect_labs.marketplace import directory

        written = []
        directory.update_cells = lambda sid, updates: written.extend(updates)  # noqa: E731
        rounds, _ = parse_rounds(ROUND_ROWS)
        for r in rounds:
            r.source_row = None
        check_access(lambda sid, tab: [["Timestamp"]], write_back_to=rounds, spreadsheet_id="sheet-id")
        assert written == []


@pytest.mark.django_db
class TestTheNextStepColumnSaysWhoseTurnItIs:
    """A status column says what is true. It does not say what to do about it,
    and somebody working down this tab should not have to cross-reference three
    other columns to find out whose turn it is.
    """

    def _round(self, **kwargs):
        defaults = {
            "slug": "r1",
            "title": "R1",
            "response_spreadsheet_id": "sheet1",
            "sa_access_state": ACCESS_OK,
            "delivery_type": "kmc",
            "last_ingested_at": timezone.now(),
        }
        return Solicitation.objects.create(**(defaults | kwargs))

    def test_no_response_sheet_asks_for_the_link(self):
        step = eoi.next_step_for(self._round(sa_access_state=ACCESS_MISSING, response_spreadsheet_id=""))
        assert step.startswith("YOU:")
        assert "Response Sheet Link" in step

    def test_no_access_names_the_account_to_share_with(self):
        """The whole blocker is that nobody knows which address to add."""
        step = eoi.next_step_for(self._round(sa_access_state=ACCESS_DENIED))
        assert "connect-labs-sa@connect-labs.iam.gserviceaccount.com" in step

    def test_a_readable_but_untagged_round_asks_for_the_programme(self):
        step = eoi.next_step_for(self._round(delivery_type=""))
        assert "Connect Programme" in step

    def test_access_is_asked_for_before_the_tag(self):
        """Both are outstanding, but one blocks ingest and the other does not.
        Listing two actions in one cell makes neither get done."""
        step = eoi.next_step_for(self._round(sa_access_state=ACCESS_DENIED, delivery_type=""))
        assert "share the response sheet" in step
        assert "Connect Programme" not in step

    def test_a_round_that_is_done_says_nothing(self):
        """An empty cell is the signal that there is nothing to do. A permanent
        "complete" note would make the column noise to scan past."""
        assert eoi.next_step_for(self._round()) == ""

    def test_a_readable_tagged_round_not_yet_ingested_is_labs_turn(self):
        step = eoi.next_step_for(self._round(last_ingested_at=None))
        assert step.startswith("LABS:")

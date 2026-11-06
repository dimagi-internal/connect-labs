"""Importing rounds and their submissions. All data invented."""

import pytest
from django.utils import timezone

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace import eoi
from connect_labs.marketplace.directory import parse_rounds
from connect_labs.marketplace.eoi import check_access, ingest_round, upsert_rounds
from connect_labs.marketplace.models import OrgContact
from connect_labs.marketplace.testing import SHEET_ROUNDS_HEADER
from connect_labs.solicitations.local_models import (
    ACCESS_DENIED,
    ACCESS_MISSING,
    ACCESS_OK,
    Solicitation,
    SolicitationResponse,
)

HEADER = SHEET_ROUNDS_HEADER
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

    def test_every_round_is_owned_by_dimagi(self, round_):
        assert round_.owner_org.slug == "dimagi"

    def test_the_forms_public_page_gives_the_questions_their_choices(self, round_):
        form = {
            "items": [
                {
                    "title": "Organization name",
                    "questionItem": {"question": {"required": True, "textQuestion": {}}},
                }
            ],
        }
        stats = ingest_round(round_, RESPONSES, read_form=lambda url: form)
        assert stats["structure"] == {round_.directory_forms[0]["key"]: 1}
        round_.refresh_from_db()
        assert round_.questions[1]["type"] == "text" and round_.questions[1]["required"] is True

    def test_an_unreadable_form_leaves_text_boxes_and_says_why(self, round_):
        def closed(url):
            raise ValueError("the page carries no form")

        stats = ingest_round(round_, RESPONSES, read_form=closed)
        assert list(stats["structure"].values()) == ["form not read: the page carries no form"]
        round_.refresh_from_db()
        assert "type" not in round_.questions[1]

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
            source_form="demo-2026",  # what migration 0025 gives an imported row
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
    OWNED = ("!P2", "!Q2", "!T2")

    def test_writes_the_verified_state_against_the_round_s_own_row(self, round_):
        from connect_labs.marketplace import directory

        written = []
        directory.update_cells = lambda sid, updates: written.extend(updates)  # noqa: E731

        rounds, _ = parse_rounds(ROUND_ROWS)
        check_access(lambda sid, tab: [["Timestamp"]], write_back_to=rounds, spreadsheet_id="sheet-id")

        by_range = {rng.split("!")[1]: values for rng, values in written}
        assert set(by_range) == {"P2", "Q2", "T2"}
        assert "OK" in by_range["P2"][0][0]
        assert by_range["Q2"][0][0]

    def test_touches_no_other_column(self, round_):
        from connect_labs.marketplace import directory

        written = []
        directory.update_cells = lambda sid, updates: written.extend(updates)  # noqa: E731
        rounds, _ = parse_rounds(ROUND_ROWS)
        check_access(lambda sid, tab: [["Timestamp"]], write_back_to=rounds, spreadsheet_id="sheet-id")

        for rng, _values in written:
            assert any(rng.endswith(owned) for owned in self.OWNED), f"labs wrote outside its own columns: {rng}"

    def test_a_column_inserted_before_labs_columns_moves_the_writes_with_it(self, round_):
        """Someone adds a column in front of "Labs Access". Writing to fixed
        letters would overwrite the column a person now keeps there."""
        from connect_labs.marketplace import directory

        written = []
        directory.update_cells = lambda sid, updates: written.extend(updates)  # noqa: E731
        header = HEADER[:15] + ["Owner"] + HEADER[15:]
        row = ROUND_ROWS[1][:15] + ["a person's note"] + ROUND_ROWS[1][15:]
        rounds, _ = parse_rounds([header, row])
        check_access(lambda sid, tab: [["Timestamp"]], write_back_to=rounds, spreadsheet_id="sheet-id")

        assert {rng.split("!")[1] for rng, _ in written} == {"Q2", "R2", "U2"}

    def test_a_labs_column_that_is_missing_is_not_written_anywhere(self, round_):
        from connect_labs.marketplace import directory

        written = []
        directory.update_cells = lambda sid, updates: written.extend(updates)  # noqa: E731
        header = [h for h in HEADER if not h.startswith("Next Step")]
        rounds, _ = parse_rounds([header, ROUND_ROWS[1]])
        check_access(lambda sid, tab: [["Timestamp"]], write_back_to=rounds, spreadsheet_id="sheet-id")

        assert {rng.split("!")[1] for rng, _ in written} == {"P2", "Q2"}

    def test_a_round_whose_row_is_unknown_is_skipped_not_guessed(self, round_):
        from connect_labs.marketplace import directory

        written = []
        directory.update_cells = lambda sid, updates: written.extend(updates)  # noqa: E731
        rounds, _ = parse_rounds(ROUND_ROWS)
        for r in rounds:
            r.source_row = None
            for form in r.forms:
                form.source_row = None
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

    def test_a_readable_but_untagged_round_asks_for_the_program(self):
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


@pytest.mark.django_db
def test_a_round_slug_owned_by_a_call_is_reported_not_imported():
    from connect_labs.solicitations.local_models import SOURCE_LABS

    call = Solicitation.objects.create(slug="demo-2026", title="An owner's call", source=SOURCE_LABS)
    rows = ROUND_ROWS + [[("other-2026" if i == 0 else c) for i, c in enumerate(ROUND_ROWS[1])]]
    rounds, _ = parse_rounds(rows)
    stats = upsert_rounds(rounds)
    assert stats["rounds"] == 1
    assert stats["collisions"] == ["demo-2026"]
    call.refresh_from_db()
    assert call.title == "An owner's call" and call.source == SOURCE_LABS
    assert Solicitation.objects.directory_rounds().filter(slug="other-2026").exists()


def _grouped_rows():
    header = HEADER + ["Round"]
    base = ROUND_ROWS[1]

    def form(slug, name, sheet):
        row = list(base) + [""] * (len(header) - len(base))
        row[0], row[1], row[12] = slug, name, f"https://docs.google.com/spreadsheets/d/{sheet}"
        row[header.index("Round")] = "chp-2026"
        return row

    return [
        header,
        form("chp-2026-en", "CHP RFI — English", "sheetEN0123456789abcdefghij"),
        form("chp-2026-fr", "CHP RFI — French", "sheetFR0123456789abcdefghij"),
    ]


FR_RESPONSES = [
    ["Horodateur", "Nom de l'organisation", "Courriel du contact", "Pays"],
    ["3/6/2026 09:00:00", "Fenwick Trust", "a@example.invalid", "Sénégal"],
]


@pytest.mark.django_db
class TestARoundWithSeveralForms:
    @pytest.fixture
    def grouped(self, db):
        rounds, _ = parse_rounds(_grouped_rows())
        upsert_rounds(rounds)
        return Solicitation.objects.get(slug="chp-2026"), rounds

    def test_one_round_records_both_forms(self, grouped):
        round_, _ = grouped
        assert Solicitation.objects.directory_rounds().count() == 1
        assert [f["key"] for f in round_.directory_forms] == ["chp-2026-en", "chp-2026-fr"]

    def test_both_forms_ingest_into_the_one_round_with_row_2_kept_apart(self, grouped):
        round_, _ = grouped
        stats = ingest_round(round_, {"chp-2026-en": RESPONSES, "chp-2026-fr": FR_RESPONSES})
        assert stats["submissions"] == 3
        assert set(round_.responses.filter(source_row=2).values_list("source_form", flat=True)) == {
            "chp-2026-en",
            "chp-2026-fr",
        }
        fr = round_.responses.get(source_form="chp-2026-fr")
        assert fr.org_name == "Fenwick Trust"
        assert "sheetFR0123456789abcdefghij" in fr.source_url

    def test_the_round_holds_every_forms_questions(self, grouped):
        round_, _ = grouped
        ingest_round(round_, {"chp-2026-en": RESPONSES, "chp-2026-fr": FR_RESPONSES})
        round_.refresh_from_db()
        texts = [q["text"] for q in round_.questions]
        assert "Organization name" in texts and "Nom de l'organisation" in texts

    def test_each_question_records_the_forms_that_asked_it(self, grouped):
        round_, _ = grouped
        ingest_round(round_, {"chp-2026-en": RESPONSES, "chp-2026-fr": FR_RESPONSES})
        round_.refresh_from_db()
        forms = {q["text"]: q["forms"] for q in round_.questions}
        assert forms["Organization name"] == ["chp-2026-en"]
        assert forms["Nom de l'organisation"] == ["chp-2026-fr"]

    def test_each_question_records_whether_it_was_answered_at_length(self, grouped):
        round_, _ = grouped
        long_fr = [FR_RESPONSES[0], FR_RESPONSES[1][:3] + ["Sénégal, " + "x" * 120]]
        ingest_round(round_, {"chp-2026-en": RESPONSES, "chp-2026-fr": long_fr})
        round_.refresh_from_db()
        long = {q["text"]: q["long"] for q in round_.questions}
        assert long["Pays"] is True
        assert long["Nom de l'organisation"] is False

    def test_each_form_is_checked_and_the_round_takes_the_worst(self, grouped):
        round_, _ = grouped

        def read(sid, tab):
            if sid == "sheetFR0123456789abcdefghij":
                raise PermissionError("403")
            return [["Timestamp"]]

        results = check_access(read)
        assert {(r["form"], r["state"]) for r in results} == {
            ("chp-2026-en", ACCESS_OK),
            ("chp-2026-fr", ACCESS_DENIED),
        }
        round_.refresh_from_db()
        assert round_.sa_access_state == ACCESS_DENIED
        assert {f["key"]: f["sa_access_state"] for f in round_.forms} == {
            "chp-2026-en": ACCESS_OK,
            "chp-2026-fr": ACCESS_DENIED,
        }

    def test_each_form_s_own_row_gets_its_own_verdict(self, grouped):
        from connect_labs.marketplace import directory

        _, rounds = grouped
        written = []
        directory.update_cells = lambda sid, updates: written.extend(updates)  # noqa: E731

        def read(sid, tab):
            if sid == "sheetFR0123456789abcdefghij":
                raise PermissionError("403")
            return [["Timestamp"]]

        check_access(read, write_back_to=rounds, spreadsheet_id="sheet-id")
        by_range = {rng.split("!")[1]: values[0][0] for rng, values in written}
        assert "OK" in by_range["P2"]
        assert "NO ACCESS" in by_range["P3"]
        assert "share the response sheet" in by_range["T3"]

    def test_a_reimport_keeps_each_forms_verified_access(self, grouped):
        round_, _ = grouped
        check_access(lambda sid, tab: [["Timestamp"]])
        rounds, _ = parse_rounds(_grouped_rows())
        upsert_rounds(rounds)
        round_.refresh_from_db()
        assert {f["sa_access_state"] for f in round_.forms} == {ACCESS_OK}

    def test_a_form_imported_earlier_as_its_own_round_is_folded_in(self, db):
        # Yesterday each form was a round of its own.
        header, en, fr = _grouped_rows()
        alone = [header] + [r[:-1] + [""] for r in (en, fr)]
        rounds, _ = parse_rounds(alone)
        upsert_rounds(rounds)
        ingest_round(Solicitation.objects.get(slug="chp-2026-fr"), FR_RESPONSES)

        rounds, _ = parse_rounds(_grouped_rows())
        stats = upsert_rounds(rounds)

        assert sorted(stats["folded"]) == ["chp-2026-en", "chp-2026-fr"]
        assert list(Solicitation.objects.directory_rounds().values_list("slug", flat=True)) == ["chp-2026"]
        moved = Solicitation.objects.get(slug="chp-2026").responses.get()
        assert moved.source_form == "chp-2026-fr"

    def test_a_form_round_with_criteria_of_its_own_is_not_merged_by_machine(self, db):
        header, en, fr = _grouped_rows()
        rounds, _ = parse_rounds([header] + [r[:-1] + [""] for r in (en, fr)])
        upsert_rounds(rounds)
        Solicitation.objects.filter(slug="chp-2026-fr").update(evaluation_criteria=[{"id": "c1"}])

        rounds, _ = parse_rounds(_grouped_rows())
        stats = upsert_rounds(rounds)
        assert stats["fold_refused"] == ["chp-2026-fr"]
        assert Solicitation.objects.filter(slug="chp-2026-fr").exists()

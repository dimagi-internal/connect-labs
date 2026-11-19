"""``run_import`` -- the one importer behind the command, the beat and the MCP tool.

The sheet is faked at ``directory.read_tab`` / ``write_tab``, so none of this needs a
service account. All data is invented.
"""

import pytest
from django.core.cache import cache
from django.core.management import call_command

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace import directory
from connect_labs.marketplace.importer import LOCK_KEY, ImportFailed, run_import
from connect_labs.marketplace.models import OrgContact, OrgProfile
from connect_labs.marketplace.tests.test_eoi import RESPONSES, ROUND_ROWS
from connect_labs.marketplace.tests.test_import import CONTACTS, DATES, MAPPING, ORGS
from connect_labs.solicitations.local_models import Solicitation, SolicitationResponse

pytestmark = pytest.mark.django_db

RESPONSE_SHEET = "1abcDEFghiJKLmnoPQRstuVWxyz0123456789"


@pytest.fixture(autouse=True)
def _clean_lock():
    cache.delete(LOCK_KEY)
    yield
    cache.delete(LOCK_KEY)


@pytest.fixture
def sheet(monkeypatch):
    """A directory sheet that can be read, and records what is written back to it."""
    tabs = {
        directory.ORGANIZATIONS_TAB: ORGS,
        directory.CONTACTS_TAB: CONTACTS,
        directory.DATES_TAB: DATES,
        directory.MAPPING_TAB: MAPPING,
        directory.ROUNDS_TAB: ROUND_ROWS,
        directory.RESPONSE_MAPPING_TAB: [],
    }
    written = {}

    def read_tab(sheet_id, tab):
        if sheet_id == RESPONSE_SHEET:
            return RESPONSES
        return tabs[tab]

    monkeypatch.setattr(directory, "read_tab", read_tab)
    monkeypatch.setattr(directory, "write_tab", lambda sid, tab, rows: written.update({tab: rows}))
    monkeypatch.setattr("connect_labs.marketplace.eoi._write_access_columns", lambda *a, **k: None)
    monkeypatch.setattr("connect_labs.marketplace.eoi.fetch_form_structure", lambda *a, **k: {}, raising=False)
    return {"tabs": tabs, "written": written}


class TestRunImport:
    def test_it_imports_the_directory_and_says_what_it_did(self, sheet):
        result = run_import()

        assert result["dry_run"] is False
        assert (result["organisations"], result["contacts"], result["attributions"]) == (2, 2, 1)
        assert LabsOrg.objects.count() == 2
        assert "rounds" not in result  # eoi was not asked for
        assert result["findings"]["published"] is True
        assert directory.FINDINGS_TAB in sheet["written"]

    def test_with_eoi_it_ingests_the_rounds_and_reports_who_is_still_unmatched(self, sheet):
        result = run_import(eoi_rounds=True)

        assert result["rounds"]["rounds"] == 1
        assert result["rounds"]["forms_readable"] == 1
        assert result["rounds"]["totals"] == {"submissions": 2, "matched": 1, "unmatched": 1}
        [round_] = result["rounds"]["per_round"]
        assert {k: round_[k] for k in ("slug", "submissions", "matched", "unmatched")} == {
            "slug": "demo-2026",
            "submissions": 2,
            "matched": 1,
            "unmatched": 1,
        }
        assert SolicitationResponse.objects.filter(solicitation__slug="demo-2026").count() == 2

    def test_a_second_run_changes_nothing(self, sheet):
        run_import(eoi_rounds=True)
        run_import(eoi_rounds=True)
        assert OrgProfile.objects.count() == 2  # the directory's orgs; a round also mints its owner, Dimagi
        assert Solicitation.objects.directory_rounds().count() == 1
        assert SolicitationResponse.objects.count() == 2

    def test_an_organisation_added_to_the_sheet_appears_on_the_next_run(self, sheet):
        """The reason this exists: add a row to the directory, run it, find the org."""
        run_import()
        sheet["tabs"][directory.ORGANIZATIONS_TAB] = ORGS + [
            ["New Body", "", "", "2020", "", "", "Uganda", "", "", "", "", "", "", "", "", ""]
        ]
        result = run_import()
        assert result["organisations"] == 3
        assert LabsOrg.objects.filter(name="New Body").exists()

    def test_a_dry_run_reads_and_reports_but_writes_nothing_and_takes_no_lock(self, sheet):
        cache.add(LOCK_KEY, "held", 60)  # a real import is running: a dry run is still allowed

        result = run_import(dry_run=True)

        assert result["dry_run"] is True
        assert result["organisations"] == 2
        assert LabsOrg.objects.count() == 0
        assert OrgProfile.objects.count() == 0
        assert sheet["written"] == {}

    def test_it_never_prunes_unless_asked(self, sheet):
        run_import()
        sheet["tabs"][directory.ORGANIZATIONS_TAB] = ORGS[:2]  # Fenwick Trust leaves the sheet
        run_import()
        assert LabsOrg.objects.filter(name="Fenwick Trust").exists()


class TestFailures:
    def test_an_unreadable_sheet_is_a_named_failure(self, monkeypatch):
        def denied(sheet_id, tab):
            raise PermissionError("403 no access")

        monkeypatch.setattr(directory, "read_tab", denied)
        with pytest.raises(ImportFailed) as exc:
            run_import()
        assert exc.value.code == "DIRECTORY_UNREADABLE"
        assert "403 no access" in str(exc.value)

    def test_an_empty_sheet_is_refused_rather_than_read_as_an_empty_registry(self, sheet):
        run_import()
        sheet["tabs"][directory.ORGANIZATIONS_TAB] = [ORGS[0]]
        with pytest.raises(ImportFailed) as exc:
            run_import()
        assert exc.value.code == "DIRECTORY_EMPTY"
        assert LabsOrg.objects.count() == 2  # nothing was deleted

    def test_a_second_import_while_one_runs_is_refused(self, sheet):
        cache.add(LOCK_KEY, "held", 60)
        with pytest.raises(ImportFailed) as exc:
            run_import()
        assert exc.value.code == "IMPORT_IN_PROGRESS"

    def test_the_lock_is_released_after_a_failure(self, sheet):
        sheet["tabs"][directory.ORGANIZATIONS_TAB] = [ORGS[0]]
        with pytest.raises(ImportFailed):
            run_import()
        assert cache.get(LOCK_KEY) is None
        sheet["tabs"][directory.ORGANIZATIONS_TAB] = ORGS
        assert run_import()["organisations"] == 2


class TestTheCommandIsTheSameImport:
    def test_it_prints_the_summary(self, sheet, capsys):
        call_command("marketplace_import")
        assert "imported 2 organisations, 2 contacts, 1 slug attributions" in capsys.readouterr().out

    def test_it_turns_a_failure_into_a_command_error(self, sheet):
        from django.core.management.base import CommandError

        cache.add(LOCK_KEY, "held", 60)
        with pytest.raises(CommandError, match="Another marketplace import is running"):
            call_command("marketplace_import")


class TestNamesThatDifferOnlyInSpelling:
    """Labs joined contacts, dates and verdicts to organisations by exact name, so "Society for Family
    Health" on one tab and "Society For Family Health" on another silently fell off (about 70
    contacts on 2026-11-19). Case, accents and spacing are folded; anything else is left alone."""

    def test_a_contact_whose_name_differs_only_in_case_still_joins_its_organisation(self, sheet):
        sheet["tabs"][directory.CONTACTS_TAB] = [
            CONTACTS[0],
            ["A Person", "harbourside HEALTH  initiative", "Director", "Yes", "a@example.invalid", "", ""],
        ]
        result = run_import()

        assert OrgContact.objects.get(email="a@example.invalid").org.name == "Harbourside Health Initiative"
        assert result["skipped"] == []
        assert result["names_aligned"]["count"] == 1
        assert (
            "'harbourside HEALTH  initiative' → 'Harbourside Health Initiative'"
            in result["names_aligned"]["examples"][0]
        )

    def test_a_date_whose_name_differs_only_in_case_still_dates_the_organisation(self, sheet):
        sheet["tabs"][directory.DATES_TAB] = [
            DATES[0],
            ["HARBOURSIDE health initiative", "2025-03-04", "EOI submission"],
        ]
        run_import()
        org = LabsOrg.objects.get(name="Harbourside Health Initiative")
        assert org.marketplace_profile.joined_at.isoformat() == "2025-03-04"

    def test_a_name_that_is_not_the_same_organisation_is_not_joined(self, sheet):
        sheet["tabs"][directory.CONTACTS_TAB] = [
            CONTACTS[0],
            ["A Person", "Harbourside Health Initiative Kenya", "Director", "Yes", "a@example.invalid", "", ""],
        ]
        result = run_import()
        assert not OrgContact.objects.filter(email="a@example.invalid").exists()
        assert any("Harbourside Health Initiative Kenya" in line for line in result["skipped"])
        assert result["names_aligned"]["count"] == 0

    def test_two_organisations_that_fold_alike_are_left_for_a_person(self, sheet):
        sheet["tabs"][directory.ORGANIZATIONS_TAB] = ORGS + [
            ORGS[1][:0] + ["HARBOURSIDE HEALTH INITIATIVE"] + ORGS[1][1:]
        ]
        sheet["tabs"][directory.CONTACTS_TAB] = [
            CONTACTS[0],
            ["A Person", "harbourside health initiative", "Director", "Yes", "a@example.invalid", "", ""],
        ]
        result = run_import()
        assert not OrgContact.objects.filter(email="a@example.invalid").exists()
        assert result["names_aligned"]["count"] == 0


class TestSelectingRounds:
    def test_open_only_skips_closed_rounds_and_reads_none_of_their_sheets(self, sheet):
        result = run_import(eoi_rounds=True, open_only=True)  # the fixture round closed on 1 April 2026

        assert result["rounds"]["selected"] == []
        assert result["rounds"]["per_round"] == []
        assert result["rounds"]["forms_total"] == 0
        assert SolicitationResponse.objects.count() == 0
        assert result["rounds"]["rounds"] == 1  # the rounds tab itself is still imported

    def test_a_named_round_is_ingested_and_the_rest_are_not(self, sheet):
        result = run_import(eoi_rounds=True, rounds=["demo-2026"])
        assert result["rounds"]["selected"] == ["demo-2026"]
        assert [p["slug"] for p in result["rounds"]["per_round"]] == ["demo-2026"]

    def test_an_unknown_round_is_refused_by_name(self, sheet):
        with pytest.raises(ImportFailed) as exc:
            run_import(eoi_rounds=True, rounds=["nope-2026"])
        assert exc.value.code == "UNKNOWN_ROUND"
        assert "nope-2026" in str(exc.value)

    def test_the_default_is_every_round(self, sheet):
        assert run_import(eoi_rounds=True)["rounds"]["selected"] == "all"


class TestWhoStillNeedsAnOrganisation:
    def test_the_result_lists_each_unmatched_submission_with_its_form_and_sheet_row(self, sheet):
        result = run_import(eoi_rounds=True)

        [round_] = result["rounds"]["per_round"]
        [waiting] = round_["awaiting_verdict"]
        assert waiting["org_name"] == "Unknown Body"
        assert waiting["source_row"] == 3
        assert waiting["form"] == "demo-2026"
        assert waiting["country"] == "Kenya"
        assert set(waiting) == {"response_id", "form", "source_row", "org_name", "submitted_at", "country", "website"}

    def test_nothing_about_a_person_is_in_it(self, sheet):
        [round_] = run_import(eoi_rounds=True)["rounds"]["per_round"]
        assert "z@example.invalid" not in str(round_["awaiting_verdict"])


class TestTheCommandTakesTheSameSelection:
    def test_round_flag(self, sheet, capsys):
        call_command("marketplace_import", "--eoi", "--round", "demo-2026")
        assert "demo-2026: 2 submissions" in capsys.readouterr().out

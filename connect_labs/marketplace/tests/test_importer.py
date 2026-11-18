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
from connect_labs.marketplace.models import OrgProfile
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
        assert result["rounds"]["per_round"] == [{"slug": "demo-2026", "submissions": 2, "matched": 1, "unmatched": 1}]
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

"""The provenance backfill marks only folders the generator named, and only with --apply."""

import io

import pytest
from django.core.management import call_command

from connect_labs.labs.management.commands import synthetic_mark_generated as command
from connect_labs.labs.synthetic.dump import dump_generator
from connect_labs.labs.synthetic.generator.io.uploader import GENERATED_FOLDER_NAME_RE, generated_folder_name
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.labs.synthetic.provenance import is_generated, mark_generated

pytestmark = pytest.mark.django_db


class _FakeDrive:
    def __init__(self, names):
        self.names = names

    def get_name(self, folder_id):
        if folder_id not in self.names:
            raise RuntimeError("404")
        return self.names[folder_id]


def _row(opportunity_id, folder, labs_only=True):
    return SyntheticOpportunity.objects.create(
        opportunity_id=opportunity_id, gdrive_folder_id=folder, labs_only=labs_only, enabled=True
    )


@pytest.fixture
def world(monkeypatch):
    _row(10_001, "gen")
    _row(10_002, "dump")
    _row(10_003, "hand")
    _row(10_004, "gone")
    _row(10_005, "")  # labs-local records, no folder: out of scope
    _row(501, "gen-real", labs_only=False)  # a real-backed opp is never generated
    drive = _FakeDrive(
        {
            "gen": "opp-10001-20260601-101500-generated",
            "dump": "opp-874-20260601-101500",
            "hand": "my demo data",
            "gen-real": "opp-501-20260601-101500-generated",
        }
    )
    monkeypatch.setattr(command, "_drive", lambda: drive)
    return drive


def _run(*args):
    out = io.StringIO()
    call_command("synthetic_mark_generated", *args, stdout=out)
    return out.getvalue()


def _generated():
    return {row.opportunity_id for row in SyntheticOpportunity.objects.all() if is_generated(row)}


def test_dry_run_reports_and_writes_nothing(world):
    out = _run()
    assert "WOULD MARK  opp 10001" in out
    assert "SKIP  opp 10002" in out and "SKIP  opp 10003" in out
    assert "ERROR  opp 10004" in out
    assert "opp 10005" not in out and "opp 501 " not in out
    assert _generated() == set()


def test_apply_marks_only_generator_named_folders(world):
    out = _run("--apply")
    assert "MARKED  opp 10001" in out
    assert _generated() == {10_001}


def test_a_marked_row_now_serving_a_dump_stays_unmarked(world):
    mark_generated(10_002, "something-else")
    _run("--apply")
    assert SyntheticOpportunity.objects.get(opportunity_id=10_002).generated_folder_id == "something-else"
    assert 10_002 not in _generated()


def test_a_row_repointed_at_a_generator_folder_is_marked(world):
    """The local flow: generate on a laptop, then repoint. Repointing never marks."""
    mark_generated(10_001, "the-previous-generated-folder")
    assert 10_001 not in _generated()
    _run("--apply")
    assert 10_001 in _generated()


def test_a_row_already_generated_is_not_rechecked(world):
    mark_generated(10_001, "gen")
    out = _run()
    assert "opp 10001" not in out


def test_the_pattern_matches_what_the_generator_names_and_not_what_a_dump_names(monkeypatch, settings):
    assert GENERATED_FOLDER_NAME_RE.match(generated_folder_name(10_001))

    created = []

    class _Drive:
        def create_folder(self, name, parent_id):
            created.append(name)
            return "f"

        def upload_file(self, *a, **k):
            pass

    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    monkeypatch.setattr("connect_labs.labs.synthetic.dump.DriveClient", lambda: _Drive())
    monkeypatch.setattr("connect_labs.labs.synthetic.dump._fetch_endpoint", lambda *a, **k: [])
    list(dump_generator(874, "tok"))
    assert created and not GENERATED_FOLDER_NAME_RE.match(created[0])

"""Tests for the gdrive pipeline data source (backends/sql/gdrive_fetcher.py).

Drive itself is faked: these pin the parsing, the folder/pattern selection, and —
the part that is a security boundary — the authorization rules: only Dimagi staff
can authorize a Drive target, an authorization is bound to its opportunity and to
the exact ids it was stamped for, and an unauthorized source is refused before
any Drive call is made.
"""

import json
from types import SimpleNamespace

import pytest

from connect_labs.labs.analysis.backends.sql import gdrive_fetcher as gf
from connect_labs.labs.analysis.config import AnalysisPipelineConfig, DataSourceConfig
from connect_labs.labs.analysis.utils import get_config_hash

STAFF = SimpleNamespace(email="analyst@dimagi.com", username="analyst")
PARTNER = SimpleNamespace(email="someone@partner.org", username="someone")
OPP = 1251


class FakeDrive:
    def __init__(self, files):
        # files: {id: {"name", "mimeType", "content" (bytes), "parent"}}
        self.files = files
        self.calls = []

    # Folder tree: ROOT (allowed) > folderA, looseFolder; synthRoot (NOT allowed) > internalFixtures.
    FOLDER_PARENTS = {"folderA": ["ROOT"], "looseFolder": ["ROOT"], "ROOT": [], "internalFixtures": ["synthRoot"],
                      "synthRoot": []}

    def get_parents(self, file_id):
        self.calls.append(("parents", file_id))
        if file_id in self.FOLDER_PARENTS:
            return self.FOLDER_PARENTS[file_id]
        return [self.files.get(file_id, {}).get("parent", "looseFolder")]

    def get_metadata(self, file_id):
        self.calls.append(("meta", file_id))
        f = self.files[file_id]
        return {"id": file_id, "name": f["name"], "mimeType": f["mimeType"], "size": str(len(f["content"]))}

    def list_folder_files(self, folder_id):
        self.calls.append(("list", folder_id))
        return [
            {"id": i, "name": f["name"], "mimeType": f["mimeType"], "size": str(len(f["content"])), "modifiedTime": "t"}
            for i, f in self.files.items()
            if f.get("parent") == folder_id
        ]

    def download_file(self, file_id):
        self.calls.append(("download", file_id))
        return self.files[file_id]["content"]

    def export_file(self, file_id, mime_type):
        self.calls.append(("export", file_id, mime_type))
        return self.files[file_id]["content"]


@pytest.fixture
def drive(monkeypatch, allowed_root):
    fake = FakeDrive(
        {
            "fileCSV": {"name": "answers_scored_bednets.csv", "mimeType": "text/csv", "parent": "folderA",
                        "content": "session_id,state,quality,day\ns1,Kebbi,4,2026-08-03\ns2,NA,,2026-08-04\n".encode()},
            "fileCSV2": {"name": "answers_scored_malaria.csv", "mimeType": "text/csv", "parent": "folderA",
                         "content": b"session_id,state,quality,day\ns9,Borno,5,2026-07-01\n"},
            "fileOther": {"name": "gems_bednets.csv", "mimeType": "text/csv", "parent": "folderA",
                          "content": b"quote\nhello\n"},
            "sheet": {"name": "Tracker", "mimeType": gf.GOOGLE_SHEET, "content": b"a,b\n1,2\n"},
            "json": {"name": "rows.json", "mimeType": "application/json",
                     "content": json.dumps({"rows": [{"a": 1, "tags": ["x"]}, {"a": None, "tags": {"k": 1}}]}).encode()},
            "doc": {"name": "README", "mimeType": "application/vnd.google-apps.document", "parent": "folderA",
                    "content": b""},
            "pdf": {"name": "answers_scored_notes.pdf", "mimeType": "application/pdf", "parent": "folderA",
                    "content": b"%PDF"},
            "baddate": {"name": "d.csv", "mimeType": "text/csv", "content": b"day\n2025-02-30\n2025-03-01T10:00:00Z\n"},
        }
    )
    monkeypatch.setattr(gf, "_drive", lambda: fake)
    monkeypatch.setattr(gf, "_caller_opportunity_ids", lambda request, token: {OPP})
    return fake


@pytest.fixture(autouse=True)
def allowed_root(settings, monkeypatch):
    """ROOT is the allowed tree. Stamping checks containment too, so tests that stamp
    without the `drive` fixture get an empty fake whose unknown ids sit under ROOT."""
    settings.LABS_WORKFLOW_GDRIVE_ROOT_IDS = ["ROOT"]
    monkeypatch.setattr(gf, "_drive", lambda: FakeDrive({}))


def _source(**kw):
    """An authorized DataSourceConfig for OPP built from schema-dict kwargs."""
    stamped = gf.authorize_gdrive_source({"type": "gdrive", **kw}, OPP, STAFF)
    return DataSourceConfig(**stamped)


# --------------------------------------------------------------------------- reading


def test_reads_one_csv_with_row_and_file_paths(drive):
    rows = gf.fetch_gdrive_rows_as_visit_dicts(
        _source(file_id="fileCSV", username_column="session_id", date_column="day"), OPP
    )
    assert [r["username"] for r in rows] == ["s1", "s2"]
    assert rows[0]["visit_date"] == "2026-08-03"
    assert rows[0]["form_json"]["row"] == {"session_id": "s1", "state": "Kebbi", "quality": "4", "day": "2026-08-03"}
    assert rows[0]["form_json"]["file"]["name"] == "answers_scored_bednets.csv"
    assert rows[1]["form_json"]["row"]["quality"] is None  # "" is null by default
    assert rows[1]["form_json"]["row"]["state"] == "NA"  # ...but "NA" is not, unless configured
    assert len({r["id"] for r in rows}) == 2


def test_null_values_are_configurable(drive):
    rows = gf.fetch_gdrive_rows_as_visit_dicts(_source(file_id="fileCSV", null_values=["", "NA"]), OPP)
    assert rows[1]["form_json"]["row"]["state"] is None


def test_folder_pattern_selects_and_concatenates_in_name_order(drive):
    rows = gf.fetch_gdrive_rows_as_visit_dicts(_source(folder_id="folderA", file_pattern="answers_scored_*.csv"), OPP)
    assert [r["form_json"]["row"]["session_id"] for r in rows] == ["s1", "s2", "s9"]
    assert {r["form_json"]["file"]["name"] for r in rows} == {
        "answers_scored_bednets.csv",
        "answers_scored_malaria.csv",
    }


def test_folder_with_no_match_is_an_error_not_an_empty_result(drive):
    with pytest.raises(gf.GDriveSourceError, match="no CSV / Sheet / JSON files matching"):
        gf.fetch_gdrive_rows_as_visit_dicts(_source(folder_id="folderA", file_pattern="nope_*.csv"), OPP)


def test_google_sheet_is_exported_as_csv(drive):
    rows = gf.fetch_gdrive_rows_as_visit_dicts(_source(file_id="sheet"), OPP)
    assert rows[0]["form_json"]["row"] == {"a": "1", "b": "2"}
    assert ("export", "sheet", "text/csv") in drive.calls


def test_json_rows(drive):
    rows = gf.fetch_gdrive_rows_as_visit_dicts(_source(file_id="json"), OPP)
    assert [r["form_json"]["row"]["a"] for r in rows] == [1, None]


def test_oversized_file_is_refused_before_download(drive, monkeypatch):
    monkeypatch.setattr(gf, "MAX_FILE_BYTES", 10)
    with pytest.raises(gf.GDriveSourceError, match="limit"):
        gf.fetch_gdrive_rows_as_visit_dicts(_source(file_id="fileCSV"), OPP)
    assert ("download", "fileCSV") not in drive.calls


# --------------------------------------------------------------------------- authorization


def test_unauthorized_source_is_refused_before_touching_drive(drive):
    with pytest.raises(gf.GDriveSourceError, match="not been authorized"):
        gf.fetch_gdrive_rows_as_visit_dicts(DataSourceConfig(type="gdrive", file_id="fileCSV"), OPP)
    assert drive.calls == []


def test_only_dimagi_staff_can_authorize():
    with pytest.raises(gf.GDriveSourceError, match="Only Dimagi staff"):
        gf.authorize_gdrive_source({"type": "gdrive", "file_id": "fileCSV"}, OPP, PARTNER)


def test_authorization_is_bound_to_the_opportunity(drive):
    src = _source(file_id="fileCSV")
    drive.calls.clear()  # stamping walks the tree; the READ must touch nothing
    with pytest.raises(gf.GDriveSourceError, match="not authorized for this pipeline in opportunity 999"):
        gf.fetch_gdrive_rows_as_visit_dicts(src, 999)
    assert drive.calls == []


def test_retargeting_after_authorization_is_refused(drive):
    stamped = gf.authorize_gdrive_source({"type": "gdrive", "file_id": "fileCSV"}, OPP, STAFF)
    stamped["file_id"] = "fileOther"
    with pytest.raises(gf.GDriveSourceError, match="changed after it was authorized"):
        gf.fetch_gdrive_rows_as_visit_dicts(DataSourceConfig(**stamped), OPP)


def test_forged_authorization_is_refused(drive):
    forged = {"opportunity_id": OPP, "authorized_by": "x@dimagi.com", "signature": "0" * 64}
    with pytest.raises(gf.GDriveSourceError):
        gf.fetch_gdrive_rows_as_visit_dicts(
            DataSourceConfig(type="gdrive", file_id="fileCSV", authorization=forged), OPP
        )


def test_schema_resave_keeps_a_valid_authorization_for_anyone():
    schema = {"data_source": gf.authorize_gdrive_source({"type": "gdrive", "file_id": "fileCSV"}, OPP, STAFF)}
    schema["fields"] = [{"name": "q", "path": "row.quality"}]
    assert gf.authorize_schema_drive_source(schema, OPP, PARTNER) is schema


def test_schema_resave_with_a_new_target_needs_staff():
    stored = gf.authorize_gdrive_source({"type": "gdrive", "file_id": "fileCSV"}, OPP, STAFF)
    schema = {"data_source": {**stored, "file_id": "fileOther"}}
    with pytest.raises(gf.GDriveSourceError, match="Only Dimagi staff"):
        gf.authorize_schema_drive_source(schema, OPP, PARTNER, previous_schema={"data_source": stored})
    restamped = gf.authorize_schema_drive_source(schema, OPP, STAFF, previous_schema={"data_source": stored})
    gf.verify_gdrive_authorization(DataSourceConfig(**restamped["data_source"]), OPP)


def test_non_drive_schema_passes_through_untouched():
    schema = {"data_source": {"type": "connect_csv"}, "fields": []}
    assert gf.authorize_schema_drive_source(schema, OPP, PARTNER) is schema


# --------------------------------------------------------------------------- config


@pytest.mark.parametrize(
    "kwargs, msg",
    [
        ({"type": "gdrive"}, "exactly one of"),
        ({"type": "gdrive", "file_id": "a", "folder_id": "b"}, "exactly one of"),
        ({"type": "gdrive", "file_id": "a' or 'x"}, "must be a Drive id"),
        ({"type": "gdrive", "file_id": "a", "file_pattern": "*.csv"}, "only applies with folder_id"),
        ({"type": "connect_csv", "file_id": "a"}, "only valid for type='gdrive'"),
    ],
)
def test_data_source_validation(kwargs, msg):
    with pytest.raises(ValueError, match=msg):
        DataSourceConfig(**kwargs)


def test_repointing_a_drive_source_changes_the_cache_hash():
    def cfg(file_id):
        return AnalysisPipelineConfig(
            grouping_key="username", fields=[], data_source=DataSourceConfig(type="gdrive", file_id=file_id)
        )

    assert get_config_hash(cfg("fileA")) != get_config_hash(cfg("fileB"))
    plain = AnalysisPipelineConfig(grouping_key="username", fields=[])
    assert get_config_hash(plain) == get_config_hash(AnalysisPipelineConfig(grouping_key="username", fields=[]))


# --------------------------------------------------------------------------- review regressions


def test_caller_who_is_not_a_member_is_refused_before_touching_drive(drive, monkeypatch):
    """A stamp names an opportunity; it is not a pass to it (a clone or fan-out can
    run a pipeline for an opportunity the caller is not in)."""
    monkeypatch.setattr(gf, "_caller_opportunity_ids", lambda request, token: {42})
    src = _source(file_id="fileCSV")
    drive.calls.clear()  # stamping walks the tree; the READ must touch nothing
    with pytest.raises(gf.GDriveSourceError, match="not a member of opportunity 1251"):
        gf.fetch_gdrive_rows_as_visit_dicts(src, OPP)
    assert drive.calls == []


def test_unresolvable_caller_is_refused(drive, monkeypatch):
    monkeypatch.setattr(gf, "_caller_opportunity_ids", lambda request, token: None)
    src = _source(file_id="fileCSV")
    drive.calls.clear()  # stamping walks the tree; the READ must touch nothing
    with pytest.raises(gf.GDriveSourceError, match="cannot confirm who is reading"):
        gf.fetch_gdrive_rows_as_visit_dicts(src, OPP)
    assert drive.calls == []


def test_caller_ids_come_from_the_web_session():
    request = SimpleNamespace(
        user=SimpleNamespace(is_authenticated=True, view_synthetic_opps=False),
        session={"labs_oauth": {"organization_data": {"opportunities": [{"id": 1251}, {"id": "77"}]}}},
    )
    assert gf._caller_opportunity_ids(request, None) == {1251, 77}
    assert gf._caller_opportunity_ids(None, None) is None


def test_staff_resave_does_not_authorize_a_target_someone_else_stored():
    """Confused deputy: a partner stores an unstamped target by a path that never
    authorizes; a staff member later edits a field. That save must not stamp it."""
    planted = {"data_source": {"type": "gdrive", "folder_id": "internalFixtures"}, "fields": []}
    edited = {**planted, "fields": [{"name": "x", "path": "row.x"}]}
    out = gf.authorize_schema_drive_source(edited, OPP, STAFF, previous_schema=planted)
    assert "authorization" not in out["data_source"]
    # ...and even a deliberate staff authorization cannot reach outside the allowed tree.
    with pytest.raises(gf.GDriveSourceError, match="not inside an allowed workflow-data folder"):
        gf.authorize_schema_drive_source(edited, OPP, STAFF, previous_schema=planted, force=True)


def test_resave_without_the_authorization_field_carries_the_stored_stamp():
    stored = {"data_source": gf.authorize_gdrive_source({"type": "gdrive", "file_id": "fileCSV"}, OPP, STAFF)}
    resent = {"data_source": {"type": "gdrive", "file_id": "fileCSV"}, "fields": [{"name": "x", "path": "row.x"}]}
    out = gf.authorize_schema_drive_source(resent, OPP, PARTNER, previous_schema=stored)
    assert out["data_source"]["authorization"] == stored["data_source"]["authorization"]


def test_nested_json_cells_are_kept_not_crashed_on(drive):
    rows = gf.fetch_gdrive_rows_as_visit_dicts(_source(file_id="json"), OPP)
    assert rows[0]["form_json"]["row"]["tags"] == ["x"]
    assert rows[1]["form_json"]["row"]["tags"] == {"k": 1}


def test_impossible_dates_become_null_not_a_cache_crash(drive):
    rows = gf.fetch_gdrive_rows_as_visit_dicts(_source(file_id="baddate", date_column="day"), OPP)
    assert [r["visit_date"] for r in rows] == [None, "2025-03-01"]


def test_folder_reads_only_its_tabular_files(drive):
    rows = gf.fetch_gdrive_rows_as_visit_dicts(_source(folder_id="folderA"), OPP)
    names = {r["form_json"]["file"]["name"] for r in rows}
    assert "README" not in names and "answers_scored_notes.pdf" not in names
    assert ("download", "pdf") not in drive.calls


def test_a_google_doc_named_directly_says_unsupported_not_unshared(drive):
    with pytest.raises(gf.GDriveSourceError, match="unsupported file type"):
        gf.fetch_gdrive_rows_as_visit_dicts(_source(file_id="doc"), OPP)


def test_row_cap_is_enforced_while_reading(drive, monkeypatch):
    monkeypatch.setattr(gf, "MAX_ROWS", 2)
    with pytest.raises(gf.GDriveSourceError, match="more than 2 rows"):
        gf.fetch_gdrive_rows_as_visit_dicts(_source(folder_id="folderA", file_pattern="answers_scored_*.csv"), OPP)


def test_malformed_authorization_is_refused_not_a_500(drive):
    bad = {"opportunity_id": OPP, "authorized_by": "x@dimagi.com", "signature": 12345}
    with pytest.raises(gf.GDriveSourceError, match="not been authorized"):
        gf.verify_gdrive_authorization(DataSourceConfig(type="gdrive", file_id="fileCSV", authorization=bad), OPP)
    with pytest.raises(ValueError, match="stamped by the server"):
        DataSourceConfig(type="gdrive", file_id="fileCSV", authorization="yes")
    with pytest.raises(ValueError, match="list of strings"):
        DataSourceConfig(type="gdrive", file_id="fileCSV", null_values="NA")


def test_error_messages_carry_the_drive_prefix():
    assert str(gf.GDriveSourceError("x")).startswith("Google Drive source: ")


@pytest.mark.django_db
def test_pipeline_gates_cache_only_reads(monkeypatch):
    """A cache hit must be gated like a fetch: cached rows are the same Drive data."""
    from connect_labs.labs.analysis.config import CacheStage
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    monkeypatch.setattr(gf, "_caller_opportunity_ids", lambda request, token: {OPP})
    config = AnalysisPipelineConfig(
        grouping_key="username",
        fields=[],
        terminal_stage=CacheStage.AGGREGATED,
        data_source=DataSourceConfig(type="gdrive", file_id="fileCSV"),
    )
    pipeline = AnalysisPipeline(access_token="tok")
    with pytest.raises(gf.GDriveSourceError, match="not been authorized"):
        pipeline.get_cached_result_only(config, OPP)
    with pytest.raises(gf.GDriveSourceError, match="not been authorized"):
        pipeline.get_period_scoped_result_only(config, OPP)
    events = list(pipeline.stream_analysis(config, OPP))
    assert events[-1][0] == "error" and "not been authorized" in events[-1][1]["message"]


# --------------------------------------------------------------------------- containment


def test_a_target_outside_the_allowed_tree_cannot_be_stamped():
    with pytest.raises(gf.GDriveSourceError, match="not inside an allowed workflow-data folder"):
        gf.authorize_gdrive_source({"type": "gdrive", "folder_id": "internalFixtures"}, OPP, STAFF)


def test_a_stamped_target_moved_out_of_the_tree_is_refused_at_read(drive):
    src = _source(file_id="fileCSV")
    drive.files["fileCSV"]["parent"] = "internalFixtures"  # moved after authorization
    with pytest.raises(gf.GDriveSourceError, match="not inside an allowed workflow-data folder"):
        gf.fetch_gdrive_rows_as_visit_dicts(src, OPP)
    assert ("download", "fileCSV") not in drive.calls


def test_the_root_itself_is_readable(drive):
    drive.files["fileCSV"]["parent"] = "ROOT"
    assert gf.fetch_gdrive_rows_as_visit_dicts(_source(folder_id="ROOT"), OPP)


def test_no_root_configured_turns_drive_sources_off(settings):
    settings.LABS_WORKFLOW_GDRIVE_ROOT_IDS = []
    with pytest.raises(gf.GDriveSourceError, match="not enabled"):
        gf.authorize_gdrive_source({"type": "gdrive", "file_id": "fileCSV"}, OPP, STAFF)


def test_parent_cycles_terminate():
    class Loop(FakeDrive):
        def get_parents(self, file_id):
            return ["b"] if file_id == "a" else ["a"]

    with pytest.raises(gf.GDriveSourceError, match="not inside"):
        gf.assert_under_allowed_root(Loop({}), "a")


# --------------------------------------------------------------------------- second review


def test_a_stamp_is_bound_to_its_pipeline(drive):
    """Any member can read a stamp; pasted into another pipeline it must not verify."""
    stamped = gf.authorize_gdrive_source({"type": "gdrive", "file_id": "fileCSV"}, OPP, STAFF, pipeline_id=7)
    src = DataSourceConfig(**stamped)
    assert gf.fetch_gdrive_rows_as_visit_dicts(src, OPP, pipeline_id=7)
    with pytest.raises(gf.GDriveSourceError, match="not authorized for this pipeline"):
        gf.fetch_gdrive_rows_as_visit_dicts(src, OPP, pipeline_id=8)


def test_celery_mock_request_resolves_membership_from_its_token(monkeypatch):
    """workflow/tasks.py builds a request whose labs_oauth holds only a token; that
    must read the org tree from Connect, not count as 'member of nothing'."""
    from connect_labs.labs.integrations.connect import oauth

    seen = {}

    def fake_fetch(token, **kw):
        seen["token"] = token
        return {"opportunities": [{"id": OPP}]}

    monkeypatch.setattr(oauth, "fetch_user_organization_data", fake_fetch)
    request = SimpleNamespace(user=None, session={"labs_oauth": {"access_token": "job-tok", "expires_at": 0}})
    monkeypatch.setattr("connect_labs.labs.integrations.ocs.ocs_tokens.current_mcp_caller", lambda: None)
    assert gf._caller_opportunity_ids(request, None) == {OPP}
    assert seen["token"] == "job-tok"


@pytest.mark.parametrize(
    "content, name",
    [(b"{not json", "x.json"), ("caf\xe9".encode("cp1252") + b"\n1\n", "x.csv")],
)
def test_unreadable_files_are_named_not_raw_errors(drive, content, name):
    drive.files["bad"] = {"name": name, "mimeType": "", "content": content}
    with pytest.raises(gf.GDriveSourceError, match=f"{name} could not be read"):
        gf.fetch_gdrive_rows_as_visit_dicts(_source(file_id="bad"), OPP)

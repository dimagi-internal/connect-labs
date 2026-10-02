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
def drive(monkeypatch):
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
                     "content": json.dumps({"rows": [{"a": 1}, {"a": None}]}).encode()},
        }
    )
    monkeypatch.setattr(gf, "_drive", lambda: fake)
    return fake


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
    with pytest.raises(gf.GDriveSourceError, match="No files matching"):
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
    with pytest.raises(gf.GDriveSourceError, match="not authorized for opportunity 999"):
        gf.fetch_gdrive_rows_as_visit_dicts(_source(file_id="fileCSV"), 999)
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
    schema = {"data_source": gf.authorize_gdrive_source({"type": "gdrive", "file_id": "fileCSV"}, OPP, STAFF)}
    schema["data_source"]["file_id"] = "fileOther"
    with pytest.raises(gf.GDriveSourceError, match="Only Dimagi staff"):
        gf.authorize_schema_drive_source(schema, OPP, PARTNER)
    restamped = gf.authorize_schema_drive_source(schema, OPP, STAFF)
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

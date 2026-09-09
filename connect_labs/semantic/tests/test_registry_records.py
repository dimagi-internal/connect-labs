"""A registry held as DATA must behave exactly like the one held as files.

This is the whole safety argument for moving Layers 2 and 3 into the database.
Iterating an indicator should not cost a merge and a deploy -- but the moment the
definition is editable live, nothing between an edit and a dashboard load is
reviewing it. Two properties carry the weight:

  1. A record seeded from the files compiles to the SAME SQL. If that holds,
     migrating KMC is a copy and not a rewrite, and nobody has to diff numbers.
  2. A registry that would break the dashboard cannot be SAVED. On disk CI was
     the gate; in the database the write path is the only gate there is.
"""

from __future__ import annotations

import copy

import pytest
import yaml

from connect_labs.semantic.compiler import SCOPES, compile_indicator_sql
from connect_labs.semantic.runtime import REGISTRY_ROOT, SemanticRuntimeError, resolve_registry
from connect_labs.semantic.seed import registry_payload
from connect_labs.semantic.validation import RegistryInvalid, validate_registry
from connect_labs.workflow.data_access import SemanticRegistryDataAccess, SemanticRegistryRecord

VISIT_SQL = "SELECT * FROM fixture_visits"


class _FakeLabsAPI:
    """Enough of the records API to exercise the store without a Connect backend."""

    def __init__(self):
        self.rows: dict[int, dict] = {}
        self._next = 1

    def create_record(self, experiment, type, data):
        rid = self._next
        self._next += 1
        self.rows[rid] = {
            "id": rid,
            "experiment": experiment,
            "type": type,
            "data": data,
            "opportunity_id": None,
        }
        return SemanticRegistryRecord(self.rows[rid])

    def update_record(self, record_id, experiment, type, data):
        self.rows[record_id]["data"] = data
        return SemanticRegistryRecord(self.rows[record_id])

    def get_record_by_id(self, record_id, experiment, type, model_class):
        row = self.rows.get(record_id)
        return model_class(row) if row else None

    def get_records(self, experiment, type, model_class, **kwargs):
        return [model_class(r) for r in self.rows.values()]

    def delete_record(self, record_id):
        self.rows.pop(record_id, None)


def _store() -> SemanticRegistryDataAccess:
    """A store wired to the fake API, bypassing the OAuth-token constructor."""
    access = SemanticRegistryDataAccess.__new__(SemanticRegistryDataAccess)
    access.labs_api = _FakeLabsAPI()
    access.opportunity_id = None
    access.organization_id = None
    access.program_id = None
    return access


@pytest.fixture
def seeded():
    store = _store()
    payload = registry_payload("kmc")
    record = store.create_registry(
        name=payload["name"],
        properties=payload["properties"],
        indicators=payload["indicators"],
        deployment=payload["deployment"],
    )
    return store, record


def test_a_seeded_record_compiles_to_the_same_sql_as_the_files(seeded):
    """The migration-safety proof: same definition, same SQL, at every scope.

    Not "it compiles" and not "the numbers look close" -- byte-identical SQL. If a
    record ever compiled differently from the files it was seeded from, the
    difference would show up as numbers moving on a dashboard nobody had touched.
    """
    store, record = seeded
    disk_props, disk_inds = (
        yaml.safe_load((REGISTRY_ROOT / "kmc" / "properties.yml").read_text()),
        yaml.safe_load((REGISTRY_ROOT / "kmc" / "indicators.yml").read_text()),
    )
    disk_dep = yaml.safe_load((REGISTRY_ROOT / "kmc" / "deployment.yml").read_text())
    disk_map = {int(k): str(v) for k, v in disk_dep["llo_map"].items()}
    disk_settings = disk_dep["settings"]

    db_props, db_inds, db_map, db_settings, _db_dep = resolve_registry({"registry_id": record.id}, store)

    assert db_map == disk_map, "llo_map did not survive the JSON round trip as integers"
    assert db_settings == disk_settings

    for scope in SCOPES:
        from_disk = compile_indicator_sql(
            disk_props, disk_inds, VISIT_SQL, scope=scope, llo_map=disk_map, settings=disk_settings
        )
        from_db = compile_indicator_sql(
            db_props, db_inds, VISIT_SQL, scope=scope, llo_map=db_map, settings=db_settings
        )
        assert from_db == from_disk, f"scope {scope!r} compiled differently from a record than from the files"


def test_the_built_in_registry_is_still_the_default():
    """Binding nothing must behave exactly as it did before records existed."""
    named_props, named_inds, named_map, named_settings, _n = resolve_registry({"name": "kmc"})
    default_props, default_inds, default_map, default_settings, _d = resolve_registry(None)
    assert (default_props, default_inds, default_map, default_settings) == (
        named_props,
        named_inds,
        named_map,
        named_settings,
    )
    assert default_map, "the default registry lost its deployment facts"


def test_an_id_without_a_store_is_an_error_not_a_silent_fallback():
    """Falling back here would serve a DIFFERENT definition than the one asked for."""
    with pytest.raises(SemanticRuntimeError) as exc:
        resolve_registry({"registry_id": 3})
    assert "registry_id" in str(exc.value)


def test_a_registry_that_cannot_compile_is_refused_on_write():
    """The write path is the only gate a database-backed registry has."""
    store = _store()
    payload = registry_payload("kmc")
    broken = copy.deepcopy(payload["indicators"])
    broken["measures"].insert(0, {"name": "zz", "type": "count", "filters": [{"sql": "{CUBE}.no_such_column"}]})

    with pytest.raises(RegistryInvalid) as exc:
        store.create_registry(
            name="broken",
            properties=payload["properties"],
            indicators=broken,
            deployment=payload["deployment"],
        )
    assert any("no_such_column" in e for e in exc.value.errors)


def test_an_update_validates_the_MERGED_result_not_the_fragment(seeded):
    """A patch can invalidate something it does not mention.

    Renaming a property away is legal in isolation and breaks every indicator that
    referenced it. Validating the fragment alone would wave that straight through
    and the dashboard would fail at the next load, on a query nobody edited.
    """
    store, record = seeded
    props = copy.deepcopy(record.properties_doc)
    victim = next(p for p in props["properties"] if p["name"] == "enrolled_within_3d")
    victim["name"] = "renamed_away"

    with pytest.raises(RegistryInvalid) as exc:
        store.update_registry(record.id, properties=props)
    assert any("enrolled_within_3d" in e for e in exc.value.errors)

    # and the stored record is untouched -- a rejected write must not half-apply
    assert store.get_registry(record.id).version == 1
    assert any(p["name"] == "enrolled_within_3d" for p in store.get_registry(record.id).properties_doc["properties"])


def test_a_good_update_bumps_the_version_and_a_metadata_edit_does_not(seeded):
    store, record = seeded
    assert store.update_registry(record.id, description="just a note").version == 1
    inds = copy.deepcopy(record.indicators_doc)
    inds["measures"][0].setdefault("meta", {})
    assert store.update_registry(record.id, indicators=inds).version == 2


def test_validation_reports_every_problem_not_only_the_first():
    """A person editing a registry should see the whole list, not play whack-a-mole."""
    payload = registry_payload("kmc")
    inds = copy.deepcopy(payload["indicators"])
    inds["measures"].insert(0, {"name": "z1", "type": "count", "filters": [{"sql": "{CUBE}.nope_one"}]})
    inds["measures"].insert(1, {"name": "z2", "type": "count", "filters": [{"sql": "{CUBE}.nope_two"}]})
    errors = validate_registry(payload["properties"], inds, payload["deployment"])
    assert any("nope_one" in e for e in errors) and any("nope_two" in e for e in errors)

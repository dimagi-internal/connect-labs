"""SYNTHETIC_STREAM_PROFILE makes the same bundle, from visits fetched to a file."""

import pytest

from connect_labs.labs.synthetic import clone_from_prod
from connect_labs.labs.synthetic.bundle import LocalBundleStore, read_bundle
from connect_labs.labs.synthetic.generator.fixtures import stream_profile
from connect_labs.labs.synthetic.generator.fixtures.tests.test_stream_profile import _app, messy_export

DETAIL = {"id": 4242, "name": "Source", "description": "kept out of the bundle"}


@pytest.fixture
def source(monkeypatch):
    visits = messy_export(8, cases=70, workers=5)
    fetched = []

    def fetch(base_url, opp_id, key, token):
        fetched.append(key)
        return {"": DETAIL, "user_visits": visits, "user_data": [], "app_structure": _app()}[key]

    def fetch_visits(base_url, opp_id, token, path):
        fetched.append("user_visits (to a file)")
        return stream_profile.VisitFile.write(path, visits)

    monkeypatch.setattr(clone_from_prod, "_fetch_endpoint", fetch)
    monkeypatch.setattr(clone_from_prod, "_fetch_visits", fetch_visits)
    return fetched


def _bundle(tmp_path, name):
    store = LocalBundleStore(tmp_path / name)
    handle = clone_from_prod.profile_opp_to_bundle(
        4242, base_url="https://x", oauth_token="t", store=store, mirror=True, processes=1
    )
    return read_bundle(handle)


def test_the_streamed_bundle_is_the_bundle(settings, tmp_path, source):
    settings.SYNTHETIC_STREAM_PROFILE = False
    in_memory = _bundle(tmp_path, "memory")
    assert "user_visits" in source
    source.clear()
    settings.SYNTHETIC_STREAM_PROFILE = True
    streamed = _bundle(tmp_path, "stream")
    assert source == ["", "user_visits (to a file)", "user_data", "app_structure"]
    assert streamed == in_memory
    assert "transplant_pool" in streamed.manifest_yaml


def test_a_source_with_no_visits_is_refused_either_way(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(clone_from_prod, "_fetch_endpoint", lambda b, o, key, t: DETAIL if key == "" else [])
    monkeypatch.setattr(
        clone_from_prod, "_fetch_visits", lambda b, o, t, path: stream_profile.VisitFile.write(path, [])
    )
    for on in (False, True):
        settings.SYNTHETIC_STREAM_PROFILE = on
        with pytest.raises(ValueError, match="No user_visits"):
            clone_from_prod.profile_opp_to_bundle(4242, base_url="https://x", oauth_token="t", store=None)


def test_a_dump_profiles_to_the_same_bundle_either_way(settings, tmp_path):
    import json

    visits = messy_export(11, cases=40, workers=4)
    files = {"opportunity.json": DETAIL, "user_visits.json": visits, "user_data.json": []}

    class Drive:
        def list_folder(self, folder_id):
            return {name: name for name in files}

        def download_file(self, file_id):
            return json.dumps(files[file_id]).encode()

    bundles = []
    for on in (False, True):
        settings.SYNTHETIC_STREAM_PROFILE = on
        store = LocalBundleStore(tmp_path / str(on))
        handle = clone_from_prod.profile_dump_to_bundle(
            "dump", drive=Drive(), store=store, app_structure=_app(), mirror=True
        )
        bundles.append(read_bundle(handle))
    assert bundles[0] == bundles[1]


def test_profile_from_prod_returns_the_same_manifest_either_way(settings, tmp_path, monkeypatch):
    from connect_labs.mcp.tools import synthetic as syn

    visits = messy_export(12, cases=40, workers=4)
    monkeypatch.setattr(
        syn, "_fetch_endpoint", lambda b, o, key, t: {"": DETAIL, "user_visits": visits, "user_data": []}[key]
    )
    monkeypatch.setattr(syn, "_fetch_visits", lambda b, o, t, path: stream_profile.VisitFile.write(path, visits))
    results = []
    for on in (False, True):
        settings.SYNTHETIC_STREAM_PROFILE = on
        results.append(syn._profile_from_prod_inner(opportunity_id=4242, token="t", mirror=True))
    assert results[0] == results[1]
    assert results[1]["source_visit_count"] == len(visits)


def test_streaming_is_the_default():
    from django.conf import settings

    assert settings.SYNTHETIC_STREAM_PROFILE is True

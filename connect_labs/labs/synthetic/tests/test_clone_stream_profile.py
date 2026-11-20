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

"""Phase-1 clone tests: profile_opp_to_bundle + profile_opps_bulk."""

from unittest.mock import patch

from commcare_connect.labs.synthetic import clone_from_prod
from commcare_connect.labs.synthetic.bundle import read_bundle


def test_profile_opp_to_bundle_writes_bundle(tmp_path):
    # Spread visits across two weeks so the profiler can compute a valid
    # start_date < end_date timeline (manifest validation rejects same-day spans).
    visits = [{"username": "a", "visit_date": "2026-05-04", "form_json": {"form": {"w": 1.0}}}] * 4 + [
        {"username": "b", "visit_date": "2026-05-11", "form_json": {"form": {"w": 1.5}}}
    ] * 4
    fake = {
        "": {"id": 523, "name": "KMC NAMA"},
        "user_visits": visits,
        "user_data": [],
        "app_structure": {"learn_app": None, "deliver_app": {"modules": []}},
    }

    def fake_fetch(base_url, opp_id, key, token):
        return fake[key]

    with patch.object(clone_from_prod, "_fetch_endpoint", side_effect=fake_fetch):
        bundle = clone_from_prod.profile_opp_to_bundle(523, base_url="https://x", oauth_token="t", out_dir=tmp_path)

    loaded = read_bundle(bundle)
    assert loaded.source_opp_id == 523
    assert "opportunity_id" in loaded.manifest_yaml
    assert loaded.app_structure["deliver_app"] == {"modules": []}


def test_profile_opp_to_bundle_raises_on_empty_visits(tmp_path):
    fake = {
        "": {"id": 1, "name": "Test Opp"},
        "user_visits": [],
        "user_data": [],
        "app_structure": {},
    }

    def fake_fetch(base_url, opp_id, key, token):
        return fake[key]

    with patch.object(clone_from_prod, "_fetch_endpoint", side_effect=fake_fetch):
        try:
            clone_from_prod.profile_opp_to_bundle(1, base_url="https://x", oauth_token="t", out_dir=tmp_path)
            assert False, "Expected ValueError"
        except ValueError:
            pass


def test_profile_opps_bulk_isolates_failures(tmp_path):
    call_count = 0

    def fake_fetch(base_url, opp_id, key, token):
        nonlocal call_count
        call_count += 1
        if opp_id == 999:
            raise RuntimeError("simulated failure")
        # Spread visits over two weeks so profiler produces a valid timeline span.
        visits = [{"username": "a", "visit_date": "2026-05-04", "form_json": {}}] * 4 + [
            {"username": "b", "visit_date": "2026-05-11", "form_json": {}}
        ] * 4
        return {
            "": {"id": opp_id, "name": f"Opp {opp_id}"},
            "user_visits": visits,
            "user_data": [],
            "app_structure": {},
        }[key]

    with patch.object(clone_from_prod, "_fetch_endpoint", side_effect=fake_fetch):
        results = clone_from_prod.profile_opps_bulk(
            [100, 999, 200],
            base_url="https://x",
            oauth_token="t",
            out_dir=tmp_path,
        )

    # opp 999 fails but 100 and 200 succeed
    assert len(results) == 2
    ids = {read_bundle(r).source_opp_id for r in results}
    assert ids == {100, 200}

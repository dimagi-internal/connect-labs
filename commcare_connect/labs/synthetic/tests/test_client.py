from commcare_connect.labs.synthetic.client import SyntheticExportClient


class StubStore:
    def __init__(self, data):
        self._data = data

    def load_endpoint(self, opp_id, key):
        return self._data[key]


def test_paginate_user_visits_yields_single_page():
    store = StubStore({"user_visits": [{"id": 1}, {"id": 2}]})
    client = SyntheticExportClient(opp_id=42, fixture_store=store)

    pages = list(client.paginate("/export/opportunity/42/user_visits/"))

    assert pages == [[{"id": 1}, {"id": 2}]]


def test_fetch_all_returns_flat_list():
    store = StubStore({"user_data": [{"username": "alice"}, {"username": "bob"}]})
    client = SyntheticExportClient(opp_id=42, fixture_store=store)

    assert client.fetch_all("/export/opportunity/42/user_data/") == [
        {"username": "alice"},
        {"username": "bob"},
    ]


def test_opportunity_detail_wraps_dict_in_list():
    store = StubStore({"": {"id": 42, "name": "demo"}})
    client = SyntheticExportClient(opp_id=42, fixture_store=store)

    pages = list(client.paginate("/export/opportunity/42/"))

    assert pages == [[{"id": 42, "name": "demo"}]]


def test_fetch_all_for_detail_returns_list_with_dict():
    store = StubStore({"": {"id": 42}})
    client = SyntheticExportClient(opp_id=42, fixture_store=store)

    assert client.fetch_all("/export/opportunity/42/") == [{"id": 42}]


def test_params_are_ignored_without_error():
    store = StubStore({"user_visits": []})
    client = SyntheticExportClient(opp_id=42, fixture_store=store)

    assert client.fetch_all("/export/opportunity/42/user_visits/", params={"images": "true"}) == []


def test_context_manager_protocol():
    store = StubStore({"user_visits": []})
    with SyntheticExportClient(opp_id=42, fixture_store=store) as client:
        assert client.fetch_all("/export/opportunity/42/user_visits/") == []


def test_endpoint_key_parses_last_segment():
    assert SyntheticExportClient._endpoint_key("/export/opportunity/42/user_visits/") == "user_visits"
    assert SyntheticExportClient._endpoint_key("/export/opportunity/42/") == ""
    assert SyntheticExportClient._endpoint_key("/export/opportunity/42/completed_works/") == "completed_works"

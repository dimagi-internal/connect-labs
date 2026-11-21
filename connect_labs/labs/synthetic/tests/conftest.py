import pytest


@pytest.fixture(autouse=True)
def _visits_come_from_the_fetch_a_test_fakes(monkeypatch):
    """Profiling streams the visit export to a file (``SYNTHETIC_STREAM_PROFILE``).

    Tests fake the export through ``clone_from_prod._fetch_endpoint``; this routes the
    file fetch through whatever that is at call time, so a faked export reaches the
    streaming path as well and nothing goes to the network."""
    from connect_labs.labs.synthetic import clone_from_prod
    from connect_labs.labs.synthetic.generator.fixtures.stream_profile import VisitFile

    def fetch_visits(base_url, opp_id, token, path):
        visits = clone_from_prod._fetch_endpoint(base_url, opp_id, "user_visits", token)
        return VisitFile.write(path, visits if isinstance(visits, list) else [])

    monkeypatch.setattr(clone_from_prod, "_fetch_visits", fetch_visits)

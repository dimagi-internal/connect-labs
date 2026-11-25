"""The CommCare HQ check made before a fill is queued (#3235's follow-up).

A fill reads HQ with the viewer's STORED HQ token, so the check asks with that token,
for both HQ source types, and for the domain each pipeline actually names. Before, it
asked with the browser session's token, only for `cchq_forms`, and only of the
opportunity's own domain: run 29804's report (HQ cases pipelines) passed it and its
fills failed for minutes at a time.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.test import RequestFactory

from connect_labs.workflow import pipeline_reads, views
from connect_labs.workflow.tests.test_preview_warms_on_read import _call
from connect_labs.workflow.visit_cache import visit_warm_slot

OPP = 765


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _pipelines(*data_sources):
    """A PipelineDataAccess whose pipelines 1..n read the given data sources."""

    class _Access:
        def __init__(self, *a, **k):
            pass

        def use_sources(self, sources):
            pass

        def get_definition(self, pid):
            return SimpleNamespace(schema={"data_source": data_sources[int(pid) - 1]})

        def close(self):
            pass

    definition = SimpleNamespace(pipeline_sources=[{"pipeline_id": i + 1} for i in range(len(data_sources))])
    return _Access, definition


def _probe(*data_sources, stored_token="hq-stored", hq_accepts=True, opp_domain="mbw-solina"):
    access_cls, definition = _pipelines(*data_sources)
    request = RequestFactory().get("/")
    request.user = SimpleNamespace(is_authenticated=True, pk=7)
    request.session = {}  # no browser-session HQ login at all
    client = MagicMock()
    client.return_value.verify_hq_access.return_value = hq_accepts
    with (
        patch("connect_labs.workflow.data_access.PipelineDataAccess", access_cls),
        patch(
            "connect_labs.labs.integrations.commcare.cchq_tokens.cchq_token_or_none", return_value=stored_token
        ) as token_lookup,
        patch("connect_labs.labs.integrations.commcare.api_client.CommCareDataAccess", client),
        patch(
            "connect_labs.labs.analysis.data_access.fetch_opportunity_metadata",
            return_value={"cc_domain": opp_domain},
        ),
    ):
        answer = pipeline_reads.probe_cchq_access(request, definition, OPP, "connect-tok")
    return answer, client, token_lookup


def test_a_report_reading_only_hq_cases_is_checked():
    answer, _client, _lookup = _probe({"type": "connect_csv"}, {"type": "cchq_cases"}, stored_token=None)
    assert answer is not None and answer.status == 403
    assert answer.details["cchq_auth_required"] is True


def test_it_asks_with_the_token_the_fill_will_use_not_the_sessions():
    answer, client, _lookup = _probe({"type": "cchq_forms"})
    assert answer is None
    assert client.call_args.kwargs["cchq_access_token"] == "hq-stored"


def test_hq_refusing_the_stored_token_is_reported_with_its_domain():
    answer, _client, _lookup = _probe({"type": "cchq_cases"}, hq_accepts=False)
    assert answer.status == 403 and answer.details["domain"] == "mbw-solina"


def test_a_pipeline_that_names_its_own_domain_is_checked_against_that_domain():
    _answer, client, _lookup = _probe({"type": "cchq_cases", "domain": "other-space"})
    assert [c.args[1] for c in client.call_args_list] == ["other-space"]


def test_a_report_with_no_hq_sources_asks_nothing():
    answer, client, lookup = _probe({"type": "connect_csv"}, {"type": "hq_store"}, stored_token=None)
    assert answer is None
    assert lookup.call_count == 0 and client.call_count == 0


def test_a_preview_with_no_hq_login_is_told_to_reconnect_and_queues_no_fill(monkeypatch):
    monkeypatch.setattr(pipeline_reads, "cchq_domains", lambda *a, **k: {None})
    monkeypatch.setattr("connect_labs.labs.integrations.commcare.cchq_tokens.cchq_token_or_none", lambda u: None)
    response, _builds, task = _call(monkeypatch)
    assert response.status_code == 409
    body = json.loads(response.content)
    assert body["code"] == "cchq_reconnect" and body["cchq_auth_required"] is True
    assert task.delay.call_count == 0


def test_a_fill_already_running_is_joined_without_asking_hq_again(monkeypatch):
    probe = MagicMock()
    monkeypatch.setattr(views, "probe_cchq_access", probe)
    visit_warm_slot(5).claim()
    request = RequestFactory().get("/", headers={"x-labs-sandbox-for": "5"})
    request.user = SimpleNamespace(is_authenticated=True, pk=7)
    request.session = {}
    views._warm_in_background(
        request,
        SimpleNamespace(opportunity_id=OPP, program_id=None),
        SimpleNamespace(id=5, opportunity_id=OPP),
        [OPP],
    )
    assert probe.call_count == 0

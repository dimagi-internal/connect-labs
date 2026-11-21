"""The HQ delta iterators, against HQ's documented response shapes (docs/api in dimagi/commcare-hq)."""

from datetime import datetime, timezone
from unittest import mock

import pytest

from connect_labs.hqdata.backends.hq_api import HQApiBackend, _hq_timestamp
from connect_labs.hqdata.models import HQSource
from connect_labs.hqdata.records import CaseRecord, FormRecord
from connect_labs.labs.integrations.commcare.api_client import CCHQAuthError, CommCareDataAccess


class Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def access():
    return CommCareDataAccess(None, "demo", cchq_access_token="tok")


def hq_form(form_id, **extra):
    return {
        "id": form_id,
        "app_id": "app1",
        "build_id": "b1",
        "archived": False,
        "received_on": "2026-11-01T10:00:00.000000Z",
        "server_modified_on": "2026-11-01T10:00:05.000000Z",
        "indexed_on": "2026-11-01T10:00:06.000000Z",
        "form": {"@xmlns": "http://x/1", "q": "a"},
        "metadata": {"username": "simon", "userID": "u1"},
        **extra,
    }


def test_forms_re_anchor_on_the_last_rows_indexed_on_like_commcare_export():
    pages = [
        Resp(
            {
                "objects": [
                    hq_form("f1", indexed_on="2026-11-01T10:00:01Z"),
                    hq_form("f2", indexed_on="2026-11-01T10:00:02Z"),
                ]
            }
        ),
        Resp(
            {
                "objects": [
                    hq_form("f2", indexed_on="2026-11-01T10:00:02Z"),
                    hq_form("f3", indexed_on="2026-11-01T10:00:03Z"),
                ]
            }
        ),
        Resp({"objects": [hq_form("f3", indexed_on="2026-11-01T10:00:03Z")]}),
    ]
    with (
        mock.patch.object(CommCareDataAccess, "FORM_PAGE_LIMIT", 2),
        mock.patch("httpx.get", side_effect=pages) as get,
    ):
        got = [f["id"] for f in access().iter_forms_since("2026-11-01T00:00:00", app_id="app1")]
    assert got == ["f1", "f2", "f2", "f3", "f3"]  # boundary rows repeat; the sync upserts
    sent = [c.kwargs["params"] for c in get.call_args_list]
    assert sent[0] == {
        "limit": 2,
        "order_by": "indexed_on",
        "indexed_on_start": "2026-11-01T00:00:00",
        "app_id": "app1",
        "include_archived": "true",
    }
    assert [p["indexed_on_start"] for p in sent] == [
        "2026-11-01T00:00:00",
        "2026-11-01T10:00:02",  # HQ sends ...Z but rejects it back (HTTP 400, read live), so the anchor is naive
        "2026-11-01T10:00:03",
    ]
    assert all("offset" not in p for p in sent)
    assert get.call_args_list[0].args[0].endswith("/a/demo/api/form/v1/")


def test_a_page_sharing_one_indexed_on_pages_by_offset_until_the_anchor_moves():
    same = "2026-11-01T10:00:02Z"
    same_naive = "2026-11-01T10:00:02"
    pages = [
        Resp({"objects": [hq_form("f1", indexed_on=same), hq_form("f2", indexed_on=same)]}),
        Resp({"objects": [hq_form("f1", indexed_on=same), hq_form("f2", indexed_on=same)]}),
        Resp({"objects": [hq_form("f3", indexed_on="2026-11-01T10:00:09Z")]}),
    ]
    with (
        mock.patch.object(CommCareDataAccess, "FORM_PAGE_LIMIT", 2),
        mock.patch("httpx.get", side_effect=pages) as get,
    ):
        list(access().iter_forms_since("2026-11-01T00:00:00"))
    sent = [c.kwargs["params"] for c in get.call_args_list]
    assert [(p["indexed_on_start"], p.get("offset")) for p in sent] == [
        ("2026-11-01T00:00:00", None),
        (same_naive, None),
        (same_naive, 2),
    ]


def test_a_form_without_indexed_on_stops_the_walk_instead_of_guessing():
    bad = hq_form("f1")
    del bad["indexed_on"]
    with (
        mock.patch.object(CommCareDataAccess, "FORM_PAGE_LIMIT", 1),
        mock.patch("httpx.get", return_value=Resp({"objects": [bad]})),
    ):
        with pytest.raises(ValueError, match="indexed_on"):
            list(access().iter_forms_since(None))


def test_a_first_sync_sends_no_start():
    with mock.patch("httpx.get", return_value=Resp({"meta": {}, "objects": []})) as get:
        list(access().iter_forms_since(None))
    assert "indexed_on_start" not in get.call_args.kwargs["params"]


def test_cases_use_the_v2_cursor_and_indexed_on_gte():
    pages = [
        Resp(
            {
                "matching_records": 2,
                "next": "https://www.commcarehq.org/a/demo/api/case/v2/?cursor=abc",
                "cases": [{"case_id": "c1"}],
            }
        ),
        Resp({"matching_records": 2, "cases": [{"case_id": "c2"}]}),
    ]
    with mock.patch("httpx.get", side_effect=pages) as get:
        a = access()
        a.base_url = "https://www.commcarehq.org"
        got = list(a.iter_cases_since("2026-11-01T00:00:00", case_type="child"))
    assert [c["case_id"] for c in got] == ["c1", "c2"]
    assert get.call_args_list[0].kwargs["params"] == {
        "limit": 5000,
        "indexed_on.gte": "2026-11-01T00:00:00",
        "case_type": "child",
    }


def test_a_failed_page_raises_instead_of_ending_the_walk_quietly():
    pages = [
        Resp({"objects": [hq_form("f1")]}),
        Resp({}, status=500),
    ]
    seen = []
    with mock.patch.object(CommCareDataAccess, "FORM_PAGE_LIMIT", 1), mock.patch("httpx.get", side_effect=pages):
        with pytest.raises(RuntimeError):
            for f in access().iter_forms_since(None):
                seen.append(f["id"])
    assert seen == ["f1"]


def test_a_rejected_token_raises_the_reauth_error():
    with (
        mock.patch("httpx.get", return_value=Resp({}, status=403)),
        mock.patch.object(CommCareDataAccess, "_refresh_token", return_value=False),
    ):
        with pytest.raises(CCHQAuthError):
            list(access().iter_forms_since(None))


def test_case_pagination_cannot_leave_the_configured_host():
    pages = [Resp({"next": "https://evil.example/a/demo/api/case/v2/?cursor=1", "cases": [{"case_id": "c1"}]})]
    with mock.patch("httpx.get", side_effect=pages):
        with pytest.raises(CCHQAuthError):
            list(access().iter_cases_since(None))


def test_form_record_reads_hqs_shape():
    rec = FormRecord.from_hq(hq_form("f1", archived=True))
    assert (rec.form_id, rec.xmlns, rec.app_id, rec.username, rec.user_id, rec.archived) == (
        "f1",
        "http://x/1",
        "app1",
        "simon",
        "u1",
        True,
    )
    assert rec.received_on == datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
    assert rec.indexed_on == datetime(2026, 11, 1, 10, 0, 6, tzinfo=timezone.utc)
    assert rec.form_json["form"]["q"] == "a"  # the whole object, so form.* paths keep working


def test_case_record_reads_hqs_shape():
    rec = CaseRecord.from_hq(
        {
            "case_id": "c1",
            "case_type": "child",
            "case_name": "N",
            "closed": True,
            "indexed_on": "2026-11-01T10:00:00.000000Z",
            "properties": {"a": "1"},
            "indices": {"parent": {"case_id": "p"}},
        }
    )
    assert rec.closed and rec.properties == {"a": "1"} and rec.indices["parent"]["case_id"] == "p"
    assert rec.indexed_on.tzinfo is not None


def test_backend_translates_sources_and_sends_hqs_timestamp_format():
    fake = mock.Mock()
    fake.iter_forms_since.return_value = iter([hq_form("f1")])
    backend = HQApiBackend(fake)
    source = HQSource(kind=HQSource.KIND_FORMS, app_id="app1", xmlns="")
    since = datetime(2026, 11, 1, 9, 0, 33, 250000, tzinfo=timezone.utc)
    out = list(backend.iter_records(source, since))
    assert isinstance(out[0], FormRecord)
    fake.iter_forms_since.assert_called_once_with("2026-11-01T09:00:33.250000", xmlns=None, app_id="app1")
    assert _hq_timestamp(since) == "2026-11-01T09:00:33.250000"


@pytest.mark.parametrize("stamp", ["2026-10-30T02:54:50.525767Z", "2026-10-30T02:54:50.525767+00:00"])
def test_the_anchor_never_carries_a_zone_because_hq_rejects_one(stamp):
    """Read live 2026-11-16: HQ answers 400 'Unknown date format' to indexed_on_start with Z or +00:00."""
    pages = [
        Resp({"objects": [hq_form("f1", indexed_on=stamp), hq_form("f2", indexed_on=stamp)]}),
        Resp({"objects": [hq_form("f3", indexed_on="2026-10-31T00:00:00Z")]}),
    ]
    with (
        mock.patch.object(CommCareDataAccess, "FORM_PAGE_LIMIT", 2),
        mock.patch("httpx.get", side_effect=pages) as get,
    ):
        list(access().iter_forms_since(None))
    second = get.call_args_list[1].kwargs["params"]["indexed_on_start"]
    assert second == "2026-10-30T02:54:50.525767"


class HttpResp(Resp):
    """A response whose error is the one httpx raises, carrying the status."""

    def raise_for_status(self):
        import httpx

        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=httpx.Request("GET", "https://x"), response=self
            )


def _cursor(**query):
    import base64
    from urllib.parse import urlencode

    return base64.b64encode(urlencode(query).encode()).decode()


def test_a_case_page_hq_fails_on_is_asked_for_again_smaller_at_the_same_place():
    """HQ answered one page of 5000 echis-niger cases with a 500 every time; the same place, smaller, goes through."""
    import base64
    from urllib.parse import parse_qs, urlsplit

    stuck = "/a/demo/api/case/v2/?cursor=" + _cursor(limit=5000, last_case_id="c1")
    pages = [
        HttpResp({"next": stuck, "cases": [{"case_id": "c1"}]}),
        HttpResp({}, status=500),
        HttpResp({}, status=500),
        HttpResp({"cases": [{"case_id": "c2"}]}),
    ]
    with mock.patch("httpx.get", side_effect=pages) as get:
        a = access()
        a.base_url = "https://www.commcarehq.org"
        got = [c["case_id"] for c in a.iter_cases_since(None)]
    assert got == ["c1", "c2"]
    asked = []
    for call in get.call_args_list[1:]:
        cursor = parse_qs(urlsplit(call.args[0]).query)["cursor"][0]
        asked.append(parse_qs(base64.b64decode(cursor).decode()))
    assert [q["limit"] for q in asked] == [["5000"], ["1000"], ["200"]]
    assert all(q["last_case_id"] == ["c1"] for q in asked)  # the same place in the walk each time


def test_a_case_page_that_fails_at_every_size_raises():
    import httpx

    with mock.patch("httpx.get", return_value=HttpResp({}, status=500)) as get:
        with pytest.raises(httpx.HTTPStatusError):
            list(access().iter_cases_since(None))
    assert [c.kwargs["params"]["limit"] for c in get.call_args_list] == [5000, 1000, 200, 40]


def test_a_failed_page_is_described_without_its_cursor():
    import httpx

    from connect_labs.hqdata.sync import describe_failure

    source = HQSource(kind=HQSource.KIND_CASES)
    exc = httpx.HTTPStatusError(
        "Server error", request=httpx.Request("GET", "https://x/?cursor=" + "A" * 300), response=HttpResp({}, 500)
    )
    text = describe_failure(source, exc)
    assert "HTTP 500" in text and "cases" in text and "cursor" not in text


def test_a_project_whose_cases_fail_says_which_and_why_without_a_url(db):
    import httpx

    from connect_labs.hqdata.models import HQProject
    from connect_labs.hqdata.sync import sync_project
    from connect_labs.hqdata.tests.test_sync import FakeBackend

    project = HQProject.objects.create(domain="demo")
    HQSource.objects.create(project=project, kind=HQSource.KIND_CASES)
    exc = httpx.HTTPStatusError(
        "x", request=httpx.Request("GET", "https://x/?cursor=AAAA"), response=HttpResp({}, 500)
    )
    with mock.patch("connect_labs.hqdata.sync.sync_source", side_effect=exc):
        sync_project(project, backend=FakeBackend([]))
    project.refresh_from_db()
    assert project.last_error.startswith("Cases: CommCare HQ answered HTTP 500") and "cursor" not in project.last_error

import logging

import httpx

from connect_labs.utils.log_scrub import scrub_url


def test_scrub_url_drops_query_and_ids():
    hq = "https://www.commcarehq.org/a/demo/api/case/v2"
    out = scrub_url(f"{hq}/0b5e8f2c-1a2b-4c3d-9e8f-123456789abc/?username=flw_amina&limit=10")
    assert out == f"{hq}/{{id}}/?…"
    assert "flw_amina" not in out


def test_httpx_record_is_scrubbed_before_any_handler(caplog):
    # caplog's handler sits on the root logger; the filter on the `httpx` logger
    # must already have rewritten the record by the time it propagates.
    with caplog.at_level(logging.INFO):
        logging.getLogger("httpx").info(
            'HTTP Request: %s %s "%s %d %s"',
            "GET",
            httpx.URL("https://connect.dimagi.com/export/opportunity/123/user_visits/?username=flw_amina"),
            "HTTP/1.1",
            200,
            "OK",
        )
    text = caplog.text
    assert "flw_amina" not in text
    assert "https://connect.dimagi.com/export/opportunity/{id}/user_visits/?…" in text
    assert "200 OK" in text

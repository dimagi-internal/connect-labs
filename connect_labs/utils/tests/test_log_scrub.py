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


def _httpx_line(caplog, url):
    with caplog.at_level(logging.INFO):
        logging.getLogger("httpx").info('HTTP Request: %s %s "%s %d %s"', "GET", httpx.URL(url), "HTTP/1.1", 200, "OK")
    return caplog.records[-1], caplog.text


def test_pagination_facts_survive_without_the_cursor_value(caplog):
    # The #1361 stampede tell (kill_stampede_triage.py) needs page_size and
    # whether a cursor was present -- never the cursor, which is a visit id.
    record, text = _httpx_line(
        caplog,
        "https://connect.dimagi.com/export/opportunity/2154/user_visits/?page_size=2500&last_id=1766518",
    )
    assert "[query: page_size=2500 paginated=true has_cursor=true]" in text
    assert "1766518" not in text and "2154" not in text
    assert (record.outbound_page_size, record.outbound_paginated, record.outbound_has_cursor) == (2500, True, True)


def test_first_page_has_no_cursor(caplog):
    _, text = _httpx_line(caplog, "https://connect.dimagi.com/export/opportunity/2154/user_visits/?page_size=2500")
    assert "[query: page_size=2500 paginated=true has_cursor=false]" in text


def test_non_pagination_query_values_never_reach_the_line(caplog):
    _, text = _httpx_line(caplog, "https://www.commcarehq.org/a/demo/api/case/v2/?username=flw_amina&case_id=abc")
    assert "flw_amina" not in text and "abc" not in text
    assert "[query: paginated=false has_cursor=false]" in text


def test_non_integer_page_size_is_not_reported(caplog):
    _, text = _httpx_line(caplog, "https://connect.dimagi.com/api/x/?page_size=flw_amina")
    assert "flw_amina" not in text
    assert "page_size=" not in text


def test_no_query_no_suffix(caplog):
    _, text = _httpx_line(caplog, "https://connect.dimagi.com/api/x/")
    assert "[query:" not in text

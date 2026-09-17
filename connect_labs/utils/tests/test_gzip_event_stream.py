"""Gzip must skip `text/event-stream`, and here is what happens when it doesn't.

Django's GZipMiddleware is installed only in `labs_aws`, so the one environment
that compresses is the one nobody tests against. That gap cost a live break:
#1902 made SSE bodies async, every SSE view on labs started delivering exactly
one event, and CI was green throughout.
"""

import asyncio
import gzip
import zlib

from django.http import JsonResponse, StreamingHttpResponse
from django.middleware.gzip import GZipMiddleware
from django.test import RequestFactory

from connect_labs.utils.gzip import GZipExceptEventStreamMiddleware

EVENTS = [f"data: event-{i}\n\n" for i in range(5)]


def _request():
    request = RequestFactory().get("/labs/whatever/stream/")
    request.META["HTTP_ACCEPT_ENCODING"] = "gzip"
    return request


async def _aevents():
    for event in EVENTS:
        yield event


def _sse_response():
    return StreamingHttpResponse(_aevents(), content_type="text/event-stream")


def _body(response):
    async def drain():
        return b"".join([c async for c in response.streaming_content])

    return asyncio.run(drain())


def _first_gzip_member(payload):
    """What a browser reads: the first member, and nothing after it.

    `gzip.decompress` follows every concatenated member, so using it here would
    hide the very defect this file is about.
    """
    return zlib.decompressobj(zlib.MAX_WBITS | 16).decompress(payload)


def test_stock_django_truncates_an_async_event_stream_to_its_first_event():
    """The defect, demonstrated — not asserted from the changelog.

    If a future Django makes the async path a single gzip stream, this test
    fails and the subclass can go.
    """
    response = GZipMiddleware(lambda r: None).process_response(_request(), _sse_response())
    assert response.headers.get("Content-Encoding") == "gzip", "Django stopped compressing this"

    payload = _body(response)
    assert _first_gzip_member(payload) == EVENTS[0].encode(), (
        "the first gzip member no longer holds exactly one event; the truncation "
        "this middleware exists to avoid may have changed shape"
    )
    # Every event IS in there — as separate members, which a browser will not read.
    assert gzip.decompress(payload) == "".join(EVENTS).encode()


def test_our_middleware_leaves_an_event_stream_alone():
    response = GZipExceptEventStreamMiddleware(lambda r: None).process_response(_request(), _sse_response())
    assert "Content-Encoding" not in response.headers
    assert _body(response).decode() == "".join(EVENTS)


def test_it_still_compresses_everything_else():
    """The point is to skip event streams, not to turn gzip off."""
    payload = {"rows": [{"id": i, "name": "a name that repeats"} for i in range(200)]}
    response = GZipExceptEventStreamMiddleware(lambda r: None).process_response(_request(), JsonResponse(payload))
    assert response.headers.get("Content-Encoding") == "gzip"
    assert len(response.content) < 2000, "the JSON body was not actually compressed"


def test_a_sync_event_stream_is_skipped_too():
    """The kill switch serves a sync body; it must not start being compressed
    just because it is the fallback path."""
    response = StreamingHttpResponse(iter(EVENTS), content_type="text/event-stream")
    out = GZipExceptEventStreamMiddleware(lambda r: None).process_response(_request(), out_response := response)
    assert out is out_response
    assert "Content-Encoding" not in out.headers

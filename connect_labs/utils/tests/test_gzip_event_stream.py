"""Gzip must skip `text/event-stream`.

Two reasons, and only one of them is still a defect.

WAS: on Django 5.2, a streaming body was compressed one way when sync
(`compress_sequence`, one gzip stream) and another when async
(`compress_string` PER CHUNK, a separate gzip member each). Browsers stop at
the end of the first member, so when #1902 made SSE bodies async every SSE view
on labs began delivering exactly one event. Django 6.0 fixed it --
`acompress_sequence` -- and `test_stock_django_...` below pins that, so a
regression or a downgrade is caught rather than rediscovered in production.

IS: an event stream exists to deliver each event the moment it happens, and
compression trades that for a ratio nobody asked for. That reason does not
expire with a Django version, which is why this middleware stays.

Worth keeping in view: GZipMiddleware is installed only in `labs_aws`, so the
one environment that compresses is the one nobody tests against. CI was green
throughout the break.
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


def test_stock_django_compresses_an_async_stream_as_one_gzip_stream():
    """Pins the upstream fix we now rely on, by driving stock Django.

    On 5.2 the first member held ONE event and a browser read no further, which
    is how every SSE view on labs came to deliver a single event. 6.0 compresses
    an async body with `acompress_sequence`, so the whole thing is one member.

    Asserted rather than assumed: if this ever goes back to a member per chunk,
    the middleware beside it stops being a latency preference and becomes
    load-bearing again, and whoever is here should know that from a red test.
    """
    response = GZipMiddleware(lambda r: None).process_response(_request(), _sse_response())
    assert response.headers.get("Content-Encoding") == "gzip", "Django stopped compressing this"

    payload = _body(response)
    assert _first_gzip_member(payload) == "".join(EVENTS).encode(), (
        "stock Django split an async stream across gzip members again — a browser "
        "reads only the first one, so every SSE view would truncate (5.2 behaviour, "
        "fixed in 6.0, see #1904)"
    )
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

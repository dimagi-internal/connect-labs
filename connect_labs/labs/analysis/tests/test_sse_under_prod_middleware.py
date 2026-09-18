"""SSE through the middleware production actually runs, which CI otherwise never does.

`GZipMiddleware` is installed only in `config/settings/labs_aws.py`. Tests run on
`config.settings.test`, so the one environment that compresses is the one nobody
tests against — and that gap is not hypothetical. It cost a live break: #1902
made SSE bodies async, Django 5.2 then compressed each chunk into its own gzip
member, browsers stopped reading after the first one, and every SSE view on labs
delivered exactly ONE event. CI was green the whole time.

So these tests assemble the response the way the prod stack does and assert the
property a browser depends on: the body a client receives carries every event.

They are deliberately about the WIRE, not about a view's logic. Any SSE view is
welcome here; what is being defended is the pipeline underneath all of them.
"""

import asyncio
import gzip
import zlib

import pytest
from django.http import StreamingHttpResponse
from django.test import RequestFactory

from connect_labs.labs.analysis.sse_streaming import stream_sse
from connect_labs.utils.gzip import GZipExceptEventStreamMiddleware

EVENTS = [f'data: {{"i": {i}}}\n\n' for i in range(50)]


def _sync_source():
    yield from EVENTS


def _prod_middleware_stack(response):
    """What labs_aws wraps a response in, as far as the body is concerned."""
    request = RequestFactory().get("/labs/anything/stream/")
    request.META["HTTP_ACCEPT_ENCODING"] = "gzip, deflate, br"
    return GZipExceptEventStreamMiddleware(lambda r: None).process_response(request, response)


def _drain(response):
    async def run():
        return b"".join([chunk async for chunk in response.streaming_content])

    return asyncio.run(run())


def _what_a_browser_reads(response, payload):
    """A browser decodes ONE gzip member. `gzip.decompress` reads them all, and
    using it here would hide exactly the defect this file exists for."""
    if response.headers.get("Content-Encoding") == "gzip":
        return zlib.decompressobj(zlib.MAX_WBITS | 16).decompress(payload)
    return payload


@pytest.mark.parametrize("chunk_size", [1, 100])
def test_every_event_reaches_the_client_through_the_prod_stack(chunk_size):
    """chunk_size 1 is the chat streams, 100 the bulk row streams — the gzip
    defect only showed at one of them, so both are checked."""
    body = stream_sse(_sync_source(), interval=30, chunk_size=chunk_size)
    response = _prod_middleware_stack(StreamingHttpResponse(body, content_type="text/event-stream"))

    payload = _drain(response)
    received = _what_a_browser_reads(response, payload).decode()
    assert received.count("data: ") == len(EVENTS), (
        f"a client reads {received.count('data: ')} of {len(EVENTS)} events through the prod "
        f"middleware stack at chunk_size={chunk_size}"
    )
    assert received == "".join(EVENTS)


def test_the_stack_does_not_compress_an_event_stream():
    """The guarantee the test above rests on. If this ever flips, the assertion
    above starts depending on Django's multi-member behaviour instead of on our
    own middleware, and would pass for the wrong reason."""
    response = _prod_middleware_stack(
        StreamingHttpResponse(stream_sse(_sync_source(), interval=30, chunk_size=1), content_type="text/event-stream")
    )
    assert "Content-Encoding" not in response.headers


def test_the_check_can_actually_fail():
    """Guards the guard. Compress the same body the way Django 5.2 did — one
    gzip member per chunk — and the browser-style read must come up short.

    Without this, a future where nothing compresses anything would leave the
    tests above passing while testing nothing.
    """

    async def per_chunk_members():
        async for item in stream_sse(_sync_source(), interval=30, chunk_size=1):
            yield gzip.compress(item.encode())

    response = StreamingHttpResponse(per_chunk_members(), content_type="text/event-stream")
    response.headers["Content-Encoding"] = "gzip"
    received = _what_a_browser_reads(response, _drain(response)).decode()
    assert received.count("data: ") == 1, "the browser-style read is not reading one member only"

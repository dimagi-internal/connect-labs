"""Event streams are compressed, and stay streams. Those pull against each other.

Both halves have been wrong in production within a day of each other, so both
are pinned here:

  * #1902/#1904 -- Django 5.2 compressed an ASYNC streaming body one gzip member
    per chunk; browsers stop after the first member, so every SSE view delivered
    exactly ONE event. Fixed upstream in Django 6.0 (`acompress_sequence`).
  * #1904 -- the fix for that skipped compression on event streams entirely,
    which threw away 4.8x on a 30.6 MB pipeline read to protect a latency
    property only the chat stream needs.

What replaces both: compress, but flush on a time bound. The tests below assert
the ratio is real, that a lone event does not sit in the compressor, and that a
browser can read the whole body -- one gzip member, via `zlib.decompressobj`,
because `gzip.decompress` follows every member and would read straight past the
defect that started this.
"""

import asyncio
import gzip
import json
import random
import time
import zlib

import pytest
from django.http import JsonResponse, StreamingHttpResponse
from django.test import RequestFactory

from connect_labs.utils.gzip import FLUSH_INTERVAL_SECONDS, StreamingAwareGZipMiddleware

EVENTS = [f'data: {{"i": {i}}}\n\n' for i in range(50)]
HEARTBEAT = ": heartbeat\n\n"


def _request(accept_encoding="gzip, deflate, br"):
    request = RequestFactory().get("/labs/anything/stream/")
    if accept_encoding is not None:
        request.META["HTTP_ACCEPT_ENCODING"] = accept_encoding
    return request


def _sse(body, content_type="text/event-stream"):
    return StreamingHttpResponse(body, content_type=content_type)


def _through(response, request=None):
    return StreamingAwareGZipMiddleware(lambda r: None).process_response(request or _request(), response)


def _drain(response):
    body = response.streaming_content
    if hasattr(body, "__aiter__"):

        async def run():
            return [chunk async for chunk in body]

        return asyncio.run(run())
    return list(body)


def _as_a_browser_reads(response, chunks):
    """One gzip member. `gzip.decompress` reads them all and would hide a split."""
    payload = b"".join(chunks)
    if response.headers.get("Content-Encoding") == "gzip":
        return zlib.decompressobj(_WBITS).decompress(payload)
    return payload


_WBITS = zlib.MAX_WBITS | 16


async def _aevents(events=EVENTS):
    for e in events:
        yield e


# --- it is compressed, and completely --------------------------------------


def test_an_event_stream_is_compressed():
    response = _through(_sse(_aevents()))
    assert response.headers.get("Content-Encoding") == "gzip"
    assert "Accept-Encoding" in response.headers.get("Vary", "")


def test_every_event_survives_the_round_trip():
    response = _through(_sse(_aevents()))
    assert _as_a_browser_reads(response, _drain(response)).decode() == "".join(EVENTS)


def test_the_whole_body_is_one_gzip_member():
    """The #1902 defect in its own right: a member per chunk decodes to the
    first event only, and every SSE view on labs did exactly that for a day."""
    response = _through(_sse(_aevents()))
    chunks = _drain(response)
    single_member = zlib.decompressobj(_WBITS).decompress(b"".join(chunks))
    assert single_member.decode() == "".join(EVENTS)
    assert gzip.decompress(b"".join(chunks)) == single_member  # i.e. there is only one


def test_the_compression_is_actually_worth_doing():
    """A ratio assertion, because 'it is compressed' is satisfied by a gzip
    header around uncompressed data. Realistic rows, not repeated ones -- 20,000
    identical events compress 800x and would prove nothing."""
    random.seed(7)
    rows = [
        "data: "
        + json.dumps(
            {
                "case_id": f"{random.getrandbits(64):016x}",
                "username": random.choice(["amara", "chidi", "ngozi", "tunde"]) + str(random.randint(1, 400)),
                "visit_date": f"2026-{random.randint(1, 9):02d}-{random.randint(1, 28):02d}",
                "weight": round(random.uniform(1.5, 5.5), 3),
                "status": random.choice(["approved", "pending", "rejected"]),
            }
        )
        + "\n\n"
        for _ in range(4000)
    ]
    raw = sum(len(r.encode()) for r in rows)
    response = _through(_sse(_aevents(rows)))
    out = sum(len(c) for c in _drain(response))
    assert raw / out > 3.0, f"only {raw / out:.1f}x — the flush is costing more than it should"


# --- and it is still a stream ----------------------------------------------


def test_a_lone_event_does_not_sit_in_the_compressor():
    """THE ONE THAT MATTERS FOR HEARTBEATS.

    A heartbeat is ~14 bytes sent into silence to stop the ALB dropping an idle
    connection at 60s. Django's own `compress_sequence` emits nothing until
    ~180 KB has accumulated, so a heartbeat would never reach the client in time
    and the connection would die of the timeout it exists to prevent.

    Asserted on TIME, not on arrival. An earlier version of this test only
    checked that the heartbeat showed up eventually -- which it does even with
    no flush at all, in the final chunk after the stream closes -- so it passed
    against the very behaviour it was written to forbid.
    """
    silence = FLUSH_INTERVAL_SECONDS * 10

    async def heartbeat_then_long_silence():
        yield HEARTBEAT
        await asyncio.sleep(silence)
        yield HEARTBEAT

    response = _through(_sse(heartbeat_then_long_silence()))

    async def time_to_first_heartbeat():
        started = time.monotonic()
        received = b""
        async for chunk in response.streaming_content:
            received += chunk
            if HEARTBEAT.encode() in zlib.decompressobj(_WBITS).decompress(received):
                return time.monotonic() - started
        return time.monotonic() - started

    elapsed = asyncio.run(time_to_first_heartbeat())
    assert elapsed < silence / 2, (
        f"the heartbeat took {elapsed:.2f}s to clear the compressor, with the producer "
        f"silent for {silence:.2f}s — it is being held until the stream closes, which is "
        f"how an idle connection gets dropped at 60s"
    )


def test_events_arrive_before_the_producer_finishes():
    """Progressive delivery, which is the whole point of the transport."""
    produced = []

    async def slow():
        for i in range(5):
            produced.append(i)
            yield f"data: {i}\n\n"
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS * 1.5)

    response = _through(_sse(slow()))

    async def read_until_first_event():
        received = b""
        async for chunk in response.streaming_content:
            received += chunk
            decoded = zlib.decompressobj(_WBITS).decompress(received)
            if b"data: 0" in decoded:
                return len(produced)
        return len(produced)

    seen_after = asyncio.run(read_until_first_event())
    assert seen_after < 5, f"nothing reached the client until the producer had made all 5 ({seen_after})"


# --- everything else is untouched ------------------------------------------


def test_a_sync_event_stream_is_compressed_too():
    """The kill-switch path (`LABS_SSE_ASYNC_STREAMING=False`) serves a sync body."""
    response = _through(_sse(iter(EVENTS)))
    assert response.headers.get("Content-Encoding") == "gzip"
    assert _as_a_browser_reads(response, _drain(response)).decode() == "".join(EVENTS)


def test_a_client_that_does_not_accept_gzip_gets_none():
    response = _through(_sse(_aevents()), request=_request(accept_encoding="identity"))
    assert "Content-Encoding" not in response.headers
    assert b"".join(_drain(response)).decode() == "".join(EVENTS)


def test_an_already_encoded_response_is_left_alone():
    response = _sse(_aevents())
    response.headers["Content-Encoding"] = "br"
    assert _through(response).headers["Content-Encoding"] == "br"


def test_content_length_is_dropped():
    """It cannot survive compression, and a wrong one truncates the response."""
    response = _sse(_aevents())
    response.headers["Content-Length"] = "999"
    assert "Content-Length" not in _through(response).headers


def test_ordinary_responses_still_go_through_django():
    payload = {"rows": [{"id": i, "name": "a name that repeats"} for i in range(200)]}
    response = _through(JsonResponse(payload))
    assert response.headers.get("Content-Encoding") == "gzip"
    assert len(response.content) < 2000, "the JSON body was not actually compressed"


@pytest.mark.parametrize("content_type", ["application/json", "text/html; charset=utf-8"])
def test_a_non_sse_stream_is_left_to_django(content_type):
    """Only `text/event-stream` takes the flushing path; everything else keeps
    whatever Django decides, including its own streaming behaviour."""
    response = _through(_sse(_aevents(), content_type=content_type))
    assert response.headers.get("Content-Encoding") == "gzip"
    assert gzip.decompress(b"".join(_drain(response))).decode() == "".join(EVENTS)

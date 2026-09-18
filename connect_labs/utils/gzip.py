"""Gzip on the way out, including event streams -- which need a flush to stay streams."""

import time
import zlib

from django.middleware.gzip import GZipMiddleware, re_accepts_gzip
from django.utils.cache import patch_vary_headers

SSE_CONTENT_TYPE = "text/event-stream"

# gzip wrapper (15 window bits + 16), matching what GZipMiddleware emits.
_GZIP_WBITS = zlib.MAX_WBITS | 16
_COMPRESS_LEVEL = 6

# Longest a produced event may sit inside the compressor before it is pushed to
# the client. Everything about this number is a trade between ratio and latency,
# and it was measured on 20,000 realistic pipeline rows:
#
#     flush every 1000 events   4.78x   (+0.0% over never flushing)
#     flush every  100 events   4.74x   (+0.8%)
#     flush every   10 events   4.38x   (+9.2%)
#     flush every    1 event    3.19x   (+50%)
#
# A time bound rather than an event count, because the two streams that matter
# sit at opposite ends: a bulk read emits far faster than this, so its flushes
# coalesce and the ratio stays at the top of that table, while a chat delta or a
# 20-second heartbeat arrives alone and is pushed out within the bound.
FLUSH_INTERVAL_SECONDS = 0.2


class StreamingAwareGZipMiddleware(GZipMiddleware):
    """Django's GZipMiddleware, taught to compress a stream without stalling it.

    It was briefly called `GZipExceptEventStreamMiddleware`, which described the
    previous behaviour -- skip event streams entirely -- and would now be the
    opposite of what it does. It compresses them, flushing often enough that
    they are still streams.

    WHY NOT JUST LET DJANGO DO IT. `compress_sequence` writes each item into a
    GzipFile and yields only `if data:` -- there is no flush -- so zlib holds
    everything until its internal buffer fills. Measured: the first byte reaches
    the client after ~180 KB of input. For a 30 MB pipeline read that is 0.6% of
    the payload and nobody would notice; for a chat reply the whole answer
    arrives at once, and for a HEARTBEAT it is fatal. Heartbeats are ~14 bytes
    sent into silence to stop the ALB dropping an idle connection at 60s. Buffer
    them and the connection dies -- the exact failure the heartbeat exists to
    prevent, reintroduced by compressing it.

    WHY NOT SKIP THEM, WHICH IS WHAT THIS DID YESTERDAY. Because the bulk
    pipeline stream is 30.6 MB and compresses 4.8x. Skipping compression to
    protect a latency property that only the chat stream needs threw away ~24 MB
    on every read of it. That was the wrong trade, made without measuring.

    Non-streaming responses and non-SSE streams are untouched -- they go to
    Django's implementation, which is correct for them.
    """

    def process_response(self, request, response):
        if response.headers.get("Content-Type", "").startswith(SSE_CONTENT_TYPE):
            return self._compress_event_stream(request, response)
        return super().process_response(request, response)

    def _compress_event_stream(self, request, response):
        # The same guards Django applies, in the same order. An event stream is
        # always "worth" compressing regardless of length -- the length is not
        # known, and the short ones are short enough not to care either way.
        if response.has_header("Content-Encoding"):
            return response
        patch_vary_headers(response, ("Accept-Encoding",))
        if not re_accepts_gzip.search(request.headers.get("accept-encoding", "")):
            return response

        body = response.streaming_content
        if hasattr(body, "__aiter__"):
            response.streaming_content = _acompress_flushing(body, FLUSH_INTERVAL_SECONDS)
        else:
            response.streaming_content = _compress_flushing(body, FLUSH_INTERVAL_SECONDS)

        response.headers["Content-Encoding"] = "gzip"
        # Unknowable once compressed, and wrong if left behind.
        del response.headers["Content-Length"]
        return response


def _flusher(interval):
    """Shared state machine, so the sync and async paths cannot drift apart.

    Returns `(feed, finish)`. `feed(chunk)` gives back the bytes to send now,
    which is the compressed chunk plus a sync flush whenever `interval` has
    passed -- and ALWAYS on the first chunk, so time-to-first-byte is not
    charged the interval on top of however long the producer took.
    """
    compressor = zlib.compressobj(_COMPRESS_LEVEL, zlib.DEFLATED, _GZIP_WBITS)
    last_flush = [0.0]  # 0.0, not now(): the first chunk always flushes

    def feed(chunk):
        data = compressor.compress(chunk)
        now = time.monotonic()
        if now - last_flush[0] >= interval:
            data += compressor.flush(zlib.Z_SYNC_FLUSH)
            last_flush[0] = now
        return data

    def finish():
        return compressor.flush()

    return feed, finish


def _compress_flushing(stream, interval):
    feed, finish = _flusher(interval)
    for chunk in stream:
        data = feed(chunk)
        if data:
            yield data
    tail = finish()
    if tail:
        yield tail


async def _acompress_flushing(stream, interval):
    feed, finish = _flusher(interval)
    async for chunk in stream:
        data = feed(chunk)
        if data:
            yield data
    tail = finish()
    if tail:
        yield tail

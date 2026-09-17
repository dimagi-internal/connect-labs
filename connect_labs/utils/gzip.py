"""Gzip on the way out — but never an event stream."""

from django.middleware.gzip import GZipMiddleware

SSE_CONTENT_TYPE = "text/event-stream"


class GZipExceptEventStreamMiddleware(GZipMiddleware):
    """Django's GZipMiddleware, minus `text/event-stream`.

    Compressing an event stream is wrong twice over.

    THE SHARP EDGE, now blunted -- kept because it explains the shape of this
    file. On Django 5.2 a streaming body was compressed one way when sync
    (`compress_sequence()`, ONE gzip stream) and another when async
    (`compress_string()` PER CHUNK, a complete independent gzip member each).
    Multi-member gzip is legal and some decoders read it, but a browser stops at
    the end of the first member: the client saw event 1, concluded the response
    was finished, and the server went on producing into a connection nobody was
    reading. **Django 6.0 fixed this** with `acompress_sequence`, and the test
    beside this pins the fix so a regression is caught rather than rediscovered.

    That is exactly what happened when #1902 made SSE bodies async. Every SSE
    view on labs delivered precisely one event -- `pipeline-rows/stream` 2
    events became 1, `pipeline-data/stream` 100 events became 1 -- while the
    server logged each pipeline starting normally. It did not show in CI or
    locally because GZipMiddleware is installed only in `labs_aws`, so the one
    environment that compresses is the one environment nobody tests against.

    THE DULL EDGE is why this middleware survives the fix: an event stream
    exists to deliver each event the moment it happens, and compression trades
    that for a ratio nobody asked for.

    Everything else still compresses -- the microplans footprint endpoint ships
    ~1 MB of GeoJSON that gzips 80-90%, which is why the middleware is here.
    """

    def process_response(self, request, response):
        if response.headers.get("Content-Type", "").startswith(SSE_CONTENT_TYPE):
            return response
        return super().process_response(request, response)

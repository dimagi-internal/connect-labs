"""Gzip on the way out — but never an event stream."""

from django.middleware.gzip import GZipMiddleware

SSE_CONTENT_TYPE = "text/event-stream"


class GZipExceptEventStreamMiddleware(GZipMiddleware):
    """Django's GZipMiddleware, minus `text/event-stream`.

    Compressing an event stream is wrong twice over.

    THE SHARP EDGE. Django compresses a streaming body one way when it is sync
    and another when it is async. Sync gets `compress_sequence()` -- ONE gzip
    stream across every chunk. Async gets `compress_string()` PER CHUNK, so
    every chunk is a complete, independent gzip member. Multi-member gzip is
    legal and some decoders read it, but a browser stops at the end of the
    first member: the client sees event 1, concludes the response is finished,
    and the server goes on producing into a connection nobody is reading.

    That is exactly what happened when #1902 made SSE bodies async. Every SSE
    view on labs delivered precisely one event -- `pipeline-rows/stream` 2
    events became 1, `pipeline-data/stream` 100 events became 1 -- while the
    server logged each pipeline starting normally. It did not show in CI or
    locally because GZipMiddleware is installed only in `labs_aws`, so the one
    environment that compresses is the one environment nobody tests against.

    THE DULL EDGE, which stands on its own: an event stream exists to deliver
    each event the moment it happens, and compression trades that for a ratio
    nobody asked for. Even fixed, gzipping SSE would be a poor trade.

    Everything else still compresses -- the microplans footprint endpoint ships
    ~1 MB of GeoJSON that gzips 80-90%, which is why the middleware is here.
    """

    def process_response(self, request, response):
        if response.headers.get("Content-Type", "").startswith(SSE_CONTENT_TYPE):
            return response
        return super().process_response(request, response)

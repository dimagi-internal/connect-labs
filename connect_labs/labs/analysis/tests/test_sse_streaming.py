"""Tests for AnalysisPipelineSSEMixin.stream_pipeline_events' EVENT_ERROR handling.

Before this fix, stream_pipeline_events had NO branch for EVENT_ERROR at all
(only EVENT_STATUS/EVENT_DOWNLOAD/EVENT_RESULT). When pipeline.stream_analysis's
own try/except caught something and yielded (EVENT_ERROR, {...}), the for-loop
here matched none of its branches and silently did nothing — self._pipeline_result
stayed None (reported by callers as row_count=0) and self._pipeline_from_cache
could even end up True purely as a side effect of an earlier, unrelated
"Checking ...-level cache..." status message containing the word "cache".

Reproduced live: the Ward Progress Tracker (workflow 5266, program-owned)
showed "0 work areas, from_cache: true" for every opportunity with zero visible
error anywhere in the pipeline metadata — even after two unrelated fixes
(auth-error retry-then-raise, raise_on_http_error) that should have surfaced
a real failure. The failure WAS happening and WAS being converted to
EVENT_ERROR by pipeline.py — it just never survived this mixin.
"""

from unittest.mock import MagicMock

import pytest

from connect_labs.labs.analysis.pipeline import EVENT_DOWNLOAD, EVENT_ERROR, EVENT_RESULT, EVENT_STATUS
from connect_labs.labs.analysis.sse_streaming import AnalysisPipelineSSEMixin


class _Mixin(AnalysisPipelineSSEMixin):
    """Bare instantiation — the mixin's __init__ only sets instance state."""


def _consume(mixin, stream, **kwargs):
    return list(mixin.stream_pipeline_events(stream, **kwargs))


class TestStreamPipelineEventsErrorHandling:
    def test_error_event_silently_dropped_by_default(self):
        """Preserves the historical behavior for callers that have never
        handled a raised exception here (labs/admin, configurable_ui,
        mbw_monitoring, custom_analysis/rutf, custom_analysis/kmc)."""
        mixin = _Mixin()
        exc = ValueError("boom")
        stream = iter(
            [
                (EVENT_STATUS, {"message": "Checking visit-level cache..."}),
                (EVENT_ERROR, {"message": "boom", "exception": exc}),
            ]
        )

        events = _consume(mixin, stream)

        assert mixin._pipeline_result is None
        # The exact symptom that made this bug so hard to diagnose: an
        # unrelated earlier status message containing "cache" flips this to
        # True even though nothing was actually served from cache.
        assert mixin._pipeline_from_cache is True
        assert len(events) == 1  # only the status event was forwarded

    def test_error_event_raises_original_exception_when_opted_in(self):
        mixin = _Mixin()
        exc = ValueError("boom")
        stream = iter(
            [
                (EVENT_STATUS, {"message": "Fetching cases..."}),
                (EVENT_ERROR, {"message": "boom", "exception": exc}),
            ]
        )

        with pytest.raises(ValueError) as excinfo:
            _consume(mixin, stream, raise_on_error=True)

        assert excinfo.value is exc  # exact same object, not a rewrapped copy

    def test_preserves_exception_type_for_downstream_isinstance_checks(self):
        """The caller (workflow/views.py) does isinstance(e, CCHQAuthError) to
        decide whether to show the yellow 'Authorize CommCare HQ' banner vs a
        generic red error. Re-raising the original object (not a new
        RuntimeError wrapping its message) is what makes that still work."""

        class FakeCCHQAuthError(Exception):
            def __init__(self, message, domain):
                super().__init__(message)
                self.domain = domain

        mixin = _Mixin()
        exc = FakeCCHQAuthError("auth rejected", domain="connect-chc-ng-isodaf")
        stream = iter([(EVENT_ERROR, {"message": str(exc), "exception": exc})])

        with pytest.raises(FakeCCHQAuthError) as excinfo:
            _consume(mixin, stream, raise_on_error=True)

        assert excinfo.value.domain == "connect-chc-ng-isodaf"

    def test_raises_runtime_error_when_no_exception_object_present(self):
        """Defensive fallback for an EVENT_ERROR payload missing "exception"
        (e.g. hand-constructed by a future caller that forgets the key)."""
        mixin = _Mixin()
        stream = iter([(EVENT_ERROR, {"message": "something broke"})])

        with pytest.raises(RuntimeError, match="something broke"):
            _consume(mixin, stream, raise_on_error=True)

    def test_success_path_unaffected(self):
        """A normal successful stream (no error) behaves identically
        regardless of the raise_on_error flag."""
        mixin = _Mixin()
        fake_result = MagicMock(rows=[{"a": 1}])

        for raise_on_error in (False, True):
            stream = iter(
                [
                    (EVENT_STATUS, {"message": "Working..."}),
                    (EVENT_DOWNLOAD, {"rows": 10, "total": 100}),
                    (EVENT_RESULT, fake_result),
                ]
            )
            events = _consume(mixin, stream, raise_on_error=raise_on_error)
            assert mixin._pipeline_result is fake_result
            assert len(events) == 2  # status + download; RESULT breaks the loop

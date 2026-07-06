"""Tests for fetch_cchq_cases_as_visit_dicts' error-visibility wiring.

Only covers the one behavior this module owns beyond CommCareDataAccess
itself: opting into raise_on_http_error=True so a non-auth HTTP failure
(e.g. a 404/500, not 401/403 — which CommCareDataAccess already raises
CCHQAuthError for regardless of this flag) surfaces as a real exception
instead of a silent empty result. See
connect_labs/labs/integrations/commcare/tests/test_api_client.py for the
underlying fetch_cases behavior this wires into.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from connect_labs.labs.analysis.backends.sql.cchq_cases_fetcher import fetch_cchq_cases_as_visit_dicts
from connect_labs.labs.analysis.config import DataSourceConfig


def _data_source():
    return DataSourceConfig(type="cchq_cases", case_type="work-area")


class TestFetchCchqCasesAsVisitDicts:
    def test_wires_raise_on_http_error_true(self):
        """The one thing this wrapper must get right: pass
        raise_on_http_error=True so callers see real failures instead of a
        silently-empty work-area list (indistinguishable from "this
        opportunity genuinely has zero work areas")."""
        request = MagicMock()
        mock_client = MagicMock()
        mock_client.check_token_valid.return_value = True
        mock_client.verify_hq_access.return_value = True
        mock_client.fetch_cases.return_value = []

        with (
            patch(
                "connect_labs.labs.analysis.backends.sql.cchq_cases_fetcher.fetch_opportunity_metadata",
                return_value={"cc_domain": "connect-chc-ng-isodaf"},
            ),
            patch(
                "connect_labs.labs.analysis.backends.sql.cchq_cases_fetcher.CommCareDataAccess",
                return_value=mock_client,
            ),
        ):
            fetch_cchq_cases_as_visit_dicts(
                request=request, data_source=_data_source(), access_token="t", opportunity_id=1982
            )

        mock_client.fetch_cases.assert_called_once_with(case_type="work-area", raise_on_http_error=True)

    def test_non_auth_http_error_propagates(self):
        """End-to-end: a real fetch_cases 404/500 (not swallowed, because of
        the flag above) reaches this function's caller as a normal exception,
        not a silently-empty result."""
        request = MagicMock()
        mock_client = MagicMock()
        mock_client.check_token_valid.return_value = True
        mock_client.verify_hq_access.return_value = True
        mock_client.fetch_cases.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=MagicMock(status_code=404)
        )

        with (
            patch(
                "connect_labs.labs.analysis.backends.sql.cchq_cases_fetcher.fetch_opportunity_metadata",
                return_value={"cc_domain": "connect-chc-ng-isodaf"},
            ),
            patch(
                "connect_labs.labs.analysis.backends.sql.cchq_cases_fetcher.CommCareDataAccess",
                return_value=mock_client,
            ),
        ):
            with pytest.raises(httpx.HTTPStatusError):
                fetch_cchq_cases_as_visit_dicts(
                    request=request, data_source=_data_source(), access_token="t", opportunity_id=1982
                )

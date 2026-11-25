"""Every CCHQHeadlessError carries CCHQ_HEADLESS_MARK.

The background visit-cache warm sees a failed HQ pipeline only as ``metadata.error``
text, and tells "this viewer has no CommCare HQ login" from a real failure by that
phrase (visit_cache._failed_for_want_of_cchq_login). Reword a message without it and the
preview goes back to saying "could not be loaded" on every Retry (run 29804, 2026-11-23).
"""

import pytest

from connect_labs.labs.analysis.backends.sql.cchq_cases_fetcher import fetch_cchq_cases_as_visit_dicts
from connect_labs.labs.analysis.backends.sql.cchq_fetcher import iter_cchq_forms_as_visit_dicts
from connect_labs.labs.analysis.config import DataSourceConfig
from connect_labs.labs.integrations.commcare.api_client import CCHQ_HEADLESS_MARK, CCHQHeadlessError


def test_the_forms_fetcher_says_it():
    with pytest.raises(CCHQHeadlessError) as raised:
        next(iter_cchq_forms_as_visit_dicts(None, DataSourceConfig(type="cchq_forms"), "tok", 765))
    assert CCHQ_HEADLESS_MARK in str(raised.value)


def test_every_auth_error_carries_the_reauth_mark():
    """The warm tells a refused login from a real failure by CCHQ_REAUTH_MARK alone."""
    from connect_labs.labs.integrations.commcare.api_client import CCHQ_REAUTH_MARK, CCHQAuthError

    assert CCHQ_REAUTH_MARK in str(CCHQAuthError("CommCare HQ said 403", status_code=403))
    worded = "CommCare HQ rejected case fetch with HTTP 401. User needs to re-authorize CommCare access."
    assert str(CCHQAuthError(worded)) == worded


def test_the_cases_fetcher_says_it():
    with pytest.raises(CCHQHeadlessError) as raised:
        fetch_cchq_cases_as_visit_dicts(None, DataSourceConfig(type="cchq_cases", case_type="work_area"), "tok", 765)
    assert CCHQ_HEADLESS_MARK in str(raised.value)

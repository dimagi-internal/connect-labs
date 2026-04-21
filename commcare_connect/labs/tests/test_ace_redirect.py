"""The ACE Web SPA is served by a separate nginx container; the ALB routes
`/ace/*` to it. Without this redirect, a bare `/ace` (no trailing slash) falls
through to Django and 404s.
"""

import pytest
from django.test import Client


@pytest.mark.django_db
def test_bare_ace_redirects_to_slash():
    resp = Client().get("/ace", follow=False)
    assert resp.status_code == 301, resp.status_code
    assert resp["Location"] == "/ace/"

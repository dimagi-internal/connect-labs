import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_ping_url_resolves():
    assert reverse("labs:pages:ping") == "/labs/p/ping/"

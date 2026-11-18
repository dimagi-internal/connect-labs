import pytest
from django.test import RequestFactory

from connect_labs.utils.safe_redirect import referer_or

pytestmark = pytest.mark.usefixtures("_labs_host")


@pytest.fixture
def _labs_host(settings):
    settings.ALLOWED_HOSTS = ["labs.example"]


def _req(referer=None, secure=True):
    headers = {"HTTP_REFERER": referer} if referer is not None else {}
    return RequestFactory().get("/x/", secure=secure, HTTP_HOST="labs.example", **headers)


def test_a_page_on_this_site_is_returned_to():
    assert referer_or(_req("https://labs.example/labs/a/?b=1"), "/d/") == "https://labs.example/labs/a/?b=1"


def test_another_site_is_not_redirected_to():
    for bad in ["https://evil.example/", "//evil.example/x", "http://labs.example/downgrade", "javascript:alert(1)"]:
        assert referer_or(_req(bad), "/d/") == "/d/", bad


def test_no_referer_is_the_default():
    assert referer_or(_req(), "/d/") == "/d/"

import pytest
from django.templatetags.static import static

pytestmark = pytest.mark.django_db


def test_favicon_ico_redirects_to_the_labs_icon(client):
    response = client.get("/favicon.ico")

    assert response.status_code == 302
    assert response.url == static("images/favicons/favicon.ico")


def test_login_page_links_the_labs_favicon(client):
    response = client.get("/labs/login/")

    assert response.status_code == 200
    assert static("images/favicons/favicon.svg") in response.content.decode()

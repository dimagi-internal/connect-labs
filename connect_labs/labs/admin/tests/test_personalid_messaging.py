"""Labs Admin › PersonalID messaging: Dimagi staff enter the client id and secret connect-id issued.

Every name and secret here is invented (this repository is public).
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from connect_labs.labs.tests.test_settings import LABS_SETTINGS
from connect_labs.messaging import credentials
from connect_labs.messaging.models import PersonalIDCredentials, TransportEvent

pytestmark = pytest.mark.django_db

URL = "labs_admin:personalid_messaging"
SECRET = "s3cr3t-value-never-shown-9f2c"


@pytest.fixture(autouse=True)
def no_env(settings):
    settings.PERSONALID_MESSAGING_CLIENT_ID = ""
    settings.PERSONALID_MESSAGING_CLIENT_SECRET = ""
    settings.LABS_PUBLIC_URL = "https://labs.example"


@pytest.fixture
def staff():
    return get_user_model().objects.create_user(username="s", email="s@dimagi.com", password="pw")


@pytest.fixture
def outsider():
    return get_user_model().objects.create_user(username="o", email="o@partner.example", password="pw")


@override_settings(**LABS_SETTINGS)
def test_outsiders_get_403_and_cannot_post(client, outsider):
    client.force_login(outsider)
    assert client.get(reverse(URL)).status_code == 403
    assert client.post(reverse(URL), {"client_id": "x", "secret": "y"}).status_code == 403
    assert not PersonalIDCredentials.objects.exists()


@override_settings(**LABS_SETTINGS)
def test_anonymous_is_sent_to_login(client):
    response = client.get(reverse(URL))
    assert response.status_code == 302 and "/labs/login/" in response.url


@override_settings(**LABS_SETTINGS)
def test_the_page_shows_no_source_and_the_four_urls(client, staff):
    client.force_login(staff)
    html = client.get(reverse(URL)).content.decode()
    assert credentials.personalid_credentials().source == credentials.NONE
    assert "None: labs cannot send" in html
    for name in ("key", "delivery", "consent", "callback"):
        assert f"https://labs.example/messaging/personalid/{name}/" in html
    assert "none yet" in html


@override_settings(**LABS_SETTINGS)
def test_saving_puts_the_pair_in_force_and_never_shows_the_secret(client, staff):
    client.force_login(staff)
    response = client.post(reverse(URL), {"client_id": "labs-server-1", "secret": SECRET}, follow=True)
    assert response.status_code == 200
    html = response.content.decode()
    assert SECRET not in html
    assert credentials.personalid_credentials() == ("labs-server-1", SECRET, credentials.DB)
    assert "Labs Admin (this page)" in html and "Secret set on" in html and "s@dimagi.com" in html
    assert SECRET not in client.get(reverse(URL)).content.decode()


@override_settings(**LABS_SETTINGS)
def test_an_empty_secret_keeps_the_saved_one(client, staff):
    client.force_login(staff)
    client.post(reverse(URL), {"client_id": "labs-server-1", "secret": SECRET})
    client.post(reverse(URL), {"client_id": "labs-server-2", "secret": ""})
    assert credentials.personalid_credentials() == ("labs-server-2", SECRET, credentials.DB)


@override_settings(**LABS_SETTINGS)
def test_a_first_save_needs_a_secret(client, staff):
    client.force_login(staff)
    response = client.post(reverse(URL), {"client_id": "labs-server-1", "secret": ""}, follow=True)
    assert "Enter the secret" in response.content.decode()
    assert not PersonalIDCredentials.objects.exists()


@override_settings(**LABS_SETTINGS)
def test_clearing_falls_back_to_env(client, staff, settings):
    settings.PERSONALID_MESSAGING_CLIENT_ID = "env-client"
    settings.PERSONALID_MESSAGING_CLIENT_SECRET = "env-secret"
    client.force_login(staff)
    client.post(reverse(URL), {"client_id": "labs-server-1", "secret": SECRET})
    assert credentials.personalid_credentials().source == credentials.DB
    response = client.post(reverse(URL), {"clear": "1"}, follow=True)
    assert credentials.personalid_credentials().source == credentials.ENV
    assert "Environment settings" in response.content.decode()


@override_settings(**LABS_SETTINGS)
def test_the_last_signed_post_is_shown(client, staff):
    TransportEvent.objects.create(kind="consent", channel_id="c1", payload={})
    client.force_login(staff)
    html = client.get(reverse(URL)).content.decode()
    assert "none yet" not in html and "(consent)" in html


@override_settings(**LABS_SETTINGS)
def test_the_admin_index_links_here(client, staff):
    client.force_login(staff)
    assert reverse(URL) in client.get(reverse("labs_admin:index")).content.decode()

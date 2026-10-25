"""The labs sign-in page says what a link was for -- a data space -- without naming it."""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

SENT = "Sign in to open the data space you were sent."


def test_a_link_to_a_data_space_says_so(client):
    response = client.get(reverse("labs:login"), {"next": "/labs/data/space/12/"})
    body = response.content.decode()
    assert SENT in body
    # It still comes back to the space after sign-in.
    assert "next=/labs/data/space/12/" in body


def test_it_never_names_the_space(client):
    from connect_labs.labs.integrations.connect.oauth_views import labs_login_page

    # The page is drawn from the URL alone: nothing about the space is read, so its name
    # cannot reach someone not yet signed in.
    assert "DataSpace" not in labs_login_page.__code__.co_names


@pytest.mark.parametrize("next_url", ["/labs/data/", "/labs/overview/", "/labs/data/space/", "/x/labs/data/space/1/"])
def test_other_links_do_not(client, next_url):
    response = client.get(reverse("labs:login"), {"next": next_url})
    assert SENT not in response.content.decode()

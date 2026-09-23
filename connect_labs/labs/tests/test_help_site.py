"""The Labs help site: the MkDocs build of user_docs/, served behind the Labs login."""

import pytest
from django.http import Http404
from django.urls import reverse

from connect_labs.labs.help_site import help_site


@pytest.fixture
def site(tmp_path, settings):
    (tmp_path / "index.html").write_text("<h1>Labs Help</h1>")
    (tmp_path / "reports-with-claude").mkdir()
    (tmp_path / "reports-with-claude" / "index.html").write_text("<h1>Reports with Claude</h1>")
    (tmp_path / "search").mkdir()
    (tmp_path / "search" / "search_index.json").write_text("{}")
    settings.HELP_SITE_DIR = tmp_path
    return tmp_path


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create(username="reader", email="reader@dimagi.com")


@pytest.mark.django_db
def test_requires_login(client, site):
    response = client.get(reverse("labs:docs_help"))
    assert response.status_code == 302
    assert "/labs/login/" in response["Location"]


@pytest.mark.django_db
def test_serves_the_home_page(client, site, user):
    client.force_login(user)
    response = client.get(reverse("labs:docs_help"))
    assert response.status_code == 200
    assert b"Labs Help" in b"".join(response.streaming_content)


@pytest.mark.django_db
def test_serves_a_page_directory_with_its_index(client, site, user):
    client.force_login(user)
    response = client.get("/labs/docs/help/reports-with-claude/")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")


@pytest.mark.django_db
def test_directory_without_trailing_slash_redirects(client, site, user):
    """MkDocs links are relative, so a page served without its trailing slash
    resolves every link one level too high."""
    client.force_login(user)
    response = client.get("/labs/docs/help/reports-with-claude")
    assert response.status_code == 302
    assert response["Location"] == "/labs/docs/help/reports-with-claude/"


@pytest.mark.django_db
def test_serves_assets_with_their_type(client, site, user):
    client.force_login(user)
    response = client.get("/labs/docs/help/search/search_index.json")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"


@pytest.mark.django_db
def test_missing_page_is_404(client, site, user):
    client.force_login(user)
    assert client.get("/labs/docs/help/no-such-page/").status_code == 404


@pytest.mark.django_db
def test_cannot_escape_the_site_root(client, site, user):
    (site.parent / "secret.txt").write_text("nope")
    client.force_login(user)
    for url in ("/labs/docs/help/../secret.txt", "/labs/docs/help/%2e%2e/secret.txt"):
        assert client.get(url).status_code in (400, 404)


@pytest.mark.django_db
def test_view_refuses_a_path_outside_the_root(rf, site, user):
    """Middleware already rejects `..` in a URL; the view must not rely on that."""
    (site.parent / "secret.txt").write_text("nope")
    request = rf.get("/labs/docs/help/x")
    request.user = user
    with pytest.raises(Http404):
        help_site(request, path="../secret.txt")


@pytest.mark.django_db
def test_unbuilt_site_says_so(client, tmp_path, settings, user):
    settings.HELP_SITE_DIR = tmp_path / "absent"
    client.force_login(user)
    response = client.get(reverse("labs:docs_help"))
    assert response.status_code == 503
    assert b"mkdocs build" in response.content

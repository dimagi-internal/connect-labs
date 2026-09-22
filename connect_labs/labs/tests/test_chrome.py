"""Per-page header options: the labs context selector and the Pulse widget.

The contract worth pinning is the defaults. Every existing page must keep the
context selector without doing anything, and no page gets the Pulse widget
without asking -- including a page rendered without the context processor,
where "missing" must read as "default", not as "off".
"""

from __future__ import annotations

import pytest
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse

from connect_labs.labs.chrome import DEFAULT, PageChrome, chrome_context, page_chrome

SELECTOR = "labsContextSelector()"
WIDGET = 'id="pulse-widget"'


def _view(request):
    return HttpResponse("ok")


class TestTheDecorator:
    def test_records_the_pages_choice_on_the_request(self):
        request = RequestFactory().get("/")
        page_chrome(labs_context=False, pulse_widget=True)(_view)(request)
        assert request.page_chrome == PageChrome(labs_context=False, pulse_widget=True)

    def test_an_unknown_option_fails_where_it_is_written(self):
        """A typo must not quietly render the defaults."""
        with pytest.raises(TypeError):
            page_chrome(pulse_widgit=True)

    def test_keeps_the_views_name_for_urls_and_tracebacks(self):
        assert page_chrome()(_view).__name__ == "_view"

    def test_the_context_processor_falls_back_to_the_defaults(self):
        assert chrome_context(RequestFactory().get("/")) == {"page_chrome": DEFAULT}
        assert DEFAULT.labs_context is True
        assert DEFAULT.pulse_widget is False


@pytest.mark.django_db
class TestTheHeader:
    @pytest.fixture
    def request_as(self, django_user_model):
        def build(chrome=None):
            request = RequestFactory().get("/")
            request.user = django_user_model.objects.create_user(username=f"u{id(chrome)}", password="x")
            request.session = {}
            if chrome is not None:
                request.page_chrome = chrome
            return request

        return build

    def test_by_default_the_context_selector_shows_and_the_widget_does_not(self, request_as):
        html = render_to_string("layouts/header.html", request=request_as())
        assert SELECTOR in html
        assert WIDGET not in html

    def test_a_page_can_hide_the_selector_and_show_the_widget(self, request_as):
        html = render_to_string(
            "layouts/header.html", request=request_as(PageChrome(labs_context=False, pulse_widget=True))
        )
        assert SELECTOR not in html
        assert WIDGET in html
        assert reverse("pulse:display", args=["nightmap"]) in html

    def test_without_the_context_processor_the_selector_still_shows(self, request_as):
        """Rendered with a bare context, `page_chrome` is missing -- which must
        mean the default, not a header that quietly lost its selector."""
        request = request_as()
        html = render_to_string("layouts/header.html", {"request": request, "user": request.user})
        assert SELECTOR in html
        assert WIDGET not in html


@pytest.mark.django_db
class TestTheMarketplace:
    def test_opts_out_of_the_selector_and_into_the_widget(self, client, django_user_model):
        client.force_login(django_user_model.objects.create_user(username="staff", password="x"))
        for name in ("marketplace:home", "marketplace:rounds", "marketplace:network", "marketplace:unmatched"):
            html = client.get(reverse(name)).content.decode()
            assert SELECTOR not in html, name
            assert WIDGET in html, name

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

from connect_labs.labs.chrome import DEFAULT, NOT_USED, STARTED_FROM, PageChrome, chrome_context, page_chrome

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

    @pytest.mark.parametrize("state", ["not-used", 1, 0, None])
    def test_an_unknown_labs_context_state_fails_where_it_is_written(self, state):
        with pytest.raises(ValueError):
            page_chrome(labs_context=state)

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


OPP_CONTEXT = {
    "opportunity_id": 2166,
    "opportunity": {"id": 2166, "name": "KMC - UG - PIPN - P1", "visit_count": 8363},
}


@pytest.mark.django_db
class TestAPageThatDoesNotUseTheContext:
    """A page that reads its own data says so, and the selector stops looking applied."""

    @pytest.fixture
    def header(self, django_user_model):
        def render(chrome=None):
            request = RequestFactory().get("/")
            request.user = django_user_model.objects.create_user(username=f"h{id(chrome)}", password="x")
            request.session = {}
            request.labs_context = OPP_CONTEXT
            if chrome is not None:
                request.page_chrome = chrome
            return render_to_string("layouts/header.html", request=request)

        return render

    def test_a_normal_page_shows_the_selection_as_applied(self, header):
        html = header()
        assert SELECTOR in html  # the per-path filters, as before
        assert 'data-testid="context-button"' in html
        assert "Opp:" in html and "KMC - UG - PIPN - P1" in html
        assert "context-not-used" not in html
        assert "context-started-from" not in html

    def test_a_page_declaring_not_used_shows_the_muted_state(self, header):
        html = header(PageChrome(labs_context=NOT_USED))
        assert 'data-testid="context-not-used"' in html
        assert "not used on this page" in html
        assert "is kept for other pages" in html
        # The selection is not presented as applied -- named only in the tooltip.
        assert 'data-testid="context-button"' not in html
        assert "Opp:" not in html
        # The menu still opens, and Apply sets the context for other pages on any path
        # (the picker's per-path filters would otherwise turn Apply into a clear).
        assert "labsContextSelector({ anyPath: true })" in html and "Apply Selection" in html
        assert 'data-testid="context-not-used-note"' in html

    def test_not_used_without_a_selection_does_not_ask_for_one(self, django_user_model):
        request = RequestFactory().get("/")
        request.user = django_user_model.objects.create_user(username="h-none", password="x")
        request.session = {}
        request.page_chrome = PageChrome(labs_context=NOT_USED)
        html = render_to_string("layouts/header.html", request=request)
        assert 'data-testid="context-not-used"' in html
        assert "Select Context" not in html

    def test_a_page_started_from_the_context_says_so(self, header):
        html = header(PageChrome(labs_context=STARTED_FROM))
        assert 'data-testid="context-button"' in html and "KMC - UG - PIPN - P1" in html
        assert "Used to start this page's selection" in html
        assert "context-not-used" not in html

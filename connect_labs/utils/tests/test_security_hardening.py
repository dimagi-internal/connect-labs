"""#1032: the bounded items from the 2026-07-29 security audit.

Covers item L (CCZ-download SSRF), item H (the two XSS sinks + CSP), item J's
code half (auto-logoff, HSTS), and item F's switch.
"""

import pytest
from django.test import RequestFactory, override_settings

from connect_labs.utils.csp import ContentSecurityPolicyMiddleware, build_policy
from connect_labs.utils.hq_hosts import is_allowed_hq_url, reject_disallowed_hq_url


class TestHqHostAllowlist:
    """Item L: `hq_server_url` comes off a LabsRecord, not configuration, and was
    fetched with no restriction — whoever can write that record chose a host the
    labs server would connect to from inside the VPC."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.commcarehq.org",
            "https://commcarehq.org/a/dom/apps/api/download_ccz/",
            "https://india.commcarehq.org",
        ],
    )
    def test_real_hq_hosts_are_allowed(self, url):
        assert is_allowed_hq_url(url)
        assert reject_disallowed_hq_url(url) is None

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",  # cloud metadata
            "https://169.254.169.254/",
            "http://localhost:8000/",
            "https://internal.vpc.local/admin/",
            "https://evil.example.com/a/d/apps/api/download_ccz/",
            "file:///etc/passwd",
            "",
            None,
        ],
    )
    def test_everything_else_is_refused(self, url):
        assert not is_allowed_hq_url(url)
        assert reject_disallowed_hq_url(url) is not None

    def test_plain_http_on_an_allowed_host_is_still_refused(self):
        """An allowlisted host over http is still not somewhere to send
        credentials-adjacent traffic."""
        assert not is_allowed_hq_url("http://www.commcarehq.org/")

    def test_a_lookalike_host_is_not_a_substring_match(self):
        """Substring checks are the classic allowlist bug."""
        assert not is_allowed_hq_url("https://commcarehq.org.evil.com/")
        assert not is_allowed_hq_url("https://notcommcarehq.org/")

    @override_settings(ALLOWED_HQ_HOSTS=["hq.internal.example"])
    def test_the_allowlist_is_configurable(self):
        assert is_allowed_hq_url("https://hq.internal.example/x")
        assert not is_allowed_hq_url("https://www.commcarehq.org")

    def test_download_ccz_refuses_before_making_a_request(self, monkeypatch):
        """The guard has to run before httpx.get — refusing after the request has
        already left is not a fix."""
        import connect_labs.labs.admin.app_data_access as mod

        called = []
        monkeypatch.setattr(mod.httpx, "get", lambda *a, **k: called.append(a) or None)

        dao = mod.AppDownloaderDataAccess.__new__(mod.AppDownloaderDataAccess)
        assert mod.AppDownloaderDataAccess.download_ccz(dao, "http://169.254.169.254", "dom", "app1") is None
        assert called == []


class TestContentSecurityPolicy:
    """Item H: there was no CSP anywhere. Every XSS fix so far has been per-sink,
    which requires finding the sink first."""

    def test_the_policy_names_the_directives_that_matter(self):
        policy = build_policy()
        for directive in ("default-src", "script-src", "object-src 'none'", "base-uri 'self'", "frame-ancestors"):
            assert directive in policy

    def test_it_ships_report_only(self):
        """An enforcing policy written blind breaks pages, and a CSP that breaks
        pages gets reverted — which is worse than not having one."""
        mw = ContentSecurityPolicyMiddleware(lambda r: _html_response())
        response = mw(RequestFactory().get("/"))

        assert "Content-Security-Policy-Report-Only" in response
        assert "Content-Security-Policy" not in response.headers or response.get("Content-Security-Policy") is None

    @override_settings(CSP_REPORT_ONLY=False)
    def test_it_can_be_flipped_to_enforcing(self):
        mw = ContentSecurityPolicyMiddleware(lambda r: _html_response())
        response = mw(RequestFactory().get("/"))
        assert "Content-Security-Policy" in response

    def test_non_html_responses_are_left_alone(self):
        from django.http import JsonResponse

        mw = ContentSecurityPolicyMiddleware(lambda r: JsonResponse({"ok": True}))
        response = mw(RequestFactory().get("/api/"))
        assert "Content-Security-Policy-Report-Only" not in response

    @override_settings(CSP_ENABLED=False)
    def test_it_can_be_turned_off_entirely(self):
        mw = ContentSecurityPolicyMiddleware(lambda r: _html_response())
        assert "Content-Security-Policy-Report-Only" not in mw(RequestFactory().get("/"))

    @override_settings(CSP_REPORT_URI="/csp-report/")
    def test_a_report_uri_is_included_when_configured(self):
        assert "report-uri /csp-report/" in build_policy()


def _html_response():
    from django.http import HttpResponse

    return HttpResponse("<html></html>", content_type="text/html; charset=utf-8")


class TestFundReportSinksAreSanitized:
    """Item H, the two concrete sinks: marked does not sanitize, and its output
    went straight to innerHTML. The markdown is LLM-generated."""

    def _template(self):
        from pathlib import Path

        import connect_labs

        return (Path(connect_labs.__file__).parent / "templates" / "funder_dashboard" / "fund_detail.html").read_text()

    def test_dompurify_is_loaded_before_marked(self):
        html = self._template()
        assert "purify.min.js" in html
        assert html.index("purify.min.js") < html.index("marked.min.js")

    def test_the_markdown_sink_goes_through_the_sanitizer(self):
        html = self._template()
        assert "marked.parse(fullText)" not in html, "the raw sink must be gone"
        assert "renderReportMarkdown(fullText)" in html
        assert "DOMPurify.sanitize(" in html

    def test_error_strings_are_not_interpolated_into_html(self):
        """`err.message` and the server's `evt.error` both came off the wire."""
        html = self._template()
        assert "+ err.message + '</p>'" not in html
        assert "+ (evt.error || 'Report generation failed') + '</p>'" not in html
        assert "p.textContent = message" in html


class TestSessionAndTransportHardening:
    """Item J, the half that is code rather than AWS console."""

    def _labs_aws(self):
        from pathlib import Path

        import config

        return (Path(config.__file__).parent / "settings" / "labs_aws.py").read_text()

    def test_hsts_meets_the_preload_floor(self):
        """3600 was below the 31536000 the preload list requires, so
        SECURE_HSTS_PRELOAD advertised eligibility the max-age denied."""
        src = self._labs_aws()
        assert "default=31536000" in src
        assert "SECURE_HSTS_SECONDS = 3600" not in src

    def test_sessions_expire_on_idle_not_after_a_fortnight(self):
        src = self._labs_aws()
        assert "SESSION_COOKIE_AGE" in src
        # Rolling expiry is what makes it idle rather than absolute.
        assert "SESSION_SAVE_EVERY_REQUEST" in src


@pytest.mark.django_db
class TestLabsOnlyDomainSwitch:
    """Item F: the isolation predicate fails open on empty allowed_domains.

    Not flipped by default — doing so alone would revoke every partner's access
    to every labs-only opp that hasn't named their domain, while item E (raw PHI
    to Drive), the thing that makes it dangerous, stays open. The audit's own
    guidance is to fix E and F together.
    """

    def _opp_and_partner(self):
        from connect_labs.labs.synthetic.models import SyntheticOpportunity
        from connect_labs.users.models import User

        opp = SyntheticOpportunity.objects.create(opportunity_id=10500, labs_only=True, allowed_domains=[])
        user = User.objects.create(username="partner", email="someone@partner.example")
        return opp, user

    def test_default_behaviour_is_unchanged(self):
        opp, user = self._opp_and_partner()
        assert opp.is_accessible_to(user) is True

    @override_settings(LABS_ONLY_REQUIRE_EXPLICIT_DOMAINS=True)
    def test_the_switch_makes_it_fail_closed(self):
        opp, user = self._opp_and_partner()
        assert opp.is_accessible_to(user) is False

    @override_settings(LABS_ONLY_REQUIRE_EXPLICIT_DOMAINS=True)
    def test_dimagi_users_are_unaffected_by_the_switch(self):
        from connect_labs.users.models import User

        opp, _ = self._opp_and_partner()
        assert opp.is_accessible_to(User.objects.create(username="d", email="x@dimagi.com")) is True

    def test_an_explicit_domain_list_still_gates(self):
        from connect_labs.labs.synthetic.models import SyntheticOpportunity
        from connect_labs.users.models import User

        opp = SyntheticOpportunity.objects.create(
            opportunity_id=10501, labs_only=True, allowed_domains=["@partner.example"]
        )
        assert opp.is_accessible_to(User.objects.create(username="p1", email="a@partner.example")) is True
        assert opp.is_accessible_to(User.objects.create(username="p2", email="a@other.example")) is False

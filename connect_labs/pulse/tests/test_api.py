"""Read API.

The behaviour most worth protecting: the server, not the page, decides whether
anything may be called LIVE. A display that decides for itself will show a
green badge over data that stopped arriving days ago — the single worst thing
this system could do in front of a funder.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.pulse import ingest
from connect_labs.pulse.models import PulseEvent, PulseIngestHealth, PulseOpportunity, PulseScalar, PulseWork


def make_event(vid, *, field_ts=None, status="approved", usd="0.70", flagged=False, country="NG"):
    now = timezone.now()
    ts = field_ts or now
    return PulseEvent.objects.create(
        connect_visit_id=vid,
        opportunity_id=765,
        field_ts=ts,
        sync_ts=ts + timedelta(minutes=9),
        lat=11.03,
        lon=7.63,
        country=country,
        status=status,
        flagged=flagged,
        flag_type="duration" if flagged else "",
        service_slug="mbw",
        worker_hash="985770f1bf2079f58119",
        usd_to_worker=usd if status == "approved" else None,
    )


@pytest.fixture
def populated(db, settings, django_user_model):
    # A configured poller is part of a working system, not an extra: without
    # one there is no ingest, and the API says so instead of badging LIVE.
    django_user_model.objects.create(username="poller-account")
    settings.PULSE_POLLER_USERNAME = "poller-account"
    PulseOpportunity.objects.create(
        opportunity_id=765, name="Mother Baby Wellness (Nigeria)", lifetime_visit_count=120351, is_active=True
    )
    PulseScalar.objects.create(key="scope", value={"opportunities": 494, "lifetime_visits": 1647855, "programs": 108})
    for i in range(1, 11):
        make_event(i, field_ts=timezone.now() - timedelta(hours=i))
    make_event(50, status="rejected", usd=None)
    make_event(51, flagged=True)
    ingest.rebuild_rollups()


@pytest.mark.django_db
class TestSummary:
    def test_returns_scope_and_totals(self, client, populated):
        data = client.get(reverse("pulse:api_summary")).json()
        assert data["scope"]["lifetime_visits"] == 1647855
        assert data["stored"]["events"] == 12
        assert data["by_status"]["approved"] == 11
        assert data["by_status"]["rejected"] == 1

    def test_exposes_labels_so_cards_need_no_hardcoded_copy(self, client, populated):
        labels = client.get(reverse("pulse:api_summary")).json()["labels"]
        assert labels["countries"]["NG"] == "Nigeria"
        assert labels["flags"]["duration"] == "form filled too fast"

    def test_summary_carries_no_pii(self, client, populated):
        body = client.get(reverse("pulse:api_summary")).content.decode()
        assert "entity_name" not in body
        assert "phone" not in body


@pytest.mark.django_db
class TestIngestHonesty:
    def test_live_not_ok_when_ingest_never_ran(self, client, populated):
        data = client.get(reverse("pulse:api_summary")).json()
        assert data["ingest"]["live_ok"] is False
        assert "never" in data["ingest"]["message"].lower()

    def test_live_ok_after_recent_success(self, client, populated):
        ingest.record_success("tail")
        ingest.record_success("cheap")
        state = client.get(reverse("pulse:api_summary")).json()["ingest"]
        assert state["live_ok"] is True
        assert state["poller"] == "poller-account"

    def test_names_the_account_it_polls_as(self, client, populated):
        """Scope follows the poller's org membership, so a wrong account rescales
        every figure on screen. Prod proved it: the numbers came out ~5x low and
        nothing errored. Naming the account on the page makes that visible from
        the display rather than only from arithmetic nobody does."""
        state = client.get(reverse("pulse:api_summary")).json()["ingest"]
        assert state["poller"] == "poller-account"
        assert state["poller_error"] == ""

    def test_unconfigured_poller_cannot_be_badged_live(self, client, populated, settings):
        """No poller means no further ingest, so the last data received is all
        there will ever be — the badge must drop immediately rather than wait
        for staleness to accumulate."""
        settings.PULSE_POLLER_USERNAME = ""
        ingest.record_success("tail")
        ingest.record_success("cheap")

        state = client.get(reverse("pulse:api_summary")).json()["ingest"]
        assert state["live_ok"] is False
        assert state["poller"] == ""
        assert "no pulse poller configured" in state["poller_error"].lower()
        assert "poller" in state["message"].lower()

    def test_stale_ingest_refuses_to_claim_live(self, client, populated):
        """The failure that matters: the poller's refresh token died hours ago
        and nothing arrived since, but the page would still badge itself LIVE."""
        ingest.record_success("tail")
        health = PulseIngestHealth.objects.get(tier="tail")
        health.last_success_at = timezone.now() - timedelta(hours=5)
        health.save()

        state = client.get(reverse("pulse:api_summary")).json()["ingest"]
        assert state["live_ok"] is False
        assert "not live" in state["message"].lower()
        assert state["staleness_seconds"] > 3600

    def test_one_dead_tier_makes_the_whole_display_not_live(self, client, populated):
        ingest.record_success("tail")
        PulseIngestHealth.objects.create(tier="cheap")  # never succeeded
        assert client.get(reverse("pulse:api_summary")).json()["ingest"]["live_ok"] is False

    def test_events_endpoint_also_reports_ingest_state(self, client, populated):
        """Cards that only poll events must be able to tell they're stale too."""
        assert "ingest" in client.get(reverse("pulse:api_events")).json()


def make_work(key, *, service="mbw", country="NG", status="approved", worker="1.00", org="0.50"):
    return PulseWork.objects.create(
        work_key=f"{key:0>64}",
        opportunity_id=765,
        service_slug=service,
        country=country,
        status=status,
        created_ts=timezone.now(),
        usd_to_worker=worker,
        usd_to_org=org,
    )


@pytest.mark.django_db
class TestMoneyTotals:
    """Money out the door means BOTH streams.

    The works spine records a worker payout and an org payout on every unit of
    approved work. A headline built from ``usd_to_worker`` alone understates
    what verified delivery moved by the whole org share — on real data a
    comparable amount of money, not a rounding error — so every money figure
    the display leads with must be the sum, with the split still visible.
    """

    def test_headline_is_workers_plus_orgs(self, client, populated):
        """The two streams are ADDITIVE, not nested.

        Verified against Connect's own source rather than assumed: the worker
        figure is `approved_count * payment_unit.amount` and the org figure is
        `approved_count * payment_unit.org_amount` — separate fields — and
        Connect's invoice generator bills `total_amount_usd = flw_usd +
        org_usd`. Had they been nested, this sum would double-count and the
        headline would be wrong in the safest-looking way possible.
        """
        make_work(1, worker="1.00", org="0.50")
        make_work(2, worker="2.00", org="1.00")

        m = client.get(reverse("pulse:api_summary")).json()["money"]
        assert m["to_workers"] == 3.0
        assert m["to_orgs"] == 1.5
        assert m["total_paid"] == 4.5

    def test_the_split_survives_beside_the_total(self, client, populated):
        """The total must never replace the split.

        `to_orgs` is only populated for *managed* opportunities, so the total
        covers a subset differently from the worker figure. A funder reading one
        blended number could not tell how much reached the worker, which is the
        claim this screen exists to make."""
        make_work(1, worker="1.00", org="0.50")

        m = client.get(reverse("pulse:api_summary")).json()["money"]
        assert m["to_workers"] and m["to_orgs"] and m["total_paid"]

    def test_cost_per_service_has_an_all_in_figure(self, client, populated):
        make_work(1, worker="1.00", org="0.50")
        make_work(2, worker="1.00", org="0.50")
        make_work(3, status="pending", worker="0.00", org="0.00")

        m = client.get(reverse("pulse:api_summary")).json()["money"]
        assert m["approved_works"] == 2
        assert m["usd_per_approved_work"] == 1.0
        assert m["total_per_approved_work"] == 1.5

    def test_service_breakdown_carries_both_streams(self, client, populated):
        make_work(1, service="mbw", worker="1.00", org="0.50")
        make_work(2, service="kmc", worker="0.10", org="4.00")

        rows = {r["service"]: r for r in client.get(reverse("pulse:api_summary")).json()["money"]["by_service"]}
        assert rows["mbw"]["usd"] == 1.0
        assert rows["mbw"]["usd_org"] == 0.5
        assert rows["mbw"]["usd_total"] == 1.5
        assert rows["kmc"]["usd_total"] == 4.1
        assert rows["kmc"]["total_rate"] == 4.1

    def test_breakdowns_rank_on_the_total_not_the_worker_share(self, client, populated):
        """A service can be org-heavy (KMC's payment units span visits). Ranking
        on the worker share alone would bury it below smaller programmes."""
        make_work(1, service="mbw", country="NG", worker="1.00", org="0.00")
        make_work(2, service="kmc", country="UG", worker="0.10", org="4.00")

        data = client.get(reverse("pulse:api_summary")).json()["money"]
        assert [r["service"] for r in data["by_service"]] == ["kmc", "mbw"]
        assert [r["country"] for r in data["by_country"]] == ["UG", "NG"]

    def test_country_coverage_note_reconciles_against_the_total(self, client, populated):
        make_work(1, country="NG", worker="1.00", org="0.50")
        make_work(2, country="", worker="2.00", org="1.00")  # country never resolved

        cov = client.get(reverse("pulse:api_summary")).json()["money"]["by_country_unattributed"]
        assert cov["usd_total"] == 3.0
        assert cov["usd_total_share"] == pytest.approx(1.5 / 4.5)


@pytest.mark.django_db
class TestEvents:
    def test_returns_positional_rows_with_a_field_map(self, client, populated):
        data = client.get(reverse("pulse:api_events")).json()
        assert data["fields"][0] == "visit_id"
        assert len(data["events"]) == 12
        assert data["cursor"] == 51

    def test_since_cursor_returns_only_newer(self, client, populated):
        data = client.get(reverse("pulse:api_events"), {"since": 10}).json()
        ids = [row[0] for row in data["events"]]
        assert ids == [50, 51]

    def test_events_carry_no_worker_identity(self, client, populated):
        data = client.get(reverse("pulse:api_events")).json()
        worker_idx = data["fields"].index("worker")
        # Truncated hash only — never a name, never the full identifier.
        assert all(len(row[worker_idx] or "") <= 6 for row in data["events"])


@pytest.mark.django_db
class TestReplay:
    def test_window_is_selected_on_field_time(self, client, populated):
        """Selecting on arrival time while pacing on field time makes a window
        span far wider than its label claims — the prototype's 'last 48h'
        actually covered nine days because of the offline-sync tail."""
        data = client.get(reverse("pulse:api_replay"), {"hours": 6}).json()
        assert data["window"]["basis"] == "field_ts"
        assert data["window"]["hours"] == 6

    def test_window_excludes_events_outside_it(self, client, populated):
        make_event(900, field_ts=timezone.now() - timedelta(days=9))
        data = client.get(reverse("pulse:api_replay"), {"hours": 48}).json()
        assert 900 not in [row[0] for row in data["events"]]

    def test_events_are_ordered_by_field_time(self, client, populated):
        data = client.get(reverse("pulse:api_replay"), {"hours": 72}).json()
        ts_idx = data["fields"].index("field_ts")
        stamps = [row[ts_idx] for row in data["events"]]
        assert stamps == sorted(stamps)

    def test_truncation_is_declared(self, client, populated):
        """Silent truncation reads as 'that's all there was'."""
        data = client.get(reverse("pulse:api_replay"), {"hours": 72, "limit": 3}).json()
        assert data["sampled"] is True
        assert len(data["events"]) == 3

    def test_a_capped_window_is_sampled_across_not_cut_at_the_head(self, client, db):
        """Slicing an ordered queryset returns the window's *head*.

        On prod this made a longer window show less: 336h returned 2000 rows
        spanning 12.8h, 94% from one country, because whichever programme
        submitted first monopolised the head. Four of the eight countries with
        delivery never appeared, so the map read as one country at any zoom.
        """
        now = timezone.now()
        # Two countries, interleaved in time: NG early, KE late. A head-slice
        # would return only NG; a sample must reach both ends.
        for i in range(60):
            make_event(2000 + i, field_ts=now - timedelta(hours=47 - (i // 2)), country="NG")
        for i in range(60):
            make_event(3000 + i, field_ts=now - timedelta(hours=20 - (i // 4)), country="KE")

        data = client.get(reverse("pulse:api_replay"), {"hours": 48, "limit": 20}).json()
        assert data["sampled"] is True
        assert data["sample_stride"] > 1
        assert data["matched"] > len(data["events"])

        ts_idx = data["fields"].index("field_ts")
        cty_idx = data["fields"].index("country")
        stamps = [r[ts_idx] for r in data["events"]]
        span_hours = (max(stamps) - min(stamps)) / 3600
        assert span_hours > 20, f"sample covers only {span_hours:.1f}h of a 48h window"
        assert {"NG", "KE"} <= {r[cty_idx] for r in data["events"]}, "sample missed a country entirely"

    def test_sampling_is_deterministic_so_replay_does_not_reshuffle(self, client, populated):
        """The viewer polls repeatedly; a re-sampled window would make points
        appear and vanish between frames."""
        now = timezone.now()
        for i in range(50):
            make_event(4000 + i, field_ts=now - timedelta(hours=i % 40))

        a = client.get(reverse("pulse:api_replay"), {"hours": 48, "limit": 10}).json()
        b = client.get(reverse("pulse:api_replay"), {"hours": 48, "limit": 10}).json()
        assert [r[0] for r in a["events"]] == [r[0] for r in b["events"]]

    def test_an_uncapped_window_is_neither_sampled_nor_truncated(self, client, populated):
        data = client.get(reverse("pulse:api_replay"), {"hours": 72}).json()
        assert data["sampled"] is False
        assert data["sample_stride"] == 1
        assert data["truncated"] is False


@pytest.mark.django_db
class TestPublicAccess:
    def test_unknown_token_is_404(self, client):
        assert client.get(reverse("pulse:public", args=["nope"])).status_code == 404

    def test_revoked_token_is_404_and_indistinguishable_from_unknown(self, client, django_user_model):
        from connect_labs.pulse.views import mint_public_token

        user = django_user_model.objects.create(username="jj")
        token = mint_public_token(user, label="A funder")
        assert client.get(reverse("pulse:public", args=[token.token])).status_code == 200

        token.revoked = True
        token.save()
        revoked = client.get(reverse("pulse:public", args=[token.token]))
        unknown = client.get(reverse("pulse:public", args=["definitely-not-a-token"]))
        assert revoked.status_code == unknown.status_code == 404

    def test_public_page_sets_noindex(self, client, django_user_model):
        from connect_labs.pulse.views import mint_public_token

        user = django_user_model.objects.create(username="jj2")
        token = mint_public_token(user)
        response = client.get(reverse("pulse:public", args=[token.token]))
        assert "noindex" in response["X-Robots-Tag"]

    def test_tokens_are_unguessable(self, django_user_model):
        from connect_labs.pulse.views import mint_public_token

        user = django_user_model.objects.create(username="jj3")
        tokens = {mint_public_token(user).token for _ in range(5)}
        assert len(tokens) == 5
        assert all(len(t) >= 24 for t in tokens)

    def test_authenticated_display_requires_login(self, client):
        response = client.get(reverse("pulse:display", args=["nightmap"]))
        assert response.status_code in (302, 403)


@pytest.mark.django_db
class TestOperatorIndex:
    """The operator page, where a wrong poller has to be catchable by eye."""

    def test_names_the_poller_and_never_on_the_public_page(self, client, populated, django_user_model):
        from connect_labs.pulse.views import mint_public_token

        operator = django_user_model.objects.create(username="operator")
        client.force_login(operator)
        body = client.get(reverse("pulse:index")).content.decode()
        assert "poller-account" in body

        # The account we poll as is an operational detail, not something a
        # funder holding a public link should ever be shown.
        client.logout()
        token = mint_public_token(operator, label="A funder")
        public = client.get(reverse("pulse:public", args=[token.token])).content.decode()
        assert "poller-account" not in public

    def test_unconfigured_poller_is_called_out_on_the_page(self, client, populated, settings, django_user_model):
        settings.PULSE_POLLER_USERNAME = ""
        client.force_login(django_user_model.objects.create(username="operator2"))
        body = client.get(reverse("pulse:index")).content.decode()
        assert "No poller configured" in body

    def test_template_comments_do_not_leak_into_the_page(self, client, populated, django_user_model):
        """`{# ... #}` only comments out a single line; a multi-line one renders
        its tail as visible text on the page."""
        client.force_login(django_user_model.objects.create(username="operator3"))
        assert "{#" not in client.get(reverse("pulse:index")).content.decode()

    def test_operator_page_uses_no_bootstrap_only_classes(self):
        """base.html ships Tailwind, so Bootstrap class names are silent no-ops.

        This page was written entirely in them — card / card-body / row / col /
        badge bg-success / table-sm / btn-primary — and rendered as unstyled
        semantic HTML on prod for its whole life. Nothing caught it, because
        every other test here asserts strings are *present in the body*, and a
        substring check passes identically with no CSS at all. Only a screenshot
        showed it.

        So this asserts on the class vocabulary rather than on appearance, which
        is the part that can be checked without eyes. Scanned against the
        template source, not the rendered page, so base.html's own markup can't
        muddy the result.
        """
        import re
        from pathlib import Path

        from django.conf import settings

        src = Path(settings.APPS_DIR) / "templates" / "pulse" / "index.html"
        # Drop Django comments — they legitimately *name* these classes to
        # explain the bug, and `{# #}` is single-line so this is exact.
        body = "\n".join(line for line in src.read_text().splitlines() if not line.strip().startswith("{#"))

        bootstrap_only = [
            "card-body",
            "text-muted",
            "table-sm",
            "btn-primary",
            "btn-sm",
            "col-md-",
            "text-uppercase",
            "d-flex",
            "flex-grow-1",
            "bg-success",
            "bg-warning",
            "text-success",
            "text-danger",
            "text-dark",
        ]
        found = [c for c in bootstrap_only if c in body]
        assert not found, (
            f"Bootstrap-only classes in a template that extends base.html (Tailwind): {found}. "
            "These render as nothing. Use the Tailwind/audit_trail house style instead."
        )
        # And confirm it is positively styled, not merely free of the wrong classes.
        assert re.search(r"class=\"[^\"]*\brounded-lg\b", body), "expected Tailwind styling"


class TestDisplayCopy:
    """The funder-facing act copy.

    A figure written into a headline cannot be checked by eye — it looks exactly
    as authoritative when it is stale. The Financial view shipped
    `'$663,682 has reached frontline workers'` as a literal against a live
    $478,490: 39% high, on the same screen as two correct copies of the real
    number. Titles quoting money must be functions of the summary.
    """

    def test_no_currency_amount_is_hardcoded_in_act_copy(self):
        import re
        from pathlib import Path

        from django.conf import settings

        src = (Path(settings.APPS_DIR) / "static" / "pulse" / "display.js").read_text()
        # Block comments are stripped first: the note above the ledger title
        # quotes the offending figures on purpose, and a comment cannot reach a
        # screen. Everything that can render is still scanned.
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)

        # A currency literal is `$` immediately followed by a digit. Interpolation
        # (`${usd(...)}`) and the DOM helper (`$('#id')`) are `$` followed by `{`
        # or `(`, so this discriminates cleanly without parsing JS.
        offenders = re.findall(r"\$\d[\d,\.]*", src)
        assert not offenders, (
            f"Hardcoded currency in display copy: {offenders}. "
            "Make the title a function of the summary so the figure tracks the data."
        )


class TestPhoneLayout:
    """A public Pulse link is shareable, so a funder may well open it on a phone.

    The stylesheet's smallest breakpoint was 900px — a small laptop. Below it
    the shell stacked, but the top-bar scope block and the KPI row never
    wrapped, so a 390px screen laid out 756px wide and had to be dragged
    sideways. Measured in a real browser; asserted here on the stylesheet so a
    later edit can't quietly drop it.
    """

    def _css(self):
        from pathlib import Path

        from django.conf import settings

        return (Path(settings.APPS_DIR) / "static" / "pulse" / "pulse.css").read_text()

    def test_a_phone_width_breakpoint_exists(self):
        import re

        widths = [int(w) for w in re.findall(r"@media\s*\(max-width:\s*(\d+)px\)", self._css())]
        assert widths, "no width breakpoints at all"
        assert min(widths) <= 640, (
            f"smallest breakpoint is {min(widths)}px, which no phone reaches. "
            "Phones are 360-430px; without one the shell lays out ~2x the screen width."
        )

    def test_the_two_blocks_that_forced_the_overflow_are_wrapped(self):
        """`.pulse-scope` (top-bar readout) and `.pulse-kpi` were the offenders:
        a flex row and a flex column that stayed side-by-side at any width."""
        css = self._css()
        phone = css[css.index("@media (max-width: 620px)") :]
        assert ".pulse-scope" in phone and "flex-wrap: wrap" in phone
        assert ".pulse-kpi" in phone and "grid-template-columns: 1fr 1fr" in phone

    def test_the_privacy_note_survives_on_a_phone(self):
        """It states that household coordinates and names are never shown. It is
        a compliance statement, so shrinking the layout must not drop it."""
        css = self._css()
        phone = css[css.index("@media (max-width: 620px)") :]
        assert "display: none" not in phone.split(".pulse-privacy")[1].split("}")[0]

"""The master view: the first screen in this domain that spans programmes.

Every other surface here stops at one programme, by construction --
`SupplyDataAccess.scope_key` is "the programme, always", and it governs the
catalogue as well as the ledger. So "how is this whole operation doing" could
not be asked at all: not badly, not at all. This closes that.

Four things about it are load-bearing, and each is a decision rather than an
implementation detail.

**Access.** A portfolio must NEVER become a way to see a programme you could
not otherwise reach. Membership of a portfolio grants nothing. The rows come
from `labs.context.get_org_data(request)["programs"]` -- the same source the
programme picker uses -- and there is deliberately no second access rule here
to disagree with it.

**When it hides something, it says so.** A portfolio showing two of three
chains without mentioning the third misrepresents the operation, which is the
one thing this view exists not to do. The count of what is missing, and the
reason, are on the page.

**Each row keeps its own units.** There is no conversion between a carton of
co-pack and a jerry can of chlorine, and the domain already refuses to invent
one: `chain_summary` produces quantities only once a single commodity is
named. Nothing here is summed across programmes -- not a quantity, not a
price, not a count that would imply one.

**No ranking.** `DomainHomeView`'s docstring records that a "Needs you"
priority banner was built and then removed, because "prioritising is a
judgement about what matters today and the database does not contain what it
would take to make it". This view knows no more than that one does, so it
inherits the restraint: rows are in the portfolio's own stated order, never in
one computed from severity.
"""

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from connect_labs.labs.access.scopes import Caller
from connect_labs.labs.context import get_org_data
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.portfolio.models import Portfolio
from connect_labs.supply_chain.views import checks_by_audience


def reachable_programmes(request) -> dict:
    """`{program_id: programme}` for every programme this viewer already holds.

    The one access rule this view has, and it is not its own: it is the list
    the programme picker is built from, so a portfolio can only ever show a
    subset of what the viewer could reach by choosing a programme by hand.
    `get_org_data` already folds in the labs-only synthetic programmes an
    entitled user may see, which is what makes the demo's four scopes visible
    to the people entitled to them and to nobody else.
    """
    programmes = {}
    for programme in (get_org_data(request) or {}).get("programs", []):
        try:
            programmes[int(programme["id"])] = programme
        except (KeyError, TypeError, ValueError):
            continue
    return programmes


@method_decorator(login_required, name="dispatch")
class PortfolioView(TemplateView):
    """One row per programme in the portfolio that the viewer can reach."""

    template_name = "supply_chain/portfolio.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        portfolio = Portfolio.objects.filter(slug=self.kwargs["slug"]).first()
        if portfolio is None:
            raise Http404(f"no portfolio named {self.kwargs['slug']!r}")

        reachable = reachable_programmes(self.request)
        rows, hidden = [], 0
        # The portfolio's OWN order. Not sorted, not ranked -- see the module
        # docstring. Reordering the rows would be an opinion about which chain
        # matters today, and nothing in this database supports one.
        for stated in portfolio.program_ids:
            try:
                program_id = int(stated)
            except (TypeError, ValueError):
                # Not a programme anybody can hold, so it cannot be reached
                # and it is counted with the rest of what is not shown. A
                # silent skip is the one thing this view must not do.
                hidden += 1
                continue
            if program_id not in reachable:
                hidden += 1
                continue
            rows.append(self._row(program_id, reachable[program_id]))

        context["portfolio"] = portfolio
        context["rows"] = rows
        context["hidden"] = hidden
        context["stated"] = len(portfolio.program_ids)
        return context

    def _row(self, program_id, programme) -> dict:
        """One programme's chain, in its own units.

        `request` is deliberately NOT passed to `SupplyDataAccess`: the
        constructor merges `request.labs_context`, so a session with an
        opportunity selected would silently narrow every row on this page to
        that opportunity -- including the rows of programmes it has nothing to
        do with. The caller still carries the request, because that is how the
        scope gets authorised a second time, in the data-access layer, against
        the same holdings this view filtered on.
        """
        access = SupplyDataAccess(
            access_token=(self.request.session.get("labs_oauth") or {}).get("access_token"),
            program_id=program_id,
            user=self.request.user,
            caller=Caller(request=self.request, user=self.request.user),
        )
        scope = f"?program_id={program_id}"

        # The same rule the Overview uses: a unit is knowable only once a
        # single commodity is named, so a programme buying exactly one thing
        # gets its quantities and one buying several gets counts. What is
        # never done is a total ACROSS rows.
        commodities = call_operation("commodity_list", access, {})
        commodity = commodities[0] if len(commodities) == 1 else None
        summary = call_operation(
            "chain_summary",
            access,
            {"commodity_slug": commodity["slug"] if commodity else None},
        )
        checks = call_operation("checks_list", access, {})

        return {
            "program_id": program_id,
            "name": programme.get("name") or f"Programme {program_id}",
            "scope": scope,
            "home_url": reverse("supply_chain:home") + scope,
            "commodity_slug": commodity["slug"] if commodity else None,
            "commodity_name": commodity["name"] if commodity else None,
            "commodity_count": len(commodities),
            "summary": summary,
            "checks": checks,
            "checks_by_audience": checks_by_audience(checks, scope=scope),
            "awaited": self._awaited(access),
            # Told apart from a chain that is merely quiet: a programme with a
            # catalogue and nothing bought yet is exactly what a chain about
            # to be seeded looks like, and a grid of zeroes reads as a broken
            # row rather than as "nothing has happened here".
            "empty": (
                summary["source"]["demand"]["rounds"] == 0
                and summary["order"]["contract"]["count"] == 0
                and summary["deliver"]["network"]["supply_points"] == 0
            ),
        }

    def _awaited(self, access) -> dict:
        """What this chain is still waiting on, and how much of it has no date.

        Read off the network page's own `expected_inbound`, so the two cannot
        disagree about what is owed. `undated` is the case the design calls
        out (section 6a): an order that carries a quantity and a donor but no
        promised lead time is not missing data, it is the honest state -- and
        a page that trails off after the supplier's name says "fine" about
        the one fact a funder asks first.
        """
        rows = call_operation("network_stock", access, {})["points"]
        consignments = [expected for row in rows for expected in row["expected_inbound"]]
        dates = sorted(e["expected_on"] for e in consignments if e["expected_on"])
        return {
            "consignments": len(consignments),
            "overdue": sum(1 for e in consignments if e["overdue"]),
            "undated": sum(1 for e in consignments if not e["expected_on"]),
            "next_expected": dates[0] if dates else None,
        }


@method_decorator(login_required, name="dispatch")
class PortfolioMapView(TemplateView):
    """The portfolio laid out by place: where the stock is and what blocks it.

    Built from the same reachable programs and the same operations as the
    rows above (see `map_data`), so it can show nothing the portfolio page
    could not. The payload rides in the page as JSON and every filter is
    applied in the browser: slicing a few hundred places needs no round trip,
    and a filter that re-queried would be a second place for the access rule
    to go wrong.
    """

    template_name = "supply_chain/portfolio_map.html"

    def get_context_data(self, **kwargs):
        from django.conf import settings

        from connect_labs.supply_chain.portfolio.map_data import portfolio_map

        context = super().get_context_data(**kwargs)
        portfolio = Portfolio.objects.filter(slug=self.kwargs["slug"]).first()
        if portfolio is None:
            raise Http404(f"no portfolio named {self.kwargs['slug']!r}")
        context["portfolio"] = portfolio
        everything = self.request.GET.get("scope") == "all"
        context["everything"] = everything
        context["map_payload"] = portfolio_map(
            self.request, portfolio, reachable_programmes(self.request), everything=everything
        )
        context["mapbox_token"] = getattr(settings, "MAPBOX_TOKEN", "") or ""
        return context

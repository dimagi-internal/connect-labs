"""Screens for stock from visits: the dispensing rules, the Workers list and one worker's page.

The Workers list and a worker's page render the figures `worker_stock` and
`worker_stock_get` return (stock/services/belief.py) and derive nothing of
their own beyond a share and a sort order, so no per-worker query is made
here: the list costs the same for two workers as for six hundred.
"""

from decimal import Decimal

from django.http import Http404
from django.urls import reverse
from django.utils.safestring import mark_safe

from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.form_views import OperationFormView
from connect_labs.supply_chain.models import DispensingRule
from connect_labs.supply_chain.stock.dispensing_forms import DispensingRuleForm
from connect_labs.supply_chain.views import OperationBase


class DispensingRulesView(OperationBase):
    """Every rule in the programme, on and off, with the paths it reads."""

    template_name = "supply_chain/dispensing_rules.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if context["has_program_context"]:
            context["rules"] = self.op("dispensing_rule_list", include_inactive=True)
        return context


class _RuleScreen(OperationFormView):
    operation = "dispensing_rule_upsert"
    form_class = DispensingRuleForm
    submit_label = "Save rule"

    def breadcrumb(self, **kwargs):
        return [
            {"label": "Stock", "href": reverse("supply_chain:stock")},
            {"label": "Dispensing rules", "href": reverse("supply_chain:dispensing_rules")},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:dispensing_rules")

    def redirect_to(self, result):
        return reverse("supply_chain:dispensing_rules")


class DispensingRuleCreateView(_RuleScreen):
    title = "New dispensing rule"
    intro = "What a visit on one opportunity gives out of one item, read from the form's own answers."
    footnote = "There is one rule per opportunity and item: saving one that exists edits it."


class DispensingRuleUpdateView(_RuleScreen):
    title = "Edit dispensing rule"
    intro = "Visits already posted are not read again; the change applies to visits read from now on."
    footnote = "The opportunity and item identify the rule, so they cannot change here."

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "editing": True}

    def get_initial(self):
        rule = DispensingRule.objects.filter(
            program_id=_access(self.request).program_id, pk=self.kwargs["rule_id"]
        ).first()
        if rule is None:
            raise Http404("no such rule in this programme")
        return {
            "opportunity_id": rule.opportunity_id,
            "item": rule.item_id,
            "resupply_point": rule.resupply_point_id,
            "active_from": rule.active_from,
            "lines": rule.lines,
            "form_names": "\n".join(rule.forms or []),
            "reports": rule.reports or None,
            "status": rule.status,
        }


# ---- workers ---------------------------------------------------------------


def rule_items(op) -> list[dict]:
    """The items some dispensing rule gives out, in rule order, once each."""
    seen, items = set(), []
    for rule in op("dispensing_rule_list", include_inactive=True):
        if rule["item_id"] not in seen:
            seen.add(rule["item_id"])
            items.append({"id": rule["item_id"], "name": rule["item_name"]})
    return items


def chosen_item(request, items):
    """The item asked for by `?item_id=`, else the first a rule gives out, else None."""
    wanted = request.GET.get("item_id")
    for item in items:
        if wanted and str(item["id"]) == wanted:
            return item
    return items[0] if items else None


def as_of_payload(request) -> dict:
    """`{"as_of": "YYYY-MM-DD"}` while a past day is on view, so figures stop on that day."""
    day = getattr(request, "supply_as_of", None)
    return {"as_of": day.isoformat()} if day else {}


def _amount(cell):
    try:
        return Decimal(cell["amount"]) if isinstance(cell, dict) and cell.get("amount") is not None else None
    except (ArithmeticError, TypeError, ValueError):
        return None


def _share(part, whole):
    """`part` as a whole percentage of `whole`, or None when either is not a number or whole is 0."""
    part, whole = _amount(part), _amount(whole)
    if part is None or not whole:
        return None
    return int((part / whole * 100).to_integral_value())


def _plain_number(value):
    try:
        return Decimal(value) if isinstance(value, str) else None
    except ArithmeticError:
        return None


# Every column of the Workers list, and how it sorts. A row with no figure in
# the column (never counted, no rate yet) goes last whichever way.
SORTS = {
    "name": lambda row: row["name"].lower(),
    "on_hand": lambda row: _amount(row["on_hand"]),
    "days_to_stockout": lambda row: _plain_number(row["days_to_stockout"]),
    "variance": lambda row: _amount(row["variance_reported_minus_ledger"]),
    "days_since_checked": lambda row: row["days_since_checked"],
    "unapproved": lambda row: row["unapproved_share"],
    "estimated": lambda row: row["estimated_share"],
    "no_answer": lambda row: row["no_answer_visits"],
}

COLUMNS = (
    ("name", "Worker", ""),
    ("on_hand", "On hand", "the ledger: issued minus dispensed"),
    ("days_to_stockout", "Days to stock-out", ""),
    ("variance", "Count − ledger on count day", "negative: fewer counted than the ledger held that day"),
    ("days_since_checked", "Days since checked", ""),
    ("unapproved", "Unapproved", "share of dispensing on visits not yet approved"),
    ("estimated", "Estimated", "share of dispensing from protocol quantities"),
    ("no_answer", "No answer", "visits whose form did not say"),
)


def sort_rows(rows, key, descending):
    """Sorted by the column asked for, by name within it; a row with no figure goes last either way."""
    value = SORTS[key]
    by_name = sorted(rows, key=SORTS["name"])
    present = [row for row in by_name if value(row) is not None]
    missing = [row for row in by_name if value(row) is None]
    return sorted(present, key=value, reverse=descending) + missing


class WorkersView(OperationBase):
    """One row per worker. Sorted by name until the reader picks a column (§22: facts, no ranking)."""

    template_name = "supply_chain/workers.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        items = rule_items(self.op)
        item = chosen_item(self.request, items)
        context.update(items=items, item=item)
        if item is None:
            return context
        data = self.op("worker_stock", item_id=item["id"], **as_of_payload(self.request))
        from connect_labs.supply_chain.network.views import band_of

        for row in data["workers"]:
            row["band"] = band_of(row)
            row["unapproved_share"] = _share(row["unapproved"], row["dispensed"])
            row["estimated_share"] = _share(row["estimated"], row["dispensed"])
        # An unknown column is no choice at all: back to by name, A to Z.
        chosen = self.request.GET.get("sort") in SORTS
        sort = self.request.GET["sort"] if chosen else "name"
        descending = chosen and self.request.GET.get("dir") == "desc"
        context.update(
            data=data,
            rows=sort_rows(data["workers"], sort, descending),
            sort=sort,
            descending=descending,
            columns=COLUMNS,
        )
        return context


# The figures above the flow, each filled by flow.js for the week on view.
FLOW_TILES = (
    ("arrived", "Arrived"),
    ("stores", "In the stores"),
    ("workers", "With workers"),
    ("given", "Given out at visits"),
    ("lost", "Lost or sent elsewhere"),
)


class StockFlowView(OperationBase):
    """Where one item went, from what arrived to what the visits gave out, week by week (`stock_flow`)."""

    template_name = "supply_chain/flow.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        items = rule_items(self.op)
        item = chosen_item(self.request, items)
        context.update(items=items, item=item)
        if item is not None:
            flow = self.op("stock_flow", item_id=item["id"], **as_of_payload(self.request))
            from connect_labs.supply_chain.templatetags.supply_chain_extras import unit_plural

            flow["unit_plural"] = unit_plural(flow["unit"])
            context["flow"] = flow
            context["flow_tiles"] = FLOW_TILES
        return context


class WorkerDetailView(OperationBase):
    """One worker's stock of one item: the figures, the timeline, and the visits and arrivals behind it."""

    template_name = "supply_chain/worker_detail.html"

    def get_context_data(self, **kwargs):
        from connect_labs.supply_chain.stock.services.timeline import timeline_svg

        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        items = rule_items(self.op)
        item = chosen_item(self.request, items)
        context.update(items=items, item=item)
        if item is None:
            return context
        try:
            data = self.op(
                "worker_stock_get",
                supply_point_id=self.kwargs["supply_point_id"],
                item_id=item["id"],
                **as_of_payload(self.request),
            )
        except ValueError as error:
            raise Http404(str(error)) from error
        # Safe: timeline_svg builds its markup from numbers and ISO dates and
        # escapes the one string that comes from data, the unit (test_timeline pins it).
        from connect_labs.supply_chain.network.views import band_of

        data["worker"]["band"] = band_of(data["worker"])
        listed, gave_none = split_visits(data["visits"])
        context.update(
            data=data,
            worker=data["worker"],
            chart=mark_safe(timeline_svg(data["timeline"])),
            listed_visits=listed,
            gave_none_visits=gave_none,
        )
        return context


def split_visits(visits):
    """(listed, gave none): the visits that moved stock first, then the rest that say something.

    A visit that gave none and moved nothing is counted rather than listed --
    most of a worker's visits are screenings, and fifty rows of "Gave none"
    hid the few that make up the Dispensed figure. Newest first within each.
    """

    def gave_none(v):
        return v.get("outcome") == "nothing_given" and not v.get("moved") and not v.get("reported")

    moved = [v for v in visits if v.get("moved")]
    said = [v for v in visits if not v.get("moved") and not gave_none(v)]
    return moved + said, [v for v in visits if gave_none(v)]

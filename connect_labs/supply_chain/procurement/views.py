"""Procurement screens. Read-only context comes from operations, and every
mutating action — quote entry, award — goes through an operation too.

`call_operation` and `_access` are imported directly into this module rather
than reached only via the domain shell's `OperationBase.op()`: a name that
lives one module up cannot be patched from here (or from a test that patches
"connect_labs.supply_chain.procurement.views.call_operation"), so a bare
`from connect_labs.supply_chain.views import OperationBase as _Base` would
leave this module able to run real operations against a fake session in
tests, hitting production APIs instead of the mock. Mirroring the tiny `op()`
helper locally keeps the same one-path-to-the-domain contract without that
trap.

The web client calls operations directly from these views — it does not
route through its own JSON API (api_views.py). That endpoint is the machine
surface, deliberately JSON-in/JSON-out for agents and API clients; teaching
it to also accept browser form-encoded POSTs would blur that boundary and
give up the CSRF protection Django hands the server-rendered path for free.
Three clients over one registry (web, HTTP API, MCP) was always the
design — the web client is a Django view calling an operation, not an HTTP
client of its own API.
"""

from datetime import date, datetime

import jsonschema
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.operations import call_operation


def _days_waiting(sent_on):
    """Days since outreach went out, or None when there is nothing to count from.

    `sent_on` is a plain ISO date string on the record (LabsRecord JSON has no
    datetime type), so Django's `timesince` filter — which requires a real
    datetime — is the wrong tool here and raises on a string.
    """
    if not sent_on:
        return None
    try:
        sent = datetime.fromisoformat(sent_on).date()
    except (TypeError, ValueError):
        return None
    return (date.today() - sent).days


# The quote-entry form submits every field as a plain string, but the
# quote_record operation's schema declares these as JSON integers (see
# operations.ID / _NON_NEGATIVE_INT). jsonschema does not coerce "5" to 5,
# so passing request.POST straight through 400s on every real submission
# that names a round, supplier, or item — i.e. every one of them.
_QUOTE_INT_FIELDS = {
    "round_id",
    "supplier_id",
    "item_id",
    "base_per_pack_stated",
    "base_unit_grams_stated",
    "shelf_life_months_stated",
    "lead_time_days",
}


@method_decorator(login_required, name="dispatch")
class _Base(TemplateView):
    def op(self, name, **payload):
        return call_operation(name, _access(self.request), payload)


class RoundBoardView(_Base):
    template_name = "supply_chain/procurement/round_board.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        # round_list is programme-scoped; a fresh '/supply/procurement/'
        # visit before a programme is selected is a normal state
        # (labs_context = {}), not a bug -- see api_views.has_program_context.
        context["rounds"] = self.op("round_list") if context["has_program_context"] else []
        return context


class RoundDetailView(_Base):
    template_name = "supply_chain/procurement/round_detail.html"

    def get_context_data(self, round_id, **kwargs):
        context = super().get_context_data(**kwargs)
        context["round"] = self.op("round_get", round_id=round_id)
        outreach = self.op("outreach_list", round_id=round_id)
        for o in outreach:
            o["days_waiting"] = None if o.get("responded") else _days_waiting(o.get("sent_on"))
        context["outreach"] = outreach
        context["quotes"] = self.op("quote_list", round_id=round_id)
        return context


class ComparisonView(_Base):
    template_name = "supply_chain/procurement/comparison.html"

    def get_context_data(self, round_id, **kwargs):
        context = super().get_context_data(**kwargs)
        round_ = self.op("round_get", round_id=round_id)
        lines = (round_ or {}).get("lines") or []
        commodity = self.request.GET.get("commodity")

        # round_compare's schema requires commodity_slug as a string — nothing
        # ambiguous is a safe default. But a bookmark, browser-history entry,
        # or shared link with no ?commodity= at all is a normal way to land
        # here, and it must not 500. A round with exactly one line has one
        # sensible default; more than one (or none) means asking, which is
        # also more useful than an error: it's a worklist of what to compare.
        if not commodity and len(lines) == 1:
            commodity = lines[0].get("commodity_slug")

        comparison = self.op("round_compare", round_id=round_id, commodity_slug=commodity) if commodity else None

        context["round"] = round_
        context["round_id"] = round_id
        context["commodity_slug"] = commodity
        context["comparison"] = comparison
        # ranked_by is a bare figure key (e.g. "landed_total_for_round_quantity");
        # its human label already lives on the matching column (pricing.py's
        # FIGURE_LABELS, formatted with this commodity's own unit nouns), so look
        # it up here rather than re-deriving or hardcoding a second copy in the
        # template. None when nothing is comparable (finding 14) -- no column
        # matches and the template shows "not yet ranked" instead.
        context["ranked_by_label"] = None
        if comparison and comparison.get("ranked_by"):
            column = next((c for c in comparison["columns"] if c["key"] == comparison["ranked_by"]), None)
            context["ranked_by_label"] = column["label"] if column else comparison["ranked_by"]
        return context

    def post(self, request, round_id, *args, **kwargs):
        """Award a quote. The Award button's form posts here (action="" —
        same URL, so the ?commodity= query string round-trips for free).

        A missing or empty rationale is refused by award_create's schema, not
        guessed around here — that refusal must not become a 500: catch it
        and re-render the page with what's wrong, the same way a browser
        form re-shows itself on a validation error.
        """
        quote_id_raw = request.POST.get("quote_id")
        rationale = request.POST.get("rationale", "")
        try:
            self.op(
                "award_create",
                round_id=round_id,
                quote_id=int(quote_id_raw),
                rationale=rationale,
                decided_by=request.user.get_username(),
            )
        except jsonschema.ValidationError as exc:
            context = self.get_context_data(round_id=round_id, **kwargs)
            context["award_error"] = exc.message
            return self.render_to_response(context)
        except (TypeError, ValueError):
            context = self.get_context_data(round_id=round_id, **kwargs)
            context["award_error"] = "No valid quote was selected to award."
            return self.render_to_response(context)

        url = reverse("supply_chain:procurement_comparison", args=[round_id])
        commodity = request.GET.get("commodity")
        return redirect(f"{url}?commodity={commodity}" if commodity else url)


class FollowupDraftView(_Base):
    """A read-only render of the follow-up email for one quote, for copying.

    Just an operation call behind a GET — followup_render mutates nothing,
    so there is no form, no CSRF concern, and no reason to route it through
    a POST.
    """

    template_name = "supply_chain/procurement/followup_draft.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        quote_id_raw = self.request.GET.get("quote_id")
        try:
            quote_id = int(quote_id_raw)
        except (TypeError, ValueError):
            context["error"] = "No quote was specified."
            return context
        context["quote_id"] = quote_id
        context["text"] = self.op("followup_render", quote_id=quote_id)["text"]
        return context


class QuoteEntryView(_Base):
    template_name = "supply_chain/procurement/quote_entry.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        context["commodities"] = self.op("commodity_list")
        context["suppliers"] = self.op("supplier_list")
        # For the pack_spec_source=trade_item_confirmed picker.
        context["items"] = self.op("item_list")
        # round_list is programme-scoped; the round board's "Record a quote"
        # button is only shown once a programme is selected (finding 3's
        # fix), but this route must stay safe however it is reached --
        # a bookmark, a direct URL, or a raw POST.
        context["rounds"] = self.op("round_list") if context["has_program_context"] else []
        return context

    def post(self, request, *args, **kwargs):
        """Record a quote. The person filling this in is transcribing figures
        out of a supplier email — "52,42" instead of "52.42", a European
        decimal comma from a francophone supplier — is an ordinary typo, not
        a reason to lose their work. A schema rejection (or a field that
        doesn't even coerce to the integer the schema wants) re-renders this
        same form with what's wrong AND what they typed, rather than 500ing
        or discarding the entry.
        """
        if not has_program_context(request):
            return self.render_to_response(self.get_context_data(**kwargs))

        submitted = {key: value for key, value in request.POST.items() if key != "csrfmiddlewaretoken"}
        data = {}
        error = None
        for key, value in submitted.items():
            if value == "":
                continue
            if key in _QUOTE_INT_FIELDS:
                try:
                    data[key] = int(value)
                except ValueError:
                    error = f"'{value}' is not a whole number for {key.replace('_', ' ')}."
                    break
            else:
                data[key] = value

        if error is None:
            try:
                created = self.op("quote_record", data=data)
            except jsonschema.ValidationError as exc:
                error = exc.message

        if error is not None:
            context = self.get_context_data(**kwargs)
            context["quote_error"] = error
            context["submitted"] = submitted
            return self.render_to_response(context)

        url = reverse("supply_chain:procurement_comparison", args=[created["round_id"]])
        return redirect(f"{url}?commodity={created['commodity_slug']}")


class RegistriesView(_Base):
    template_name = "supply_chain/procurement/registries.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["commodities"] = self.op("commodity_list")
        context["suppliers"] = self.op("supplier_list")
        return context

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
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.form_views import OperationActionView, OperationFormView
from connect_labs.supply_chain.forms import (
    ApprovalDecisionForm,
    ApprovalRequestForm,
    OutreachForm,
    OutreachReplyForm,
    QuoteForm,
    ReasonForm,
    RoundForm,
    RoundLineFormSet,
)
from connect_labs.supply_chain.navigation import supply_tabs
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
@method_decorator(login_required, name="dispatch")
class _Base(TemplateView):
    def op(self, name, **payload):
        return call_operation(name, _access(self.request), payload)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["supply_tabs"] = supply_tabs(self.request)
        return context


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
        round_ = self.op("round_get", round_id=round_id)
        # Was a 200 rendering "Round not found." A missing resource answering
        # 200 tells a browser, a link checker and a monitor that the page is
        # fine, which is the one thing it is not.
        if round_ is None:
            raise Http404(f"no round {round_id} in this programme")
        context["round"] = round_
        outreach = self.op("outreach_list", round_id=round_id)
        for o in outreach:
            o["days_waiting"] = None if o.get("responded") else _days_waiting(o.get("sent_on"))
        context["outreach"] = outreach
        context["quotes"] = self.op("quote_list", round_id=round_id)
        # Rows showed "Supplier #2". An id is not a supplier to anyone
        # reading the page, and the name is one list call away.
        context["supplier_names"] = {s["id"]: s["name"] for s in self.op("supplier_list")}
        return context


class QuoteDetailView(_Base):
    """One quote: what the supplier stated, beside what we derive from it.

    The two columns are the product. A quote arrives on the supplier's own
    terms -- per carton, per sachet, freight in or out -- and every figure
    worth comparing is derived from those terms plus the round and the
    commodity. Showing the derivation next to its inputs is what makes an
    `Unconfirmed` legible: the reason names the input that is missing, and the
    input is right there, blank.

    It also holds the two things that existed in the data with nowhere to be
    read: the date the quote arrived, and any documents attached to it.
    """

    template_name = "supply_chain/procurement/quote_detail.html"

    def get_context_data(self, quote_id, **kwargs):
        context = super().get_context_data(**kwargs)
        # Every programme-scoped view in this app guards this and the new one
        # did not: without a programme in context, `quote_get` reaches
        # `_require_program` and raises, which is a 500 on a page reached by a
        # link rather than an honest "choose a programme".
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context

        detail = self.op("quote_get", quote_id=quote_id)
        if detail is None:
            raise Http404(f"no quote {quote_id} in this programme")
        context["detail"] = detail
        quote = detail["quote"]
        context["quote"] = quote
        context["round"] = self.op("round_get", round_id=quote["round_id"])
        context["supplier"] = self.op("supplier_get", supplier_id=quote["supplier_id"])
        # A quote carries its own evidence now -- a quotation PDF, or the
        # pro-forma invoice a price came off, which is what EHA's note cites.
        # The supplier's own documents are shown separately rather than mixed
        # in: a certification belongs to the company, not to this offer.
        context["documents"] = self.op("document_list", quote_id=quote["id"])
        # Guarded rather than passed straight through. `list_documents` treats
        # a None link id as "no filter", so a missing supplier would render
        # EVERY document in the programme under this supplier's name.
        # `Quote.supplier` is non-nullable today, so that is unreachable --
        # but the widening is silent, and one nullable column later it would
        # be a quiet disclosure rather than an error.
        supplier_id = quote.get("supplier_id")
        context["supplier_documents"] = self.op("document_list", supplier_id=supplier_id) if supplier_id else []
        # The invitation this quote answered, so the page can say how long the
        # supplier took rather than only when the quote landed.
        context["outreach"] = [
            o for o in self.op("outreach_list", round_id=quote["round_id"]) if o["supplier_id"] == quote["supplier_id"]
        ]
        context["questions"] = detail["missing"]
        return context


class ComparisonView(_Base):
    template_name = "supply_chain/procurement/comparison.html"

    def get_context_data(self, round_id, **kwargs):
        context = super().get_context_data(**kwargs)
        round_ = self.op("round_get", round_id=round_id)
        # The `or {}` below tolerated a missing round as far as here and then
        # `round_compare` raised on it, so a stale link 500'd. A round that is
        # not in this programme is a 404.
        if round_ is None:
            raise Http404(f"no round {round_id} in this programme")
        lines = round_.get("lines") or []
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
        # The awards already made on this line, so the page that awards is
        # also the way to one -- and to the approvals it may be waiting on.
        context["awards"] = (
            [a for a in self.op("award_list", round_id=round_id) if a["commodity_slug"] == commodity]
            if commodity
            else []
        )

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


# FollowupDraftView used to render a ready-to-send follow-up email here.
# It is gone from the product on purpose (design doc section 22): the
# `followup_render` OPERATION remains, so a client -- an agent, a script, a
# person hitting the API -- can ask for the text. What the product no longer
# does is press a drafted message on the user as the next thing to do.
# Prioritising and phrasing are judgements about what matters today, which a
# client can make better than a hardcoded page can.


class QuoteEntryView(OperationFormView):
    """Record a quote, as the supplier stated it.

    This was the last screen in the domain built by hand — a 281-line
    template, a set of field names to coerce to integers, and a POST handler
    that rebuilt the page out of `request.POST` on a rejection so the typist
    did not lose their work.

    Django does all three. A bound form re-renders with what was typed; a
    `ModelChoiceField` coerces and validates an id; a `DecimalField` reports a
    bad number on the field it came from, rather than as a sentence naming a
    payload key. The care in the original was right — somebody transcribing
    figures out of a supplier email will type "52,42" — and it is now the
    framework's job rather than this view's.
    """

    operation = "quote_record"
    form_class = QuoteForm
    title = "Record a quote"
    intro = (
        "As the supplier stated it. Converting it to a comparable basis happens in the "
        "comparison, not here — a screen that normalised on entry would throw away the only "
        "record of what they actually wrote."
    )
    submit_label = "Record quote"

    def get_initial(self):
        initial = super().get_initial()
        # Arriving from a round, that round is the answer.
        round_id = self.request.GET.get("round")
        if round_id and str(round_id).isdigit():
            initial.setdefault("round", int(round_id))
        return initial

    def breadcrumb(self, **kwargs):
        return [
            {"label": "Sourcing", "href": reverse("supply_chain:procurement_round_board")},
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:procurement_round_board")

    def redirect_to(self, result):
        url = reverse("supply_chain:procurement_comparison", args=[result["round_id"]])
        return f"{url}?commodity={result['commodity_slug']}"


# ---- write screens -------------------------------------------------------
#
# Each is a declaration: which operation, what to call it, and where to go
# afterwards. The form comes from the model (connect_labs/supply_chain/forms.py)
# and the page from one shared template, so a screen carries no markup and no
# validation of its own.


class _RoundScreen(OperationFormView):
    """Create or update a round, header plus its commodity lines.

    A round asks for one or more commodities, each with its own quantity and
    unit, so the lines are a formset rather than a JSON textarea -- which is
    what a ModelForm would render `Round.lines` as.
    """

    form_class = RoundForm
    template_name = "supply_chain/procurement/round_form.html"

    def commodities(self):
        return [(c["slug"], c["name"]) for c in self.op("commodity_list")]

    def line_formset(self, data=None, initial=None):
        return RoundLineFormSet(
            data,
            initial=initial,
            prefix="lines",
            form_kwargs={"commodities": self.commodities()},
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if context.get("has_program_context") and "lines" not in context:
            context["lines"] = (
                self.line_formset(self.request.POST)
                if self.request.method == "POST"
                else self.line_formset(initial=self.initial_lines())
            )
        return context

    def initial_lines(self):
        return []

    def form_valid(self, form):
        lines = self.line_formset(self.request.POST)
        if not lines.is_valid():
            return self.render_to_response(self.get_context_data(form=form, lines=lines))

        kept = [
            {
                "commodity_slug": row["commodity_slug"],
                "quantity": str(row["quantity"]),
                "quantity_unit": row["quantity_unit"],
            }
            for row in lines.cleaned_data
            if row and not row.get("DELETE") and row.get("commodity_slug")
        ]
        if not kept:
            form.add_error(None, "A round has to ask for at least one commodity.")
            return self.render_to_response(self.get_context_data(form=form, lines=lines))

        self._lines = kept
        return super().form_valid(form)

    def fixed(self, **kwargs):
        return {"data": {"lines": getattr(self, "_lines", [])}}

    def breadcrumb(self, **kwargs):
        return [
            {"label": "Sourcing", "href": reverse("supply_chain:procurement_round_board")},
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:procurement_round_board")

    def redirect_to(self, result):
        return reverse("supply_chain:procurement_round_detail", args=[result["id"]])


class RoundCreateView(_RoundScreen):
    operation = "round_create"
    title = "New quote round"
    intro = (
        "A round is one ask, to several suppliers, for the same thing. It opens in draft: "
        "nothing goes out until you open it."
    )
    submit_label = "Create round"


class RoundUpdateView(_RoundScreen):
    operation = "round_update"
    title = "Edit round"
    submit_label = "Save changes"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self._round_instance()
        return kwargs

    def _round_instance(self):
        from connect_labs.supply_chain.models import Round

        found = Round.objects.filter(pk=self.kwargs["round_id"], program_id=_access(self.request).program_id).first()
        if found is None:
            raise Http404(f"no round {self.kwargs['round_id']} in this programme")
        return found

    def initial_lines(self):
        return [
            {
                "commodity_slug": line.get("commodity_slug"),
                "quantity": line.get("quantity"),
                "quantity_unit": line.get("quantity_unit"),
            }
            for line in (self._round_instance().lines or [])
        ]

    def fixed(self, **kwargs):
        return {"round_id": int(kwargs["round_id"]), "data": {"lines": getattr(self, "_lines", [])}}


class RoundOpenView(OperationActionView):
    operation = "round_open"
    success_message = "Round opened — it can take quotes now."

    def fixed(self, **kwargs):
        return {"round_id": int(kwargs["round_id"])}

    def redirect_to(self, **kwargs):
        return reverse("supply_chain:procurement_round_detail", args=[kwargs["round_id"]])


class RoundCloseView(OperationActionView):
    operation = "round_close"
    success_message = "Round closed to further quotes."

    def fixed(self, **kwargs):
        return {"round_id": int(kwargs["round_id"])}

    def redirect_to(self, **kwargs):
        return reverse("supply_chain:procurement_round_detail", args=[kwargs["round_id"]])


class OutreachLogView(OperationFormView):
    operation = "outreach_log"
    form_class = OutreachForm
    title = "Record an invitation"
    intro = (
        "That we asked this supplier to quote on this round. A log, not a state machine — "
        "re-inviting is a real event worth keeping."
    )
    submit_label = "Record invitation"

    def fixed(self, **kwargs):
        return {"data": {"round_id": int(kwargs["round_id"])}}

    def breadcrumb(self, **kwargs):
        return [
            {"label": "Sourcing", "href": reverse("supply_chain:procurement_round_board")},
            {"label": "Round", "href": reverse("supply_chain:procurement_round_detail", args=[kwargs["round_id"]])},
            {"label": "Record an invitation"},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:procurement_round_detail", args=[kwargs["round_id"]])

    def redirect_to(self, result):
        return reverse("supply_chain:procurement_round_detail", args=[result["round_id"]])


class OutreachReplyView(OperationFormView):
    operation = "outreach_update"
    form_class = OutreachReplyForm
    title = "Record a reply"
    intro = "What came back, and when they were last chased."
    submit_label = "Save"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self._outreach()
        return kwargs

    def _outreach(self):
        from connect_labs.supply_chain.models import Outreach

        found = Outreach.objects.filter(
            pk=self.kwargs["outreach_id"], round__program_id=_access(self.request).program_id
        ).first()
        if found is None:
            raise Http404(f"no invitation {self.kwargs['outreach_id']} in this programme")
        return found

    def fixed(self, **kwargs):
        return {"outreach_id": int(kwargs["outreach_id"])}

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:procurement_round_detail", args=[self._outreach().round_id])

    def redirect_to(self, result):
        return reverse("supply_chain:procurement_round_detail", args=[result["round_id"]])


class OutreachDeleteView(OperationFormView):
    operation = "outreach_delete"
    form_class = ReasonForm
    title = "Delete this invitation"
    danger = True
    submit_label = "Delete invitation"
    intro = (
        "For an invitation recorded in error. Unlike voiding a quote this removes the row: "
        "a quote is a supplier's stated fact worth keeping once superseded, and an invitation "
        "we never sent is not history — it is a mistake that would keep asserting the contact."
    )

    def fixed(self, **kwargs):
        return {"outreach_id": int(kwargs["outreach_id"])}

    def redirect_to(self, result):
        return reverse("supply_chain:procurement_round_detail", args=[result["round_id"]])


class QuoteVoidView(OperationFormView):
    operation = "quote_void"
    form_class = ReasonForm
    title = "Void this quote"
    danger = True
    submit_label = "Void quote"
    intro = (
        "The quote stays readable and stops counting. A supplier said this, and that they said it "
        "remains true — voiding records that it should not be compared, it does not erase it."
    )

    def fixed(self, **kwargs):
        return {"quote_id": int(kwargs["quote_id"])}

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:procurement_quote_detail", args=[kwargs["quote_id"]])

    def redirect_to(self, result):
        return reverse("supply_chain:procurement_quote_detail", args=[result["id"]])


# ---- awards and their approvals -----------------------------------------


def _award(request, award_id):
    """An award, reached through its round's programme, or a 404."""
    from connect_labs.supply_chain.models import Award

    found = (
        Award.objects.filter(pk=award_id, round__program_id=_access(request).program_id)
        .select_related("supplier", "commodity", "round")
        .first()
    )
    if found is None:
        raise Http404(f"no award {award_id} in this programme")
    return found


class AwardDetailView(_Base):
    """One award: the decision, who else has to agree to it, and what was ordered.

    An award is a decision, not a commitment. Between the two there may be
    somebody whose agreement the award needs -- a technical partner, a
    funder, a regulator -- and until they have said yes, an order cannot be
    placed against it. The page says so in the place the order button would
    otherwise be, naming whose answer is outstanding.
    """

    template_name = "supply_chain/procurement/award_detail.html"

    def get_context_data(self, award_id, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        award = _award(self.request, award_id)
        detail = next((a for a in self.op("award_list", round_id=award.round_id) if a["id"] == award.pk), None)
        if detail is None:
            raise Http404(f"no award {award_id} in this programme")
        approvals = self.op("approval_list", award_id=award.pk)
        orgs = {o["id"]: o for o in self.op("org_list")}
        context["award"] = detail
        context["supplier"] = self.op("supplier_get", supplier_id=detail["supplier_id"])
        context["round"] = self.op("round_get", round_id=detail["round_id"])
        context["approvals"] = [{**a, "approver": orgs.get(a["approver_org_id"])} for a in approvals]
        # The same rule the order guard applies: a refusal later reversed by
        # a fresh approval from the same approver in the same role is history.
        blocking_ids = {a.pk for a in _access(self.request).blocking_approvals(award)}
        context["blocking"] = [a for a in context["approvals"] if a["id"] in blocking_ids]
        context["contracts"] = [
            c for c in self.op("contract_list", round_id=detail["round_id"]) if c["award_id"] == award.pk
        ]
        return context


class _AwardScreen(OperationFormView):
    def breadcrumb(self, **kwargs):
        award = self.award()
        return [
            {"label": "Sourcing", "href": reverse("supply_chain:procurement_round_board")},
            {
                "label": award.round.label,
                "href": reverse("supply_chain:procurement_round_detail", args=[award.round_id]),
            },
            {
                "label": f"Award to {award.supplier.name}",
                "href": reverse("supply_chain:award_detail", args=[award.pk]),
            },
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:award_detail", args=[self.award().pk])

    def redirect_to(self, result):
        return reverse("supply_chain:award_detail", args=[self.award().pk])


class ApprovalRequestView(_AwardScreen):
    operation = "approval_request"
    form_class = ApprovalRequestForm
    title = "Ask for an approval"
    intro = (
        "Somebody other than the decider whose agreement this award needs before money moves — a "
        "technical partner confirming the product, a funder approving its use. Until they answer, "
        "no order can be placed against the award."
    )
    submit_label = "Record the request"

    def award(self):
        return _award(self.request, self.kwargs["award_id"])

    def fixed(self, **kwargs):
        return {"data": {"award_id": int(kwargs["award_id"])}}


class ApprovalDecideView(_AwardScreen):
    operation = "approval_decide"
    form_class = ApprovalDecisionForm
    title = "Record their answer"
    intro = (
        "Approved or declined, once. If a refusal is later reversed, ask again — the first answer "
        "stays on the record."
    )
    submit_label = "Record the answer"

    def approval(self):
        from connect_labs.supply_chain.models import AwardApproval

        found = AwardApproval.objects.filter(
            pk=self.kwargs["approval_id"], award__round__program_id=_access(self.request).program_id
        ).first()
        if found is None:
            raise Http404(f"no approval {self.kwargs['approval_id']} in this programme")
        return found

    def award(self):
        return _award(self.request, self.approval().award_id)

    def fixed(self, **kwargs):
        return {"approval_id": int(kwargs["approval_id"])}

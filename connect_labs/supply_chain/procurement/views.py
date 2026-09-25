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
    TenderForm,
    TenderLineFormSet,
)
from connect_labs.supply_chain.fulfilment_forms import DocumentForm
from connect_labs.supply_chain.navigation import supply_tabs
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.values import quantity_phrase, unit_noun


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
# that names a tender, supplier, or item — i.e. every one of them.
@method_decorator(login_required, name="dispatch")
class _Base(TemplateView):
    def op(self, name, **payload):
        return call_operation(name, _access(self.request), payload)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["supply_tabs"] = supply_tabs(self.request)
        return context


class TenderBoardView(_Base):
    template_name = "supply_chain/procurement/tender_board.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        # tender_list is programme-scoped; a fresh '/supply/procurement/'
        # visit before a programme is selected is a normal state
        # (labs_context = {}), not a bug -- see api_views.has_program_context.
        context["tenders"] = self.op("tender_list") if context["has_program_context"] else []
        return context


class TenderDetailView(_Base):
    template_name = "supply_chain/procurement/tender_detail.html"

    def get_context_data(self, tender_id, **kwargs):
        context = super().get_context_data(**kwargs)
        tender = self.op("tender_get", tender_id=tender_id)
        # Was a 200 rendering "Tender not found." A missing resource answering
        # 200 tells a browser, a link checker and a monitor that the page is
        # fine, which is the one thing it is not.
        if tender is None:
            raise Http404(f"no tender {tender_id} in this programme")
        context["tender"] = tender
        outreach = self.op("outreach_list", tender_id=tender_id)
        for o in outreach:
            o["days_waiting"] = None if o.get("responded") else _days_waiting(o.get("sent_on"))
        context["outreach"] = outreach
        context["quotes"] = self.op("quote_list", tender_id=tender_id)
        # Each quote's trade item, by name and -- for a kit -- contents. Three
        # co-pack quotes from one distributor read as the same offer three
        # times, told apart only by price.
        items = {}
        for quote in context["quotes"]:
            item_id = quote.get("item_id")
            if item_id and item_id not in items:
                items[item_id] = self.op("item_get", item_id=item_id)
        commodities = {c["slug"]: c for c in self.op("commodity_list")}
        context["quotes"] = [
            {
                **quote,
                "item": items.get(quote.get("item_id")) if quote.get("item_id") else None,
                "priced_per": _priced_per(
                    quote,
                    items.get(quote.get("item_id")) if quote.get("item_id") else None,
                    commodities.get(quote.get("commodity_slug")),
                ),
            }
            for quote in context["quotes"]
        ]
        # Rows showed "Supplier #2". An id is not a supplier to anyone
        # reading the page, and the name is one list call away.
        context["supplier_names"] = {s["id"]: s["name"] for s in self.op("supplier_list")}
        context["commodity_names"] = _commodity_names(self.op("commodity_list"))
        # What the tender buys, a line at a time, with a kit's contents when
        # the tender states them -- the fact its comparison ranks against.
        context["buys"] = [
            {
                "name": context["commodity_names"].get(line.get("commodity_slug"), line.get("commodity_slug")),
                "quantity": line.get("quantity"),
                "unit": line.get("quantity_unit"),
                "contents": " + ".join(
                    f"{part.get('quantity')} {part.get('base_unit')} "
                    f"{context['commodity_names'].get(part.get('commodity_slug'), part.get('commodity_slug'))}"
                    for part in line.get("components") or []
                ),
            }
            for line in (tender.get("lines") or [])
            if isinstance(line, dict)
        ]
        return context


class QuoteDetailView(_Base):
    """One quote: what the supplier stated, beside what we derive from it.

    The two columns are the product. A quote arrives on the supplier's own
    terms -- per carton, per sachet, freight in or out -- and every figure
    worth comparing is derived from those terms plus the tender and the
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
        context["tender"] = self.op("tender_get", tender_id=quote["tender_id"])
        context["supplier"] = self.op("supplier_get", supplier_id=quote["supplier_id"])
        # The product quoted, so each derived figure names its unit the way the
        # comparison's header does ("USD per jerry can") rather than "per pack".
        context["commodity"] = next(
            (c for c in self.op("commodity_list") if c.get("slug") == quote.get("commodity_slug")), None
        )
        # A quote carries its own evidence now -- a quotation PDF, or the
        # pro-forma invoice a price came off, which is what EHA's note cites.
        # The supplier's own documents are shown separately rather than mixed
        # in: a trading licence belongs to the company, not to this offer.
        # Anything attached through this page's "Attach document" -- a
        # product's registration included -- is filed with the quote.
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
            o
            for o in self.op("outreach_list", tender_id=quote["tender_id"])
            if o["supplier_id"] == quote["supplier_id"]
        ]
        context["questions"] = detail["missing"]
        # The trade item quoted and, for a kit, what one unit of it holds --
        # the fact a co-pack is chosen or set aside on, which otherwise lived
        # only in the comparison and in a void reason typed by hand.
        item = self.op("item_get", item_id=quote["item_id"]) if quote.get("item_id") else None
        context["item"] = item
        if item and item.get("components"):
            names = _commodity_names(self.op("commodity_list"))
            context["contents"] = " + ".join(
                f"{part.get('quantity')} {part.get('base_unit')} {names.get(slug, slug)}"
                for part in item["components"]
                for slug in [part.get("commodity_slug")]
            )
        return context


def _priced_per(quote, item, commodity) -> str | None:
    """The unit a price is per, as the product names it: "per co-pack", not "per base unit".

    None when the basis is not a unit of the product (a lot total), so the
    template falls back to the basis in words.
    """
    field = {"per_base_unit": "base_unit", "per_pack": "pack_unit"}.get(quote.get("as_quoted_unit"))
    if field is None:
        return None
    unit = (item or {}).get(field) or (commodity or {}).get(field)
    return f"per {unit_noun(unit)}" if unit else None


def table_columns(comparison) -> list:
    """The ranked table's columns, with the landed total shown once when it is one figure.

    "Landed total (as quoted)" and "Landed total (this tender)" differ only
    when a quote was priced on another quantity. When every ranked row has the
    same figure in both, the second column repeated the first and pushed the
    award marker off the right edge of a 1280px screen.
    """
    columns = list(comparison.get("columns") or [])
    rows = comparison.get("comparable") or []
    as_quoted, this_tender = "landed_total_as_quoted", "landed_total_for_tender_quantity"
    if rows and all(
        (row.get("figures") or {}).get(as_quoted) == (row.get("figures") or {}).get(this_tender)
        and (row.get("figures") or {}).get(this_tender, {}).get("amount") is not None
        for row in rows
    ):
        columns = [column for column in columns if column.get("key") != as_quoted]
    return [column for column in columns if column.get("key") not in folded_columns(comparison, columns)]


def folded_columns(comparison, columns=None) -> dict:
    """{key: label} of the per-course figures that repeat the first column in every ranked row.

    A co-pack IS one course, so "USD per course" and "USD per child treated"
    printed the per-co-pack price twice more and pushed the table past a
    1280px screen, cutting a header mid-word (the CHC render). They are
    dropped only when they say nothing new, and the page says they were.
    """
    from connect_labs.supply_chain.procurement.services.comparison import COURSE_FIGURES

    columns = list(columns if columns is not None else comparison.get("columns") or [])
    rows = comparison.get("comparable") or []
    if not columns or not rows:
        return {}
    first = columns[0].get("key")

    def amount(row, key):
        return ((row.get("figures") or {}).get(key) or {}).get("amount")

    return {
        column["key"]: column.get("label", column["key"])
        for column in columns
        if column.get("key") in COURSE_FIGURES
        and column.get("key") != first
        and all(
            amount(row, column["key"]) is not None and amount(row, column["key"]) == amount(row, first) for row in rows
        )
    }


def _commodity_names(commodities) -> dict:
    """slug -> name, tolerating anything that is not a list of commodity rows."""
    if not isinstance(commodities, list):
        return {}
    return {c["slug"]: c.get("name") or c["slug"] for c in commodities if isinstance(c, dict) and c.get("slug")}


class ComparisonView(_Base):
    template_name = "supply_chain/procurement/comparison.html"

    def get_context_data(self, tender_id, **kwargs):
        context = super().get_context_data(**kwargs)
        tender = self.op("tender_get", tender_id=tender_id)
        # The `or {}` below tolerated a missing tender as far as here and then
        # `tender_compare` raised on it, so a stale link 500'd. A tender that is
        # not in this programme is a 404.
        if tender is None:
            raise Http404(f"no tender {tender_id} in this programme")
        lines = tender.get("lines") or []
        commodity = self.request.GET.get("commodity")

        # tender_compare's schema requires commodity_slug as a string — nothing
        # ambiguous is a safe default. But a bookmark, browser-history entry,
        # or shared link with no ?commodity= at all is a normal way to land
        # here, and it must not 500. A tender with exactly one line has one
        # sensible default; more than one (or none) means asking, which is
        # also more useful than an error: it's a worklist of what to compare.
        if not commodity and len(lines) == 1:
            commodity = lines[0].get("commodity_slug")

        comparison = self.op("tender_compare", tender_id=tender_id, commodity_slug=commodity) if commodity else None
        # The awards already made on this line, so the page that awards is
        # also the way to one -- and to the approvals it may be waiting on.
        context["awards"] = (
            [a for a in self.op("award_list", tender_id=tender_id) if a["commodity_slug"] == commodity]
            if commodity
            else []
        )

        context["tender"] = tender
        context["tender_id"] = tender_id
        context["commodity_slug"] = commodity
        # The product's name, not its slug: "ors-zinc-copack" is an identifier.
        names = _commodity_names(self.op("commodity_list")) if commodity else {}
        context["commodity_name"] = names.get(commodity) or commodity
        # The offers already chosen, so the page marks them rather than
        # offering to award them again.
        context["awarded_quote_ids"] = {a.get("quote_id") for a in context["awards"] if isinstance(a, dict)}
        context["today"] = date.today().isoformat()
        # Who decides, by name: the award form's "decided by" starts as the
        # signed-in person's display name, never their login or email -- on a
        # shared or service account that read as the account, not the person.
        # Prefilled only with a person's name. A login handle ("ace") is the
        # account recording the award, not the person who decided it, and
        # prefilled it reads as though somebody called that made the choice.
        context["decider"] = _person_name(self.request.user)
        # Offers set aside on this line. A voided quote leaves the ranking, and
        # without this it left the page too -- so the one screen that applies
        # "kits rank only against the same contents" never showed an offer the
        # rule had excluded, or the reason it was excluded.
        context["set_aside"] = []
        if commodity:
            quotes = self.op("quote_list", tender_id=tender_id)
            items = {}
            for quote in quotes if isinstance(quotes, list) else []:
                if not (isinstance(quote, dict) and quote.get("voided") and quote.get("commodity_slug") == commodity):
                    continue
                item_id = quote.get("item_id")
                if item_id and item_id not in items:
                    items[item_id] = self.op("item_get", item_id=item_id)
                context["set_aside"].append({"quote": quote, "item": items.get(item_id)})
        context["comparison"] = comparison
        context["table_columns"] = table_columns(comparison) if comparison else []
        if comparison and context["table_columns"]:
            context["folded_columns"] = list(folded_columns(comparison).values())
            context["first_column_label"] = context["table_columns"][0].get("label")
        # ranked_by is a bare figure key (e.g. "landed_total_for_tender_quantity");
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

    def post(self, request, tender_id, *args, **kwargs):
        """Award a quote. The Award button's form posts here (action="" —
        same URL, so the ?commodity= query string tender-trips for free).

        A missing or empty rationale is refused by award_create's schema, not
        guessed around here — that refusal must not become a 500: catch it
        and re-render the page with what's wrong, the same way a browser
        form re-shows itself on a validation error.
        """
        quote_id_raw = request.POST.get("quote_id")
        rationale = request.POST.get("rationale", "")
        decided_on = request.POST.get("decided_on") or None
        decided_by = (request.POST.get("decided_by") or "").strip() or _display_name(request.user)
        try:
            self.op(
                "award_create",
                tender_id=tender_id,
                quote_id=int(quote_id_raw),
                rationale=rationale,
                decided_by=decided_by,
                **({"decided_on": decided_on} if decided_on else {}),
            )
        except jsonschema.ValidationError as exc:
            context = self.get_context_data(tender_id=tender_id, **kwargs)
            context["award_error"] = exc.message
            return self.render_to_response(context)
        except (TypeError, ValueError):
            context = self.get_context_data(tender_id=tender_id, **kwargs)
            context["award_error"] = "No valid quote was selected to award."
            return self.render_to_response(context)

        url = reverse("supply_chain:procurement_comparison", args=[tender_id])
        commodity = request.GET.get("commodity")
        return redirect(f"{url}?commodity={commodity}" if commodity else url)


# FollowupDraftView used to render a ready-to-send follow-up email here.
# It is gone from the product on purpose (design doc section 22): the
# `followup_render` OPERATION remains, so a client -- an agent, a script, a
# person hitting the API -- can ask for the text. What the product no longer
# does is press a drafted message on the user as the next thing to do.
# Prioritising and phrasing are judgements about what matters today, which a
# client can make better than a hardcoded page can.


def _person_name(user) -> str:
    """The signed-in person's own name, or "" when all we have is a handle.

    `User.name` is free text, and on a shared or service account it holds the
    login itself. Prefilled into "Decided by", that read as though somebody
    called "ace" had made the purchasing decision -- the exact reading the
    field exists to prevent. A name that IS the account's handle is not a
    person's name, so nothing is prefilled and the decider types who decided.
    """
    name = (getattr(user, "name", "") or "").strip()
    if not name:
        # Not `get_full_name()`: it joins the two halves unconditionally, so a
        # user with both unset reads "None None" -- which would then be
        # prefilled as the person who decided.
        name = " ".join(
            str(part).strip() for part in (getattr(user, "first_name", ""), getattr(user, "last_name", "")) if part
        ).strip()
    handles = {
        (user.get_username() or "").strip().lower(),
        (getattr(user, "email", "") or "").split("@")[0].strip().lower(),
    }
    handles.discard("")
    return "" if name.lower() in handles else name


def _display_name(user) -> str:
    """The name a person goes by, for "decided by": their name, not their login."""
    if hasattr(user, "get_display_name"):
        return user.get_display_name()
    return user.get_full_name() or user.get_username()


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
        # Arriving from a tender, that tender is the answer.
        tender_id = self.request.GET.get("tender")
        if tender_id and str(tender_id).isdigit():
            initial.setdefault("tender", int(tender_id))
        return initial

    def breadcrumb(self, **kwargs):
        return [
            {"label": "Sourcing", "href": reverse("supply_chain:procurement_tender_board")},
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:procurement_tender_board")

    def redirect_to(self, result):
        url = reverse("supply_chain:procurement_comparison", args=[result["tender_id"]])
        return f"{url}?commodity={result['commodity_slug']}"


# ---- write screens -------------------------------------------------------
#
# Each is a declaration: which operation, what to call it, and where to go
# afterwards. The form comes from the model (connect_labs/supply_chain/forms.py)
# and the page from one shared template, so a screen carries no markup and no
# validation of its own.


class _TenderScreen(OperationFormView):
    """Create or update a tender, header plus its commodity lines.

    A tender asks for one or more commodities, each with its own quantity and
    unit, so the lines are a formset rather than a JSON textarea -- which is
    what a ModelForm would render `Tender.lines` as.
    """

    form_class = TenderForm
    template_name = "supply_chain/procurement/tender_form.html"

    def commodities(self):
        return [(c["slug"], c["name"]) for c in self.op("commodity_list")]

    def line_formset(self, data=None, initial=None):
        return TenderLineFormSet(
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
            form.add_error(None, "A tender has to ask for at least one commodity.")
            return self.render_to_response(self.get_context_data(form=form, lines=lines))

        self._lines = kept
        return super().form_valid(form)

    def fixed(self, **kwargs):
        return {"data": {"lines": getattr(self, "_lines", [])}}

    def breadcrumb(self, **kwargs):
        return [
            {"label": "Sourcing", "href": reverse("supply_chain:procurement_tender_board")},
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:procurement_tender_board")

    def redirect_to(self, result):
        return reverse("supply_chain:procurement_tender_detail", args=[result["id"]])


class TenderCreateView(_TenderScreen):
    operation = "tender_create"
    title = "New quote tender"
    intro = (
        "A tender is one ask, to several suppliers, for the same thing. It opens in draft: "
        "nothing goes out until you open it."
    )
    submit_label = "Create tender"


class TenderUpdateView(_TenderScreen):
    operation = "tender_update"
    title = "Edit tender"
    submit_label = "Save changes"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self._tender_instance()
        return kwargs

    def _tender_instance(self):
        from connect_labs.supply_chain.models import Tender

        found = Tender.objects.filter(pk=self.kwargs["tender_id"], program_id=_access(self.request).program_id).first()
        if found is None:
            raise Http404(f"no tender {self.kwargs['tender_id']} in this programme")
        return found

    def initial_lines(self):
        return [
            {
                "commodity_slug": line.get("commodity_slug"),
                "quantity": line.get("quantity"),
                "quantity_unit": line.get("quantity_unit"),
            }
            for line in (self._tender_instance().lines or [])
        ]

    def fixed(self, **kwargs):
        return {"tender_id": int(kwargs["tender_id"]), "data": {"lines": getattr(self, "_lines", [])}}


class TenderOpenView(OperationActionView):
    operation = "tender_open"
    success_message = "Tender opened — it can take quotes now."

    def fixed(self, **kwargs):
        return {"tender_id": int(kwargs["tender_id"])}

    def redirect_to(self, **kwargs):
        return reverse("supply_chain:procurement_tender_detail", args=[kwargs["tender_id"]])


class TenderCloseView(OperationActionView):
    operation = "tender_close"
    success_message = "Tender closed to further quotes."

    def fixed(self, **kwargs):
        return {"tender_id": int(kwargs["tender_id"])}

    def redirect_to(self, **kwargs):
        return reverse("supply_chain:procurement_tender_detail", args=[kwargs["tender_id"]])


class OutreachLogView(OperationFormView):
    operation = "outreach_log"
    form_class = OutreachForm
    title = "Record an invitation"
    intro = (
        "That we asked this supplier to quote on this tender. A log, not a state machine — "
        "re-inviting is a real event worth keeping."
    )
    submit_label = "Record invitation"

    def fixed(self, **kwargs):
        return {"data": {"tender_id": int(kwargs["tender_id"])}}

    def breadcrumb(self, **kwargs):
        return [
            {"label": "Sourcing", "href": reverse("supply_chain:procurement_tender_board")},
            {"label": "Tender", "href": reverse("supply_chain:procurement_tender_detail", args=[kwargs["tender_id"]])},
            {"label": "Record an invitation"},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:procurement_tender_detail", args=[kwargs["tender_id"]])

    def redirect_to(self, result):
        return reverse("supply_chain:procurement_tender_detail", args=[result["tender_id"]])


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
            pk=self.kwargs["outreach_id"], tender__program_id=_access(self.request).program_id
        ).first()
        if found is None:
            raise Http404(f"no invitation {self.kwargs['outreach_id']} in this programme")
        return found

    def fixed(self, **kwargs):
        return {"outreach_id": int(kwargs["outreach_id"])}

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:procurement_tender_detail", args=[self._outreach().tender_id])

    def redirect_to(self, result):
        return reverse("supply_chain:procurement_tender_detail", args=[result["tender_id"]])


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
        return reverse("supply_chain:procurement_tender_detail", args=[result["tender_id"]])


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
    """An award, reached through its tender's programme, or a 404."""
    from connect_labs.supply_chain.models import Award

    found = (
        Award.objects.filter(pk=award_id, tender__program_id=_access(request).program_id)
        .select_related("supplier__org__supplier_profile", "commodity", "tender")
        .first()
    )
    if found is None:
        raise Http404(f"no award {award_id} in this programme")
    return found


def approvals_as_read(op, award_id) -> list[dict]:
    """An award's approvals as its page reads them: each with its approver and evidence.

    The documents attached to it (the approver's letter), the one it rests on
    (a product registration), and whether the approver answered through its
    own link. One reading, shared by the award page and the order placed
    against the award, so the two cannot describe the same approval apart.
    `op` is a view's `op`, so the reads run as that view's caller.
    """
    approvals = op("approval_list", award_id=award_id)
    if not approvals:
        return []
    orgs = {o["id"]: o for o in op("org_list")}
    documents = {d["id"]: d for d in op("document_list")}
    return [
        {
            **a,
            "approver": orgs.get(a["approver_org_id"]),
            "documents": [documents[i] for i in a.get("document_ids") or [] if i in documents],
            "rests_on": documents.get(a.get("rests_on_document_id")),
            # Answered by the approver itself, through its own link.
            "answered_by_approver": a.get("decision_source") == "partner_reported"
            and a.get("decision_recorded_by_org_id") == a["approver_org_id"],
        }
        for a in approvals
    ]


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
        detail = next((a for a in self.op("award_list", tender_id=award.tender_id) if a["id"] == award.pk), None)
        if detail is None:
            raise Http404(f"no award {award_id} in this programme")
        context["award"] = detail
        # The heading named the product's slug; what was awarded is a kit.
        context["awarded_item"] = award.quote.item.name if award.quote.item_id else None
        context["supplier"] = self.op("supplier_get", supplier_id=detail["supplier_id"])
        context["tender"] = self.op("tender_get", tender_id=detail["tender_id"])
        context["approvals"] = approvals_as_read(self.op, award.pk)
        # The same rule the order guard applies: a refusal later reversed by
        # a fresh approval from the same approver in the same role is history.
        blocking_ids = {a.pk for a in _access(self.request).blocking_approvals(award)}
        context["blocking"] = [a for a in context["approvals"] if a["id"] in blocking_ids]
        context["contracts"] = [
            c for c in self.op("contract_list", tender_id=detail["tender_id"]) if c["award_id"] == award.pk
        ]
        # The offer the award froze, read from the comparison it was chosen
        # from rather than recomputed here -- so the award and the ranking can
        # never disagree about the money. Without it the award named a kit, a
        # decider and a reason, and stated no quantity, price or total above
        # its own "Place order" button; and it never said the awarded kit met
        # the specification it was chosen against (the test-kit render).
        context["awarded_row"] = None
        context["awarded_columns"] = []
        comparison = self.op("tender_compare", tender_id=detail["tender_id"], commodity_slug=detail["commodity_slug"])
        if comparison:
            rows = list(comparison.get("comparable") or []) + list(comparison.get("all_rows") or [])
            context["awarded_row"] = next((r for r in rows if r.get("quote_id") == award.quote_id), None)
            context["awarded_columns"] = table_columns(comparison)
        # How many the money is for. "USD 760.00 landed" means nothing without
        # it, and the comparison's own column says only "(this tender)".
        sought = self.op("tender_get", tender_id=detail["tender_id"]) or {}
        for line in sought.get("lines") or []:
            if line.get("commodity_slug") == detail["commodity_slug"] and line.get("quantity") not in (None, ""):
                context["tender_quantity"] = quantity_phrase(line["quantity"], line.get("quantity_unit"))
                break
        return context


class _AwardScreen(OperationFormView):
    def breadcrumb(self, **kwargs):
        award = self.award()
        return [
            {"label": "Sourcing", "href": reverse("supply_chain:procurement_tender_board")},
            {
                "label": award.tender.label,
                "href": reverse("supply_chain:procurement_tender_detail", args=[award.tender_id]),
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


class ApprovalDocumentAttachView(_AwardScreen):
    """The approver's letter or email, attached to the approval it records."""

    operation = "document_attach"
    form_class = DocumentForm
    title = "Attach a document to this approval"
    submit_label = "Attach"
    footnote = "Over 12 MB, store it elsewhere and give a link."

    def approval(self):
        from connect_labs.supply_chain.models import AwardApproval

        found = (
            AwardApproval.objects.filter(
                pk=self.kwargs["approval_id"], award__tender__program_id=_access(self.request).program_id
            )
            .select_related("approver_org")
            .first()
        )
        if found is None:
            raise Http404(f"no approval {self.kwargs['approval_id']} in this programme")
        return found

    def award(self):
        return _award(self.request, self.approval().award_id)

    @property
    def intro(self):
        approval = self.approval()
        return (
            f"Evidence for {approval.approver_org.name}'s {approval.role} approval of this award — their "
            "letter, their email, the registration they granted. Upload the file or link to where it lives."
        )

    def get_initial(self):
        initial = super().get_initial()
        initial["kind"] = "product_registration" if self.approval().role == "regulatory" else "other"
        return initial

    def fixed(self, **kwargs):
        return {"data": {"approval_id": int(kwargs["approval_id"])}}


class QuoteDocumentAttachView(OperationFormView):
    """A document filed with this offer: the quotation, a pro-forma, the product's registration."""

    operation = "document_attach"
    form_class = DocumentForm
    title = "Attach a document to this quote"
    intro = (
        "The quotation as the supplier sent it, the pro-forma invoice its price came off, or the "
        "product's registration or certificate sent with it. Upload the file or link to where it lives."
    )
    submit_label = "Attach"
    footnote = "Over 12 MB, store it elsewhere and give a link."

    def quote(self):
        from connect_labs.supply_chain.models import Quote

        found = (
            Quote.objects.filter(pk=self.kwargs["quote_id"], tender__program_id=_access(self.request).program_id)
            .select_related("supplier__org__supplier_profile", "tender")
            .first()
        )
        if found is None:
            raise Http404(f"no quote {self.kwargs['quote_id']} in this programme")
        return found

    def breadcrumb(self, **kwargs):
        quote = self.quote()
        return [
            {"label": "Sourcing", "href": reverse("supply_chain:procurement_tender_board")},
            {
                "label": quote.tender.label,
                "href": reverse("supply_chain:procurement_tender_detail", args=[quote.tender_id]),
            },
            {
                "label": f"Quote from {quote.supplier.name}",
                "href": reverse("supply_chain:procurement_quote_detail", args=[quote.pk]),
            },
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:procurement_quote_detail", args=[self.kwargs["quote_id"]])

    def redirect_to(self, result):
        return reverse("supply_chain:procurement_quote_detail", args=[self.kwargs["quote_id"]])

    def get_initial(self):
        initial = super().get_initial()
        initial["kind"] = "quotation"
        return initial

    def fixed(self, **kwargs):
        return {"data": {"quote_id": int(kwargs["quote_id"])}}


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
            pk=self.kwargs["approval_id"], award__tender__program_id=_access(self.request).program_id
        ).first()
        if found is None:
            raise Http404(f"no approval {self.kwargs['approval_id']} in this programme")
        return found

    def award(self):
        return _award(self.request, self.approval().award_id)

    def fixed(self, **kwargs):
        return {"approval_id": int(kwargs["approval_id"])}

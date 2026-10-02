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

from datetime import date
from types import SimpleNamespace

import jsonschema
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.banner import program_line
from connect_labs.supply_chain.form_views import OperationActionView, OperationFormView
from connect_labs.supply_chain.fulfilment.forms import DocumentForm
from connect_labs.supply_chain.history.timeline import (
    ai_entered_quotes,
    corrections_for_quotes,
    reminders_for_outreach,
    timeline_for_tender,
)
from connect_labs.supply_chain.identity import person_name as _person_name
from connect_labs.supply_chain.navigation import supply_tabs
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.procurement.forms import (
    ApprovalDecisionForm,
    ApprovalRequestForm,
    CommitmentForm,
    CommitmentResolveForm,
    OutreachChaseForm,
    OutreachForm,
    OutreachReplyForm,
    QuoteForm,
    ReasonForm,
    TenderForm,
    TenderLineFormSet,
    TenderPlaceFormSet,
)
from connect_labs.supply_chain.procurement.services.comparison import RANKING_RULE
from connect_labs.supply_chain.values import quantity_phrase, unit_noun
from connect_labs.supply_chain.views import mark_changed, owed_context


def _ordinal(n: int) -> str:
    """1st, 2nd, 3rd, 4th ... 11th, 12th, 13th ... 21st."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _deadline_passed(tender, as_of=None):
    """For an open tender whose response deadline is behind it: the deadline and how long ago.

    `{"day": date, "days": 3}`, or None. An open tender read "response deadline
    29 Sep 2026" on 2 Oct as if that were still to come. Counted from the as-of
    day on a rewound page, so it says what the page said then.
    """
    if (tender or {}).get("status") != "open" or not tender.get("response_deadline"):
        return None
    try:
        deadline = date.fromisoformat(str(tender["response_deadline"])[:10])
    except ValueError:
        return None
    days = ((as_of or date.today()) - deadline).days
    return {"day": deadline, "days": days} if days > 0 else None


def _days_since_ask(outreach, as_of=None):
    """Days since we asked: the invitation's send day; None without one.

    A reminder chases the same request, it does not restart it -- the overview's
    "no reply since" counts from the ask too, and the two must agree. How recently
    we chased is the Last chased column's job.
    """
    if not outreach.get("sent_on"):
        return None
    try:
        latest = date.fromisoformat(str(outreach["sent_on"])[:10])
    except ValueError:
        return None
    return max(((as_of or date.today()) - latest).days, 0)


def _mark_waiting_on_us(outreach, owed_open, org_of_supplier):
    """Put "waiting on us — 3 questions since 18 Sep" on a supplier's row while its questions are open.

    A reply that was questions is not the supplier's silence: the move is ours,
    and the round should say so where the supplier is listed. Counts the open
    questions owed to the supplier's organisation on this tender, from the
    earliest day one was asked.
    """
    for o in outreach:
        org_id = org_of_supplier.get(o.get("supplier_id"))
        mine = [c for c in owed_open if org_id is not None and c.get("owed_to_org_id") == org_id]
        o["waiting_on_us"] = ""
        if not mine:
            continue
        questions = [c for c in mine if c.get("kind") == "question"]
        counted = questions or mine
        noun = "question" if questions else "promise"
        earliest = min((c.get("raised_on") for c in counted if c.get("raised_on")), default=None)
        since = ""
        if earliest:
            day = date.fromisoformat(str(earliest)[:10])
            since = f" since {day.day} {day.strftime('%b')}"
        o["waiting_on_us"] = f"waiting on us — {len(counted)} {noun}{'' if len(counted) == 1 else 's'}{since}"


def _changed(url, key, anchor):
    """`url` arriving back at the row a form just saved: "?changed=outreach-12#outreach"."""
    return f"{url}?changed={key}#{anchor}"


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
        context["supply_program_line"] = program_line(self.request)
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
        as_of = getattr(self.request, "supply_as_of", None)
        outreach = self.op("outreach_list", tender_id=tender_id)
        # How many times each supplier has been chased, from the history: the
        # record holds only the last day ("25 Sep 2026 · 2nd reminder").
        reminders = reminders_for_outreach(
            [o.get("id") for o in outreach], program_id=_access(self.request).program_id, until=as_of
        )
        for o in outreach:
            count = reminders.get(o.get("id"), 0)
            if o.get("last_reminder_on"):
                count = max(count, 1)
            o["reminder_count"] = count
            o["reminder_text"] = f"{_ordinal(count)} reminder" if count else ""
            # Silent since the latest ask -- the invitation or the last chase --
            # not since the invitation: a chase restarts the wait.
            o["silent_days"] = None if o.get("responded") else _days_since_ask(o, as_of)
        # The row a form just saved is picked out on arrival ("?changed=outreach-12").
        changed = self.request.GET.get("changed")
        context["outreach"] = mark_changed(outreach, "outreach", changed)
        context["deadline_passed"] = _deadline_passed(tender, as_of)
        # Someone asked has not answered, on a round still taking quotes: the
        # page's first action is chasing them, ahead of comparing what came in.
        context["any_silent"] = tender.get("status") == "open" and any(not o.get("responded") for o in outreach)
        # The tender's own title and its outreach lead; the supply banner steps back.
        context["compact_banner"] = True
        context.update(
            owed_context(self.op("commitment_list", tender_id=tender_id), changed, _access(self.request).program_id)
        )
        # The emails due on this round now, for Sophie to copy into her own
        # mailbox. Listed in the order a round runs and then by name -- not
        # ranked, and folded shut (design doc section 22: the product drafts
        # when asked; it does not press a next action). Not on a page rewound
        # with ?as_of=: what is due is a fact about today.
        context["drafts"] = (
            None
            if getattr(self.request, "supply_as_of", None)
            else self.op("tender_drafts_render", tender_id=tender_id)
        )
        # Each supplier's first draft carries an anchor, so the comparison's
        # "Draft email to <supplier>" opens the panel at that supplier's.
        anchored = set()
        for d in (context["drafts"] or {}).get("drafts") or []:
            if d.get("supplier_id") is not None and d["kind"] != "reply" and d["supplier_id"] not in anchored:
                anchored.add(d["supplier_id"])
                d["anchor"] = f"draft-supplier-{d['supplier_id']}"
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
        # "Outreach — 3 of 6 replied": a supplier has replied when it answered or quoted.
        asked = {o.get("supplier_id") for o in outreach if o.get("supplier_id")}
        quoted = {q.get("supplier_id") for q in context["quotes"] if not q.get("voided")}
        answered = {o.get("supplier_id") for o in outreach if o.get("responded")}
        context["outreach_asked"] = len(asked)
        context["outreach_replied"] = len(asked & (answered | quoted))
        # Who was asked, from the outreach, for the invited panel when nobody
        # is on the marketplace invitation list.
        context["asked_names"] = list(
            dict.fromkeys(context["supplier_names"].get(o.get("supplier_id"), "Supplier") for o in outreach)
        )
        context["tender_is_over"] = tender.get("status") in ("closed", "awarded")
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
        from connect_labs.labs.models import LabsOrg

        tender = context.get("tender") or {}
        owner_id = tender.get("owner_org_id")
        if owner_id:
            context["owner_name"] = LabsOrg.objects.filter(pk=owner_id).values_list("name", flat=True).first() or ""
        from connect_labs.supply_chain.models import Supplier

        invited_ids = tender.get("invited_org_ids") or []
        context["invited_orgs"] = LabsOrg.objects.filter(pk__in=invited_ids).order_by("name")
        # Not anyone already invited, or already asked directly (on the outreach
        # above): offering them again reads as if they had not been.
        access = _access(self.request)
        asked_supplier_ids = [o.get("supplier_id") for o in outreach if o.get("supplier_id")]
        org_of_supplier = (
            dict(
                Supplier.objects.filter(scope_key=access.scope_key, pk__in=asked_supplier_ids).values_list(
                    "pk", "org_id"
                )
            )
            if asked_supplier_ids and access.program_id
            else {}
        )
        asked_org_ids = list(org_of_supplier.values())
        _mark_waiting_on_us(outreach, context.get("owed_open") or [], org_of_supplier)
        context["invitable_orgs"] = (
            LabsOrg.objects.filter(supplier_profile__isnull=False)
            .exclude(pk__in=invited_ids)
            .exclude(pk__in=asked_org_ids)
            .order_by("name")[:200]
        )
        # What changed on this tender and its children, and who told us. Scoped
        # by this program as well as the tender, and cut at the as-of date.
        context["timeline"] = timeline_for_tender(
            tender_id,
            program_id=_access(self.request).program_id,
            until=getattr(self.request, "supply_as_of", None),
        )
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
        from connect_labs.supply_chain.models import Quote as _Quote
        from connect_labs.supply_chain.procurement.services.comparison import delivery_words

        found = (
            _Quote.objects.select_related("tender")
            .filter(pk=quote_id, tender__program_id=_access(self.request).program_id)
            .first()
        )
        context["delivery_words"] = delivery_words(found, found.tender) if found else ""
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
    columns = [column for column in columns if column.get("key") not in folded_columns(comparison, columns)]
    return _without_empty_tail(rows, _one_unit_column(comparison, columns))


def _without_empty_tail(rows, columns) -> list:
    """The columns less any at the right that no ranked row has a figure in.

    An all-blank column at the end of the table is width spent on nothing. A
    blank column between figures stays: dropping it would move the columns
    after it out from under their headers in the reader's memory of the page.
    """
    columns = list(columns)

    def has_figure(column):
        for row in rows:
            cell = (row.get("figures") or {}).get(column.get("key"))
            if isinstance(cell, dict) and (cell.get("unconfirmed") or cell.get("amount") is not None):
                return True
        return False

    while columns and rows and not has_figure(columns[-1]):
        columns.pop()
    return columns


def _and_list(words) -> str:
    words = [w for w in words if w]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1] if words else ""


def not_stated(row) -> str:
    """ "Kanem Foods Ltd has not stated sachets per carton": what keeps one quote out, as a sentence.

    From the card's own blockers -- the gap words the overview's flag uses --
    so the banner, the button and the card name the same facts.
    """
    labels = list(dict.fromkeys(b.get("label") or b.get("fact") or "" for b in row.get("blockers") or []))
    if not labels:
        return ""
    return f"{row.get('supplier_name') or 'A supplier'} has not stated {_and_list(labels)}"


def award_anyway(comparison) -> str:
    """ "Kanem Foods Ltd has not stated sachets per carton", while any quote is blocked on a fact; else "".

    The first blocked quote's sentence, and how many more are waiting.
    """
    blocked = [row for row in (comparison or {}).get("blocked") or [] if row.get("blockers")]
    if not blocked:
        return ""
    text = not_stated(blocked[0])
    if len(blocked) > 1:
        text += f" (and {len(blocked) - 1} more)"
    return text


# What the ranking compares, in words, for the banner: "can be compared on cost per course".
_COMPARED_ON = {
    "usd_per_course": "cost per course",
    "usd_per_child_treated": "cost per child treated",
    "landed_total_for_tender_quantity": "landed total",
    "landed_total_as_quoted": "landed total",
}


def compared_on(comparison, columns) -> str:
    """What the comparison compares quotes on, in words; "" when nothing ranks them."""
    key = (comparison or {}).get("ranked_by")
    if not key:
        return ""
    if key in _COMPARED_ON:
        return _COMPARED_ON[key]
    column = next((c for c in columns or [] if c.get("key") == key), None)
    label = (column or {}).get("label") or ""
    return label if label[:3].isupper() else label[:1].lower() + label[1:]


UNIT_KEYS = ("usd_per_pack_normalized", "usd_per_course", "usd_per_child_treated")


def _one_unit_column(comparison, columns) -> list:
    """Per pack, per course and per child treated as ONE column when they are one figure.

    When 1 carton = 1 course = 1 child in every ranked row, three equal columns
    read as a repeated number; one headed "USD per carton (= 1 course = 1 child
    treated)" says the figure once and why it stands for all three.
    """
    equivalence = unit_equivalence(comparison, columns)
    if not equivalence:
        return columns
    out = []
    for column in columns:
        key = column.get("key")
        if key == UNIT_KEYS[0]:
            column = {**column, "label": f"{column.get('label')} (one course)", "one_course": True}
        elif key in UNIT_KEYS[1:]:
            continue
        out.append(column)
    return out


def unit_equivalence(comparison, columns) -> str:
    """ "1 carton = 1 course = 1 child treated", when the table's per-pack, per-course and per-child
    figures are the same in every ranked row -- said once, so three equal columns read as meant.

    "" when any of the three is not shown, or any row differs.
    """
    keys = ("usd_per_pack_normalized", "usd_per_course", "usd_per_child_treated")
    shown = {column.get("key") for column in columns or []}
    rows = comparison.get("comparable") or []
    if not rows or not all(key in shown for key in keys):
        return ""
    for row in rows:
        amounts = {((row.get("figures") or {}).get(key) or {}).get("amount") for key in keys}
        if len(amounts) != 1 or None in amounts:
            return ""
    pack = rows[0].get("pack_unit") or "pack"
    return f"1 {unit_noun(pack, 1)} = 1 course = 1 child treated"


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


def _spec_sources(comparison, corrections) -> None:
    """Stop calling a correction's figure "stated on the quote".

    After a supplier's reply supplies the pack, "Stated on the quote: sachets
    per carton" was not true -- the quote never said it; the email did. The
    figures a correction supplied are taken out of the stated-on-the-quote
    lists, and nothing replaces them: the row's correction-source line
    ("Corrected 28 Aug by ... from <supplier> email") already says where the
    value came from, and a second "Sachets per carton: from supplier email,
    28 Aug" line beside it said it twice. Edits the comparison's rows in place.
    """
    for row in comparison.get("comparable") or []:
        correction = corrections.get(row.get("quote_id"))
        specification = row.get("specification")
        if not correction or not specification:
            continue
        supplied = set(correction.get("labels") or [])
        specification["stated_on_quote"] = [
            label for label in specification.get("stated_on_quote") or [] if label not in supplied
        ]
        specification["stated_values"] = [
            value for value in specification.get("stated_values") or [] if value.get("label") not in supplied
        ]


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
        commodities = self.op("commodity_list") if commodity else []
        names = _commodity_names(commodities)
        context["commodity_name"] = names.get(commodity) or commodity
        # The offers already chosen, so the page marks them rather than
        # offering to award them again.
        context["awarded_quote_ids"] = {a.get("quote_id") for a in context["awards"] if isinstance(a, dict)}
        context["today"] = date.today().isoformat()
        # Who decides is who is signed in: shown on the award form, not asked,
        # and recorded from the request by `post` whatever is posted.
        context["decider"] = _decider(self.request.user)
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
        # The first offer that can still be awarded carries id="award" on its
        # ranked ROW, so a link ending "#award" frames the supplier, its figures
        # and the award form under them together. One per page: ids are unique.
        context["award_anchor_quote_id"] = next(
            (
                row.get("quote_id")
                for row in (comparison or {}).get("comparable") or []
                if row.get("quote_id") not in context["awarded_quote_ids"]
            ),
            None,
        )
        # Which offers an AI entered, so each carries the same indigo AI pill as
        # the timeline and the overview -- the reader checks those first.
        context["ai_quotes"] = (
            ai_entered_quotes(
                [row.get("quote_id") for row in comparison.get("all_rows") or []],
                program_id=_access(self.request).program_id,
            )
            if comparison
            else {}
        )
        # Offers a correction brought into the ranking, and what it changed,
        # so why an offer joined is read on its row rather than hunted for.
        context["corrections"] = (
            corrections_for_quotes(
                [row.get("quote_id") for row in comparison.get("comparable") or []],
                program_id=_access(self.request).program_id,
                until=getattr(self.request, "supply_as_of", None),
            )
            if comparison
            else {}
        )
        if comparison:
            _spec_sources(comparison, context["corrections"])
        context["history_url"] = reverse("supply_chain:procurement_tender_detail", args=[tender_id]) + "#history"
        context["cost_basis"] = (
            cost_basis(next((c for c in commodities if isinstance(c, dict) and c.get("slug") == commodity), None))
            if comparison
            else ""
        )
        context["table_columns"] = table_columns(comparison) if comparison else []
        context["unit_equivalence"] = unit_equivalence(comparison, context["table_columns"]) if comparison else ""
        # One column standing for three ("USD per carton (one course)"): the equation is
        # said in the caption, not crammed into the header.
        if comparison and any(c.get("key") == UNIT_KEYS[0] and c.get("one_course") for c in context["table_columns"]):
            context["unit_equivalence"] = unit_equivalence(comparison, comparison.get("columns") or [])
        # Two lines that said one thing -- "1 carton = 150 sachets; a course is 150 sachets"
        # and "1 carton = 1 course = 1 child treated" -- as one basis line when a carton is a course.
        if context["unit_equivalence"] and context["cost_basis"]:
            pack = context["cost_basis"].split(";")[0].strip()
            context["cost_basis"] = f"Basis: {pack} = 1 course (one child treated)"
            context["unit_equivalence"] = ""
        context["compared_on"] = compared_on(comparison, (comparison or {}).get("columns"))
        context["not_stated"] = [
            sentence for sentence in (not_stated(row) for row in (comparison or {}).get("blocked") or []) if sentence
        ]
        context["ranking_rule"] = RANKING_RULE
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
        # The ranking basis is marked on its own column's header ("ranked by,
        # lowest first"), not in a label floating at the right above the table.
        # The floating label stays only for the case the column is not shown.
        context["ranked_by_key"] = (comparison or {}).get("ranked_by")
        context["ranked_in_table"] = any(c.get("key") == context["ranked_by_key"] for c in context["table_columns"])
        # One comparable offer is not a ranking: no "#", no "ranked by".
        context["single_offer"] = len((comparison or {}).get("comparable") or []) == 1
        context["award_anyway"] = award_anyway(comparison)
        # Arriving by a link to the award step (?step=award) opens the folded award fields:
        # the link already said "award", so a second click to reveal them is friction.
        context["award_step"] = self.request.GET.get("step") == "award"
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
        # Never from the form: a posted name would let anyone record an award
        # as somebody else's decision.
        decided_by = _decider(request.user)
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


def _decider(user) -> str:
    """Who an award made by this signed-in person is recorded as deciding: their name, else their display name."""
    return _person_name(user) or _display_name(user)


def cost_basis(commodity) -> str:
    """ "1 carton = 150 sachets; a course is 150 sachets": only what the commodity defines.

    The pack from the commodity's own units per pack, or else an "exactly"
    requirement on the pack count in its specification; the course from its
    ration table. "" when it defines neither.
    """
    from connect_labs.supply_chain.procurement.services.compliance import is_pack_count_field
    from connect_labs.supply_chain.values import quantity_digits

    if not isinstance(commodity, dict):
        return ""
    base, pack = commodity.get("base_unit") or "", commodity.get("pack_unit") or ""
    if not base:
        return ""
    parts = []
    per_pack = commodity.get("base_per_pack")
    if per_pack is None:
        units = SimpleNamespace(base_unit=base, pack_unit=pack)
        per_pack = next(
            (
                r.get("value")
                for r in commodity.get("spec_requirements") or []
                if isinstance(r, dict) and r.get("operator") == "==" and is_pack_count_field(r.get("field"), units)
            ),
            None,
        )
    if per_pack not in (None, "") and pack:
        parts.append(f"1 {unit_noun(pack, 1)} = {quantity_digits(per_pack)} {unit_noun(base, per_pack)}")
    course = commodity.get("course_definition") or {}
    per_course = course.get("base_units_per_course")
    if per_course in (None, "") and course.get("base_units_per_day") and course.get("days_per_course"):
        try:
            per_course = float(course["base_units_per_day"]) * float(course["days_per_course"])
        except (TypeError, ValueError):
            per_course = None
    if per_course not in (None, ""):
        parts.append(f"a course is {quantity_digits(per_course)} {unit_noun(base, per_course)}")
    return "; ".join(parts)


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

    def place_formset(self, data=None, initial=None):
        return TenderPlaceFormSet(data, initial=initial, prefix="places")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        posted = self.request.method == "POST"
        if context.get("has_program_context") and "lines" not in context:
            context["lines"] = (
                self.line_formset(self.request.POST) if posted else self.line_formset(initial=self.initial_lines())
            )
        if context.get("has_program_context") and "places" not in context:
            context["places"] = (
                self.place_formset(self.request.POST) if posted else self.place_formset(initial=self.initial_places())
            )
        return context

    def initial_lines(self):
        return []

    def initial_places(self):
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

        places = self.place_formset(self.request.POST)
        if not places.is_valid():
            return self.render_to_response(self.get_context_data(form=form, lines=lines, places=places))
        self._places = [
            {k: v for k, v in row.items() if k != "DELETE" and v}
            for row in places.cleaned_data
            if row and not row.get("DELETE") and (row.get("name") or row.get("city"))
        ]

        self._lines = kept
        return super().form_valid(form)

    def fixed(self, **kwargs):
        return {"data": {"lines": getattr(self, "_lines", []), "delivery_points": getattr(self, "_places", [])}}

    def breadcrumb(self, **kwargs):
        return [
            {"label": "Sourcing", "href": reverse("supply_chain:procurement_tender_board")},
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:procurement_tender_board")

    def redirect_to(self, result):
        return reverse("supply_chain:procurement_tender_detail", args=[result["id"]])


class _TenderInviteView(OperationActionView):
    """Put an organisation on, or take it off, a tender's invited list -- a button on the tender."""

    def fixed(self, **kwargs):
        org = self.request.POST.get("org", "")
        return {"tender_id": int(kwargs["tender_id"]), "org_id": int(org) if org.isdigit() else 0}

    def redirect_to(self, **kwargs):
        return reverse("supply_chain:procurement_tender_detail", args=[kwargs["tender_id"]])


class TenderInviteOrgView(_TenderInviteView):
    operation = "tender_invite_org"
    success_message = "Invited. They can now see and bid on this tender while it is restricted."


class TenderUninviteOrgView(_TenderInviteView):
    operation = "tender_uninvite_org"
    success_message = "Taken off the invited list."


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

    def initial_places(self):
        return [
            {k: point.get(k, "") for k in ("key", "name", "city", "country_name")}
            for point in (self._tender_instance().delivery_points or [])
        ]

    def fixed(self, **kwargs):
        return {
            "tender_id": int(kwargs["tender_id"]),
            "data": {"lines": getattr(self, "_lines", []), "delivery_points": getattr(self, "_places", [])},
        }


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
        # Back to the row just saved, picked out, rather than the top of the
        # tender: the edit is otherwise invisible on arrival.
        return _changed(
            reverse("supply_chain:procurement_tender_detail", args=[result["tender_id"]]),
            f"outreach-{result['id']}",
            "outreach",
        )


class OutreachChaseView(OutreachReplyView):
    """The day a reminder went, from the reminder draft itself.

    The reply form would do, but it posts every field on the row, and a form
    that carries only the chase date would clear the reply the row holds. This
    sends `last_reminder_on` alone through the same operation.
    """

    form_class = OutreachChaseForm
    title = "Record a chase"
    intro = "The day the reminder went, so the next one is counted from it."
    submit_label = "Record chase"

    def get_form_kwargs(self):
        kwargs = super(OutreachReplyView, self).get_form_kwargs()
        self._outreach()  # 404 outside this program
        return kwargs


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

    def get_initial(self):
        # The document the request already rests on, pre-selected: the answer
        # form said "None -- it rests on nothing on file" beside an approval
        # the award page showed resting on a registration.
        initial = super().get_initial()
        rests_on = self.approval().rests_on_document_id
        if rests_on:
            initial["rests_on_document"] = rests_on
        return initial

    def fixed(self, **kwargs):
        return {"approval_id": int(kwargs["approval_id"])}


# ---- what we owe them --------------------------------------------------------


class CommitmentRecordView(OperationFormView):
    """A question a supplier asked us, or something we promised, on a tender or an order."""

    operation = "commitment_record"
    form_class = CommitmentForm
    title = "Record something we owe"
    intro = (
        "A question they asked us, or something we promised them. Until it is resolved, the overview "
        "reads this as waiting on us, and a supplier's questions are drafted as a reply."
    )
    submit_label = "Record it"

    def _where(self):
        if "tender_id" in self.kwargs:
            return "tender_id", int(self.kwargs["tender_id"])
        return "contract_id", int(self.kwargs["contract_id"])

    def fixed(self, **kwargs):
        key, pk = self._where()
        return {"data": {key: pk}}

    def _back(self):
        key, pk = self._where()
        if key == "tender_id":
            return reverse("supply_chain:procurement_tender_detail", args=[pk])
        return reverse("supply_chain:order_detail", args=[pk])

    def breadcrumb(self, **kwargs):
        return [{"label": "Back", "href": self._back()}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return self._back()

    def redirect_to(self, result):
        return self._back()


class CommitmentResolveView(OperationFormView):
    """Answered, or done -- with what was said and when."""

    operation = "commitment_resolve"
    form_class = CommitmentResolveForm
    title = "Mark it answered"
    submit_label = "Save"

    def commitment(self):
        from connect_labs.supply_chain.models import Commitment

        found = (
            Commitment.objects.filter(pk=self.kwargs["commitment_id"], program_id=_access(self.request).program_id)
            .select_related("owed_to_org")
            .first()
        )
        if found is None:
            raise Http404(f"nothing owed with id {self.kwargs['commitment_id']} in this program")
        return found

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        commitment = self.commitment()
        context["intro"] = f"{commitment.owed_to_org.name}: {commitment.text}"
        return context

    def fixed(self, **kwargs):
        return {"commitment_id": int(kwargs["commitment_id"])}

    def _back(self):
        commitment = self.commitment()
        if commitment.tender_id:
            return reverse("supply_chain:procurement_tender_detail", args=[commitment.tender_id])
        return reverse("supply_chain:order_detail", args=[commitment.contract_id])

    def cancel_href(self, **kwargs):
        return self._back()

    def redirect_to(self, result):
        # To what we owe, with the row just resolved picked out.
        return _changed(self._back(), f"commitment-{self.commitment().pk}", "owed")

"""Where every tender and order in a program stands: one line each, for /supply/.

Each line says what stage the record has reached, what it is waiting on, the
last change anywhere under it (when, and who told us), and any stale flags.
Design doc docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md
§4.1, §4.2.

Two things this deliberately does not do:

  - Rank. Rows are ordered by last change, newest first. A stale flag is a
    stated fact -- "No reply in 20 days from 2 suppliers" -- and whether that
    matters more than the row above it is the reader's judgement, which the
    database does not contain (DomainHomeView's docstring; design doc
    sections 22 and 24).
  - Claim one buyer for the program. A local partner may place an order, so
    an order row names its buyer whenever that is not the program's own
    organisation.

`today` and `until` come from the caller. Under as-of the view already runs
inside a rewound transaction, so the rows read here are the past; `today` is
the as-of date, so "20 days" is counted from it, and `until` cuts the
revision log at the end of that day.

Cost: the rows and their children are read with one query per kind for the
whole program, but each row's last change goes through the Task 7 scope
collectors (`tender_scope_revisions` / `contract_scope_revisions`), about ten
queries a row -- fine for a program's handful of tenders and orders. If a
program ever holds hundreds, batch it: collect every row's scope ids first,
then read the newest revision per scope in one query over the union.
"""

from dataclasses import dataclass, field
from datetime import date, datetime

from django.urls import reverse

from connect_labs.supply_chain import records
from connect_labs.supply_chain.history.labels import actor_label, is_ai
from connect_labs.supply_chain.history.timeline import contract_scope_revisions, tender_scope_revisions

# An invitation unanswered this many days after it was sent is stale (§4.2).
NO_REPLY_DAYS = 14
# How many silent suppliers the flag names before it counts the rest ("+2").
# Three, because half of a usual six-supplier round goes silent: "+1" for the
# third hid exactly the name the reader needed to chase.
NAMED_SILENT = 3
PROVISIONAL_STAGE = "awarded, provisional"

# The rule behind each flag, for its title: what raised it, said once.
NO_REPLY_RULE = (
    f"An invited supplier who has neither replied nor quoted {NO_REPLY_DAYS} or more days after "
    "we last asked, while the tender is open."
)
BLOCKED_RULE = (
    "A live quote the comparison cannot rank yet, named with what it is missing -- the same gaps "
    "that keep it out of the comparison ranking (freight, duties, the pack, the quantity...)."
)
AWARDED_GAP_RULE = (
    "The awarded quote against the product's specification: a requirement it states no figure for, "
    "or one its figure does not meet."
)
ETA_RULE = "A shipment whose expected arrival day has passed and that has not been received."
INVOICE_ABOVE_RULE = (
    "An invoice on the order that bills more than the contract agreed -- its unit price, its freight, "
    "or the total against goods plus freight (the invoice_above_contract check)."
)
INVOICE_ABOVE_FLAG = "Invoice above agreed price"
# What that flag asks of us, on the row's Waiting on.
INVOICE_DISPUTE = "dispute the invoice above the agreed price"

# How "waiting on" opens when the next move is ours: a question we have not
# answered, a promise we have not kept, a document only we can supply.
WAITING_ON_US = "us"

# The order's chain, in order; the stage is the furthest one reached.
ORDER_STAGES = ("placed", "dispatched", "received", "invoiced", "paid")
# How a stage reads on the overview where the chain's own word is not what a
# program manager would say: goods dispatched and not yet arrived are in
# transit, and an order both received and paid is done on both counts.
IN_TRANSIT = "in transit"
DELIVERED_AND_PAID = "delivered and paid"


class Flag(str):
    """A flag's words, carrying the rule that raised it -- shown as its title.

    `heading` and `lines`, when a flag names several things: "Can't compare
    yet" over one line per supplier ("Northwind Foods: freight"). The string
    itself still says all of it on one line, for anything reading it as text.
    """

    rule: str = ""
    heading: str = ""
    lines: tuple = ()
    # What opens behind the chevron, when it is not `lines`: a flag whose
    # lines the row's Waiting on cell already says opens on its definition.
    folded: tuple = ()

    def __new__(cls, text, rule="", *, heading="", lines=(), folded=()):
        flag = super().__new__(cls, text)
        flag.rule = rule
        flag.heading = heading
        flag.lines = tuple(lines)
        flag.folded = tuple(folded)
        return flag


@dataclass
class Row:
    kind: str  # "tender" | "order"
    title: str
    url: str
    stage: str
    waiting_on: str
    last_change_at: datetime | None
    last_change_by: str
    last_change_is_ai: bool
    stale: list[str] = field(default_factory=list)
    # The AI pill's words and its title: "ACE (agent)" (beside an AI glyph),
    # "ACE recorded Quote · Northwind Foods from a forwarded email". Blank
    # when a person made the change.
    last_change_badge: str = ""
    last_change_title: str = ""
    # A second line under "waiting on": "3 of 4 replied".
    waiting_detail: str = ""
    # "Waiting on" as separate lines when it names two kinds of answer owed:
    # ("No reply: Plateau Mills", "Missing facts: Sahel Nutrition"). Empty
    # when `waiting_on` is one thing; `waiting_on` joins these with "; ".
    waiting_lines: tuple = ()
    # Who placed an order, when it is not the program's own organisation.
    buyer: str = ""
    # The order this row is; None on a tender's row.
    contract_id: int | None = None
    # The tender this row is, or the order was placed from; None when an
    # order came from no tender.
    tender_id: int | None = None
    # A tender whose award could still be beaten (see `_tender_rows`), and
    # the reason the buyer gave for it, so "provisional" reads with its why.
    provisional: bool = False
    award_why: str = ""
    # Why the award is provisional, from the comparison it froze:
    # "provisional — 2 of 3 quotes not yet comparable".
    provisional_caveat: str = ""
    # The awarded quote's headline price, beside "awarded to <supplier>",
    # and what it commits: "USD 41.00 per carton · USD 82,000.00 for 2,000 cartons".
    award_price: str = ""

    @property
    def award_price_lines(self) -> tuple:
        """The award's price a clause a line, under "awarded to <supplier>":
        ("USD 41.00 per carton", "USD 82,000.00 for 2,000 cartons")."""
        return tuple(part for part in self.award_price.split(" · ") if part)


def standing_rows(program_id: int, today: date, *, until: date | None = None, own_org_id=None) -> list[Row]:
    """Every tender and order in the program, newest change first.

    `own_org_id` is the organisation the program acts as (identity.resolve_org);
    an order bought by anyone else says so on its row.
    """
    rows = _tender_rows(program_id, today, until) + _order_rows(program_id, today, until, own_org_id)
    # Newest change first; a record with no revision at all (never captured) last.
    rows.sort(key=lambda r: (r.last_change_at is not None, r.last_change_at or datetime.min), reverse=True)
    return rows


# ---- words ---------------------------------------------------------------


def _day(d: date) -> str:
    return f"{d.day} {d.strftime('%b')}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _words(value: str) -> str:
    return (value or "").replace("_", " ")


def _one_line(text) -> str:
    """The award's reason as typed, on one line; never cut -- the cell wraps it."""
    return " ".join((text or "").split())


def _provisional_caveat(award, silent=0) -> str:
    """ "provisional — 2 of 3 quotes not yet comparable".

    The quotes are counted off the comparison the award froze. Who has not
    replied is "waiting on"'s to say ("No reply: ..."), and the flags', so it
    is not said a third time here; `silent` is kept for callers that pass it.
    """
    snapshot = getattr(award, "comparison_snapshot", None) or {}
    total, comparable = snapshot.get("total_count"), snapshot.get("comparable_count")
    if isinstance(total, int) and isinstance(comparable, int) and total > comparable:
        return f"provisional — {total - comparable} of {_plural(total, 'quote')} not yet comparable"
    return "provisional"


def _award_price(award) -> str:
    """ "USD 41.00 per carton": the awarded quote's per-pack figure from the comparison the award
    froze, or failing that its price as quoted. "" when neither is known."""
    from connect_labs.supply_chain.values import money_digits

    snapshot = getattr(award, "comparison_snapshot", None) or {}
    key = "usd_per_pack_normalized"
    for row in snapshot.get("comparable") or []:
        if row.get("quote_id") != award.quote_id:
            continue
        cell = (row.get("figures") or {}).get(key) or {}
        if cell.get("amount") in (None, ""):
            break
        label = next((c.get("label") or "" for c in snapshot.get("columns") or [] if c.get("key") == key), "")
        per = label.split(" ", 1)[1] if label.startswith("USD per ") else ""
        price = f"{cell.get('currency') or 'USD'} {money_digits(cell['amount'])}" + (f" {per}" if per else "")
        return price + _committed_total(row)
    quote = award.quote
    if quote is None or quote.as_quoted_amount is None:
        return ""
    from connect_labs.supply_chain.procurement.services.comparison import as_quoted_words

    holder = quote.item or quote.commodity
    # Already currency first: "USD 42.50 per carton".
    return as_quoted_words(quote, getattr(holder, "base_unit", ""), getattr(holder, "pack_unit", ""))


def _committed_total(row) -> str:
    """ " · USD 106,800.00 landed (incl. freight) for 2,000 cartons": what the award commits.

    The figure is the comparison's landed total as quoted -- goods plus whatever
    freight and duties the quote's basis counted -- so it is labelled as landed,
    with the legs it includes, read off the same row's `landed_basis`. Unlabelled,
    it did not reconcile with the goods price above it. "" when the frozen row
    does not hold both the total and its quantity.
    """
    from connect_labs.supply_chain.values import money_digits

    total = (row.get("figures") or {}).get("landed_total_as_quoted") or {}
    quantity = row.get("quantity_quoted") or ""
    if not isinstance(total, dict) or total.get("amount") in (None, "") or not quantity:
        return ""
    basis = (row.get("landed_basis") or "").lower()
    legs = [leg for leg in ("freight", "duties") if leg in basis]
    included = f" (incl. {' and '.join(legs)})" if legs else ""
    return f" · {total.get('currency') or 'USD'} {money_digits(total['amount'])} landed{included} for {quantity}"


def _names(names, limit=NAMED_SILENT) -> str:
    """ "Northwind Foods, Sahel Nutrition +1": up to `limit` names, then how many more."""
    shown = ", ".join(names[:limit])
    return f"{shown} +{len(names) - limit}" if len(names) > limit else shown


def _ai_badge(label: str, call, revision=None) -> tuple[str, str]:
    """The AI pill's words and its title.

    The words are who told us, as the timeline says it -- "ACE (agent)" -- with
    the AI marker a glyph beside them, not the word "AI" said a second time.
    The title says what was recorded and from what: "ACE recorded Quote ·
    Northwind Foods from a forwarded email".
    """
    agent = label.endswith(" (agent)")
    if agent:
        who = label.removesuffix(" (agent)")
    elif label.startswith("via AI · "):
        who = f"{label.removeprefix('via AI · ')} via an AI assistant"
    else:
        who = "An AI assistant"
    verb = _ACTION_VERBS.get(getattr(revision, "action", ""), "recorded")
    title = f"{who} {verb} {_record_words(revision)}"
    ref = getattr(call, "source_ref", "") or ""
    if "@" in ref:
        title += " from a forwarded email" if agent else " from an email"
    elif ref:
        title += " from a document"
    return label, title


_ACTION_VERBS = {"create": "recorded", "update": "updated", "delete": "removed"}


def _record_words(revision) -> str:
    """Which record a revision is on, as the timeline names it: "Quote · Northwind Foods"."""
    from connect_labs.supply_chain.history.labels import Lookup, _values_of, subject

    model = revision.content_type.model_class() if revision is not None else None
    if model is None:
        return "a change"
    lookup = Lookup()
    if revision.action == "create":
        values = {k: v[1] for k, v in (revision.changes or {}).items()}
    elif revision.action == "delete":
        values = dict(revision.changes or {})
    else:
        row = lookup.row(model, revision.object_id)
        values = _values_of(row) if row is not None else None
    return subject(model, values, lookup)


def _last_change(revisions):
    latest = revisions.first()
    if latest is None:
        return {"last_change_at": None, "last_change_by": "", "last_change_is_ai": False}
    label = actor_label(latest.call)
    ai = is_ai(latest.call)
    badge, title = _ai_badge(label, latest.call, latest) if ai else ("", "")
    return {
        "last_change_at": latest.recorded_at,
        "last_change_by": label,
        "last_change_is_ai": ai,
        "last_change_badge": badge,
        "last_change_title": title,
    }


# ---- tenders -------------------------------------------------------------


def _tender_rows(program_id, today, until):
    from connect_labs.supply_chain.models import Award, Outreach, Quote, Tender

    tenders = list(Tender.objects.filter(program_id=program_id))
    tender_ids = [t.pk for t in tenders]
    outreach = {}
    for o in Outreach.objects.filter(tender__program_id=program_id, tender_id__in=tender_ids).select_related(
        "supplier__org"
    ):
        outreach.setdefault(o.tender_id, []).append(o)
    quotes = {}
    for q in Quote.objects.filter(tender__program_id=program_id, tender_id__in=tender_ids).select_related(
        "supplier__org", "commodity", "item"
    ):
        quotes.setdefault(q.tender_id, []).append(q)
    contracted = set(
        Tender.objects.filter(program_id=program_id, contracts__isnull=False).values_list("pk", flat=True)
    )
    # The newest award per tender (Award is ordered newest decision first):
    # who it went to, and -- when provisional -- the reason it was made anyway.
    awards = {}
    for award in Award.objects.filter(tender__program_id=program_id, tender_id__in=tender_ids).select_related(
        "quote__supplier", "quote__commodity", "quote__item"
    ):
        awards.setdefault(award.tender_id, award)
    provisional = {tid: a for tid, a in awards.items() if a.provisional}
    owed = _owed_by_tender(program_id, tender_ids)

    rows = []
    for tender in tenders:
        award = awards.get(tender.pk) if tender.status == "awarded" else None
        waiting_on, waiting_detail, stale, waiting_lines, silent = _tender_state(
            tender,
            outreach.get(tender.pk, []),
            quotes.get(tender.pk, []),
            tender.pk in contracted,
            today,
            award,
            provisional=tender.pk in provisional,
        )
        ours_items = list(owed.get(tender.pk, []))
        deadline_passed = tender.status == "open" and tender.response_deadline and tender.response_deadline < today
        if deadline_passed:
            # A round still open past its deadline is a decision only we can make:
            # the stage says the deadline passed, so the owner list says whose it is.
            ours_items.append(("extend or close the round", f"deadline passed {_day(tender.response_deadline)}"))
        if ours_items:
            # What we owe comes first: it is the one thing on the row only we can move.
            # A labelled list like "No reply" and "Missing facts" beside it, so the
            # cell reads as one list of owners, each with its items.
            ours = Flag(
                f"{WAITING_ON_US}: " + "; ".join(f"{what} ({detail})" for what, detail in ours_items),
                heading=WAITING_ON_US.capitalize(),
                lines=ours_items,
            )
            others = list(waiting_lines) or ([waiting_on] if waiting_on not in ("", "—") else [])
            waiting_lines = (ours, *others) if others else ()
            waiting_on = "; ".join((ours, *others))
        stage = _words(tender.status)
        # An open round whose deadline is behind it says so: "open" alone read
        # as a round still inside its window.
        if deadline_passed:
            stage = f"open, deadline passed {_day(tender.response_deadline)}"
        awardee = award.quote.supplier.name if award is not None and award.quote_id else ""
        if awardee:
            stage = f"awarded to {awardee}"
        # An award made while suppliers were still blocked from the comparison
        # (the frozen snapshot's `provisional`) could still be beaten: say so.
        is_provisional = tender.status == "awarded" and tender.pk in provisional
        # "Provisional" is said once, by the caveat line under the stage
        # ("provisional — 2 of 3 suppliers not yet comparable"); the stage
        # itself names who it went to.
        if is_provisional and not awardee:
            stage = PROVISIONAL_STAGE
        rows.append(
            Row(
                kind="tender",
                title=tender.label,
                url=reverse("supply_chain:procurement_tender_detail", args=[tender.pk]),
                tender_id=tender.pk,
                stage=stage,
                waiting_on=waiting_on,
                waiting_detail=waiting_detail,
                waiting_lines=waiting_lines,
                stale=stale,
                provisional=is_provisional,
                award_why=_one_line(provisional[tender.pk].rationale) if is_provisional else "",
                provisional_caveat=_provisional_caveat(provisional[tender.pk], silent) if is_provisional else "",
                award_price=_award_price(award) if awardee else "",
                **_last_change(tender_scope_revisions(tender.pk, program_id=program_id, until=until)),
            )
        )
    return rows


def _owed_by_tender(program_id, tender_ids) -> dict:
    """[("answers to Northgate Commodities", "3 questions since 11 Jul")], per tender, from open commitments."""
    from connect_labs.supply_chain.models import Commitment

    grouped = {}
    for c in Commitment.objects.filter(
        program_id=program_id, tender_id__in=tender_ids, resolved_on__isnull=True
    ).select_related("owed_to_org"):
        grouped.setdefault(c.tender_id, {}).setdefault((c.owed_to_org.name, c.kind), []).append(c)
    out = {}
    for tender_id, by_party in grouped.items():
        parts = []
        for (name, kind), items in sorted(by_party.items()):
            since = _day(min(c.raised_on for c in items))
            if kind == "question":
                parts.append((f"answers to {name}", f"{_plural(len(items), 'question')} since {since}"))
            else:
                parts.append((f"{_plural(len(items), 'promise')} to {name}", f"since {since}"))
        out[tender_id] = parts
    return out


def _tender_state(tender, outreach, quotes, contracted, today, award=None, *, provisional=False):
    """(waiting on, a second line under it, flags, waiting-on lines, silent count) for one tender.

    While any live quote is kept out of the comparison -- before an award, or
    after a provisional one -- the tender is waiting on answers, and "waiting
    on" names who owes them, in two kinds: "No reply: <silent suppliers>" and
    "Missing facts: <blocked suppliers>". One list of both lumped a supplier
    who never answered with suppliers whose quotes lack a fact, beside "3 of 4
    replied".

    A silent supplier stays named -- under "No reply", and in its stale flag
    once it is old enough -- while the tender is still being chased: while it
    is open, and after a provisional award until the order is placed. The two
    used to part company after an award: the flag went, the name stayed.
    """
    live = [q for q in quotes if q.is_live]
    invited = {o.supplier_id for o in outreach}
    replied = {o.supplier_id for o in outreach if o.responded} | {q.supplier_id for q in live}
    replied &= invited
    suppliers = {o.supplier_id: o.supplier for o in outreach}
    still_open = provisional and not contracted and tender.status == "awarded"
    chasing = tender.status == "open" or still_open
    silent = sorted(invited - replied, key=lambda sid: suppliers[sid].name) if chasing else []

    stale = []
    # A supplier's clock runs from the latest time we asked it: a re-invite is a new request.
    latest_ask = {}
    for o in outreach:
        if o.sent_on is not None and o.supplier_id not in replied:
            latest_ask[o.supplier_id] = max(latest_ask.get(o.supplier_id, o.sent_on), o.sent_on)
    overdue = {sid: (today - sent).days for sid, sent in latest_ask.items() if (today - sent).days >= NO_REPLY_DAYS}
    # Only while replies are still being chased: once a tender is closed, or
    # awarded for good, a silent supplier is history, not something waiting.
    if overdue and chasing:
        # Counted, not named: "No reply in 17 days: 3 suppliers". Who they are
        # is the row's "Waiting on" ("No reply: ..."), beside it; naming them
        # twice made the row read as a list of names. The lines folded under
        # the flag still name each, with when it was asked. The age is the one
        # every one of them has passed, so the sentence is true of each.
        by_age = sorted(overdue, key=lambda sid: (-overdue[sid], suppliers[sid].name))
        text = f"No reply in {min(overdue.values())} days: {_plural(len(by_age), 'supplier')}"
        # It opens, like "Can't compare yet", on what it rests on: when each
        # request went out and to whom, then the rule that raised the flag.
        channels = {o.supplier_id: o.channel for o in outreach if o.sent_on == latest_ask.get(o.supplier_id)}
        lines = [
            f"{suppliers[sid].name} — asked {_day(latest_ask[sid])}"
            + (" by email" if channels.get(sid) == "email" else "")
            + f", {_plural(overdue[sid], 'day')} ago"
            for sid in by_age
        ]
        lines.append(f"Flagged after {NO_REPLY_DAYS} days without a reply")
        stale.append(Flag(text, NO_REPLY_RULE, heading=text, lines=lines))
    # Every live quote the comparison cannot rank, named with what it is
    # missing -- read off the comparison itself, so "0 of 3 comparable" there
    # and this flag here count the same offers for the same reasons. Not once
    # the tender is awarded: the decision has been made, and what the chosen
    # quote still lacks is flagged on its own below.
    # A provisional award was made while some of these were still blocked:
    # the flag stays up until the order is placed, so stage, waiting-on and
    # flags say one thing. The awarded quote itself is no longer "missing
    # facts" -- what it lacks is the awarded-quote flag's to say.
    blocked_names = []
    if tender.status not in ("awarded", "cancelled") or (provisional and not contracted):
        awarded_quote = award.quote_id if award is not None else None
        blocked, blocked_names = _blocked(tender, live, skip_quote=awarded_quote)
        if blocked:
            # A one-line marker, the per-supplier lines folded under it: the
            # filled block with a line per supplier was the heaviest thing on
            # the page.
            stale.append(
                Flag(
                    "Can't compare yet: " + "; ".join(blocked),
                    BLOCKED_RULE,
                    # The count, not a verdict, and not the names again: who is
                    # missing what is the Waiting on cell's, beside it. The
                    # definition opens behind the chevron.
                    heading=f"{_plural(len(blocked), 'quote')} missing facts",
                    lines=blocked,
                    folded=(BLOCKED_RULE,),
                )
            )
    if award is not None:
        gaps = _award_gaps(award)
        if gaps:
            stale.append(Flag(f"Awarded quote: {gaps}", AWARDED_GAP_RULE))
    # By importance, not by the order they were worked out in: what the chosen
    # offer still leaves open first, then the quotes that cannot be compared,
    # then the suppliers to chase for a reply.
    stale.sort(key=_flag_rank)

    detail = ""
    lines = []
    # Each silent supplier on a line of its own under "No reply", with the day
    # we asked it and the day we last chased it: who to chase today is read off
    # the overview, not a click into the round. The line's text still names
    # them on one line, for anything reading it as text.
    last_chased = {}
    for o in outreach:
        if o.last_reminder_on is not None:
            last_chased[o.supplier_id] = max(last_chased.get(o.supplier_id, o.last_reminder_on), o.last_reminder_on)

    def silent_line(text):
        per_supplier = []
        for sid in silent:
            days = []
            if sid in latest_ask:
                days.append(f"asked {_day(latest_ask[sid])}")
            if sid in last_chased:
                days.append(f"chased {_day(last_chased[sid])}")
            per_supplier.append((suppliers[sid].name, " · ".join(days)))
        return Flag(text, heading="No reply", lines=per_supplier)

    if blocked_names or (still_open and silent):
        if silent:
            lines.append(silent_line(f"No reply: {_names([suppliers[sid].name for sid in silent])}"))
        if blocked_names:
            lines.append(
                Flag(
                    "Missing facts: "
                    + ", ".join(f"{name} ({gaps})" if gaps else name for name, gaps in blocked_names),
                    heading="Missing facts",
                    lines=blocked_names,
                )
            )
    if lines:
        waiting_on = lines[0] if len(lines) == 1 else "; ".join(lines)
        if invited and tender.status == "open":
            detail = f"{len(replied)} of {len(invited)} replied"
    elif tender.status == "awarded":
        awardee = award.quote.supplier.name if award is not None and award.quote_id else ""
        if contracted:
            waiting_on = "—"
        else:
            waiting_on = f"a contract with {awardee}" if awardee else "a contract"
    elif tender.status == "draft":
        waiting_on = "opening"
    elif invited and replied == invited:
        waiting_on = "award decision"
    elif tender.status == "closed":
        waiting_on = "award decision"
    elif invited:
        # Who, not how many: "Northwind Foods — no reply since 9 Sep" is who
        # to chase. The day is the latest we asked any of them, so it is true
        # of each; the count moves to the line under it.
        asked = [latest_ask[sid] for sid in silent if sid in latest_ask]
        since = f"no reply since {_day(max(asked))}" if asked else "no reply yet"
        waiting_on = silent_line(f"{_names([suppliers[sid].name for sid in silent])} — {since}")
        detail = f"{len(replied)} of {len(invited)} replied"
    else:
        waiting_on = "invitations"
    return waiting_on, detail, stale, tuple(lines) if len(lines) > 1 else (), len(silent)


def _flag_rank(flag) -> int:
    """0 for the awarded-quote flag, 1 for "can't compare yet", 2 for a no-reply reminder."""
    return {AWARDED_GAP_RULE: 0, BLOCKED_RULE: 1, NO_REPLY_RULE: 2}.get(getattr(flag, "rule", None), 3)


def _blocked(tender, live, skip_quote=None) -> tuple[list[str], list[str]]:
    """ "Northwind Foods — missing: freight" for every live quote the comparison blocks, and
    each blocked supplier with what it lacks, in the same order: "Northwind Foods (freight,
    lot size)". `skip_quote`: the awarded quote, which is judged on its own flag rather
    than counted among the blocked."""
    from connect_labs.supply_chain.procurement.services.comparison import compare_tender

    by_commodity = {}
    for quote in live:
        by_commodity.setdefault(quote.commodity_id, []).append(quote)
    named, names, gaps = [], {}, {}
    for quotes in by_commodity.values():
        comparison = compare_tender(
            tender,
            quotes[0].commodity,
            quotes,
            {q.supplier_id: q.supplier for q in quotes},
            items_by_id={q.item_id: q.item for q in quotes if q.item_id},
        )
        for row in comparison.blocked:
            if skip_quote is not None and row.quote_id == skip_quote:
                continue
            text = f"{row.supplier_name} — missing: {', '.join(row.gaps)}" if row.gaps else row.supplier_name
            if text not in named:
                named.append(text)
                names[text] = row.supplier_name
            held = gaps.setdefault(row.supplier_name, [])
            held.extend(g for g in row.gaps if g not in held)
    named.sort()
    ordered = []
    for text in named:
        if names[text] not in ordered:
            ordered.append(names[text])
    return named, [(name, ", ".join(gaps.get(name) or [])) for name in ordered]


def _award_gaps(award) -> str:
    """What the awarded quote leaves open against the specification: "shelf life not stated"."""
    from connect_labs.supply_chain.procurement.services.compliance import FAIL, check_compliance, requirement_label

    quote = award.quote
    if quote is None or quote.commodity_id is None:
        return ""
    results = check_compliance(quote, quote.commodity, item=quote.item)

    def names(outcome):
        return [
            requirement_label(r.field, r.requirement.get("unit", "")).lower() for r in results if r.outcome == outcome
        ]

    parts = []
    if names("not_stated"):
        parts.append(f"{', '.join(names('not_stated'))} not stated")
    if names(FAIL):
        parts.append(f"{', '.join(names(FAIL))} outside the specification")
    return "; ".join(parts)


# ---- orders --------------------------------------------------------------


def _order_rows(program_id, today, until, own_org_id):
    from connect_labs.supply_chain.models import Contract, Invoice, Payment, Receipt, Shipment

    contracts = list(Contract.objects.filter(program_id=program_id).select_related("supplier__org", "buyer_org"))
    ids = [c.pk for c in contracts]
    shipments = {}
    for s in Shipment.objects.filter(contract__program_id=program_id, contract_id__in=ids):
        shipments.setdefault(s.contract_id, []).append(s)
    receipts = list(
        Receipt.objects.filter(contract__program_id=program_id, contract_id__in=ids).values_list(
            "contract_id", "shipment_id"
        )
    ) + list(
        Receipt.objects.filter(
            shipment__contract__program_id=program_id, shipment__contract_id__in=ids, contract_id__isnull=True
        ).values_list("shipment__contract_id", "shipment_id")
    )
    received_contracts = {c for c, _ in receipts}
    received_shipments = {s for _, s in receipts if s is not None}
    # A receipt against the order but no shipment could be any of them, so
    # none of that order's shipments is called "not received".
    unlinked = {c for c, s in receipts if s is None}
    invoiced = set(
        Invoice.objects.filter(contract__program_id=program_id, contract_id__in=ids)
        .exclude(status="rejected")
        .values_list("contract_id", flat=True)
    )
    # Paid means every live invoice is settled -- fulfilment/repository.py
    # derives an invoice's "paid" from what has been paid against its amount,
    # so a half payment leaves it "part_paid" and the order still waiting on
    # payment. An invoice with no amount cannot be derived, so a payment
    # against it is taken as settling it.
    with_payment = set(
        Payment.objects.filter(
            contract__program_id=program_id, contract_id__in=ids, invoice__isnull=False
        ).values_list("invoice_id", flat=True)
    )
    settled, any_paid = {}, set()
    for invoice_id, contract_id, status, amount in (
        Invoice.objects.filter(contract__program_id=program_id, contract_id__in=ids)
        .exclude(status="rejected")
        .values_list("pk", "contract_id", "status", "amount")
    ):
        done = status == "paid" or (amount is None and invoice_id in with_payment)
        settled[contract_id] = settled.get(contract_id, True) and done
        if invoice_id in with_payment:
            any_paid.add(contract_id)
    paid = {c for c, done in settled.items() if done}
    part_paid = any_paid - paid

    from connect_labs.supply_chain.fulfilment.services.holds import holds_for

    holds = holds_for(contracts)
    rows = []
    for contract in contracts:
        stage, waiting_on, stale = _order_state(
            contract,
            shipments.get(contract.pk, []),
            received_shipments,
            contract.pk in received_contracts,
            contract.pk in invoiced,
            contract.pk in paid,
            contract.pk in part_paid,
            contract.pk in unlinked,
            today,
            holds=holds.get(contract.pk, []),
        )
        waiting_lines = ()
        if contract.pk in invoiced and _invoice_above(contract, today):
            stale = [*stale, Flag(INVOICE_ABOVE_FLAG, INVOICE_ABOVE_RULE)]
            # A flag the row raises has an owner on the row: disputing an
            # overbilled invoice is ours, beside whatever else we owe.
            waiting_on, waiting_lines = _with_ours(
                waiting_on, [h.words for h in holds.get(contract.pk, [])], INVOICE_DISPUTE
            )
        title = contract.reference or f"Order {contract.pk}"
        rows.append(
            Row(
                kind="order",
                title=f"{title} · {contract.supplier.name}",
                url=reverse("supply_chain:order_detail", args=[contract.pk]),
                stage=stage,
                waiting_on=waiting_on,
                waiting_lines=waiting_lines,
                stale=stale,
                tender_id=contract.tender_id,
                contract_id=contract.pk,
                buyer=(
                    contract.buyer_org.name
                    if contract.buyer_org_id is not None and contract.buyer_org_id != own_org_id
                    else ""
                ),
                **_last_change(contract_scope_revisions(contract.pk, program_id=program_id, until=until)),
            )
        )
    return rows


def _with_ours(waiting_on, held_words, item):
    """An order's waiting-on with `item` added to what we owe: (waiting_on, waiting_lines).

    Held documents and the new item read as one "Us" list; anything the order
    waits on from someone else (an arrival, a dispatch) stays its own line, so
    our list never seems to own it.
    """
    ours_items = [(w, "") for w in held_words] + [(item, "")]
    ours = Flag(
        f"{WAITING_ON_US}: " + "; ".join(w for w, _ in ours_items),
        heading=WAITING_ON_US.capitalize(),
        lines=ours_items,
    )
    if held_words or waiting_on in ("", "—"):
        return ours, ()
    return "; ".join((ours, waiting_on)), (ours, waiting_on)


def _invoice_above(contract, today) -> bool:
    """Whether the invoice_above_contract check fires on this order -- the check itself, not a copy.

    Only asked of an invoiced, priced order, so the landed cost it needs is
    worked out for those alone.
    """
    if contract.consideration != "priced":
        return False
    from connect_labs.supply_chain.checks import _invoice_above_contract
    from connect_labs.supply_chain.fulfilment.services.landed import landed_total

    return _invoice_above_contract(contract, landed_total(contract), today) is not None


def _dispatched(shipment) -> bool:
    return shipment.dispatched_on is not None or shipment.status in (*records.IN_TRANSIT_STATUSES, "delivered")


def _order_state(
    contract, shipments, received_shipments, received, invoiced, paid, part_paid, unlinked_receipt, today, holds=()
):
    if contract.status in ("cancelled", "draft"):
        return _words(contract.status), "—", []

    reached = {
        "placed": True,
        "dispatched": any(_dispatched(s) for s in shipments),
        "received": received,
        "invoiced": invoiced,
        "paid": paid,
    }
    stage = [s for s in ORDER_STAGES if reached[s]][-1]
    # The order's own status says when only some of the goods have arrived.
    if stage == "received" and contract.status == "part_received":
        stage = "part received"
    elif stage == "invoiced" and part_paid:
        stage = "part paid"

    outstanding = [
        s for s in shipments if s.pk not in received_shipments and s.status != "lost" and not unlinked_receipt
    ]
    # What the goods are held on that WE owe, per shipment: a late shipment
    # held on our own document is ours, and its flag says so.
    held = {}
    for hold in holds:
        if hold.shipment_id is not None:
            held.setdefault(hold.shipment_id, []).append(hold.what)
    stale = [
        Flag(
            f"ETA {_day(s.expected_on)} passed, not received"
            + (f" — held on us: {', '.join(held[s.pk])}" if s.pk in held else ""),
            ETA_RULE,
        )
        for s in sorted(outstanding, key=lambda s: (s.expected_on or date.max, s.pk))
        if s.expected_on is not None and s.expected_on < today
    ]

    in_transit = [s for s in outstanding if _dispatched(s)]
    if in_transit and not received:
        # Goods on the road are the order's news even when the money moved first
        # (paid in advance): "paid" alone would read as finished.
        stage = IN_TRANSIT if stage == "dispatched" else f"{stage}, {IN_TRANSIT}"
    elif stage == "paid" and received and contract.status != "part_received":
        stage = DELIVERED_AND_PAID
    if holds:
        # Our move, not the supplier's: name what we owe before anything else
        # (docs/superpowers/specs/2026-10-02-supply-tracking-reality.md, ruling 5).
        waiting_on = f"{WAITING_ON_US}: {'; '.join(h.words for h in holds)}"
    elif in_transit:
        etas = sorted(s.expected_on for s in in_transit if s.expected_on is not None)
        waiting_on = f"arrival (ETA {_day(etas[0])})" if etas else "arrival"
    elif not received:
        waiting_on = "dispatch"
    elif not invoiced:
        waiting_on = "invoice"
    elif not paid:
        waiting_on = "payment"
    else:
        waiting_on = "—"
    return stage, waiting_on, stale

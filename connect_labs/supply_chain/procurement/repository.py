"""Reads and writes for the sourcing tier: tenders, quotes, awards, approvals.

A mixin rather than a second access class, for the reason
`StockRepositoryMixin` gives: every client keeps one object with one scope,
and the operation handlers stay `access.<verb>`. Split out of data_access.py
because this tier owns a different concern from the rest -- what a programme
has ASKED for and DECIDED, as against what it has bought, received or holds.

Two rules live here rather than in the operations above them, and both are
refusals rather than warnings:

`_require_approved_award` will not let a contract rest on an award whose
approval is pending or was declined. That is a fact about the award, not a
judgement about the buyer, and the refusal names whose answer is outstanding.

`blocking_approvals` decides what counts as standing in the way, and the
award page and the order guard both read it so the two cannot disagree about
whether an order may be placed. The latest answer from each approver in each
role decides: a refusal followed by a fresh request from the same approver is
history, not a veto.
"""

from datetime import date

from django.db import transaction
from django.utils import timezone

from connect_labs.supply_chain.models import (
    Award,
    AwardApproval,
    Commitment,
    Outreach,
    Quote,
    SupplierProfile,
    Tender,
)
from connect_labs.supply_chain.values import possessive


def _same_offer(quote, data) -> bool:
    """Whether `data` states the offer `quote` already holds, by any one mark."""
    from decimal import Decimal, InvalidOperation

    reference = (data.get("supplier_reference") or "").strip()
    if reference and quote.supplier_reference and reference.lower() == quote.supplier_reference.strip().lower():
        return True
    received = data.get("received_on")
    if received and quote.received_on and str(received) == quote.received_on.isoformat():
        return True
    try:
        amount = Decimal(str(data.get("as_quoted_amount")))
    except (InvalidOperation, ValueError):
        return False
    return (
        quote.as_quoted_amount is not None
        and amount == quote.as_quoted_amount
        and (data.get("as_quoted_unit") or "") == quote.as_quoted_unit
        and (data.get("as_quoted_currency") or "USD").upper() == (quote.as_quoted_currency or "USD").upper()
    )


class SuspectedDuplicate(ValueError):
    """A quote_record that looks like an offer already on file."""


def _quote_evidence(quote) -> str:
    """ "quote 2 (USD 54.50 per pack, received 9 Jul 2026, ref KF/Q/2611, from <ref>: \"...\")" """
    from django.contrib.contenttypes.models import ContentType

    from connect_labs.supply_chain.history.models import Revision
    from connect_labs.supply_chain.values import day_text, money_digits

    parts = []
    if quote.as_quoted_amount is not None:
        per = quote.as_quoted_unit.replace("_", " ")
        parts.append(f"{quote.as_quoted_currency} {money_digits(quote.as_quoted_amount)} {per}")
    if quote.received_on:
        parts.append(f"received {day_text(quote.received_on)}")
    if quote.supplier_reference:
        parts.append(f"supplier's reference {quote.supplier_reference}")
    created = (
        Revision.objects.filter(
            content_type=ContentType.objects.get_for_model(Quote), object_id=str(quote.pk), action="create"
        )
        .select_related("call")
        .first()
    )
    call = created.call if created is not None else None
    if call is not None and call.source_ref:
        evidence = f"recorded from {call.source_ref}"
        if call.source_excerpt:
            excerpt = " ".join(call.source_excerpt.split())
            evidence += f': "{excerpt[:240]}{"…" if len(excerpt) > 240 else ""}"'
        parts.append(evidence)
    return f"quote {quote.pk} ({', '.join(parts)})" if parts else f"quote {quote.pk}"


class ProcurementRepositoryMixin:
    def _resolve_tender(self, tender_id):
        if tender_id is None:
            return None
        found = self.get_tender(tender_id)
        if found is None:
            raise ValueError(f"tender {tender_id} does not exist")
        return found

    def _require_tender(self, tender_id):
        return self._resolve_tender(tender_id)

    def _tenders(self):
        return Tender.objects.filter(program_id=self._require_program())

    def list_tenders(self):
        return list(self._tenders().all())

    def get_tender(self, tender_id):
        return self._tenders().filter(pk=tender_id).first()

    def create_tender(self, data):
        from connect_labs.supply_chain.data_access import _columns, _delivery, _fresh, _listing

        data = _listing(data)
        return _fresh(
            Tender.objects.create(
                program_id=self._require_program(),
                **{"status": "draft", **_columns(Tender, _delivery(data))},
            )
        )

    def update_tender(self, tender_id, data):
        from connect_labs.supply_chain.data_access import _columns, _delivery, _fresh, _listing

        found = self.get_tender(tender_id)
        if found is None:
            raise ValueError(f"tender {tender_id} not found")
        data = _listing(data, tender=found)
        # Duty terms go through their own setter, which stamps the day they
        # were settled -- the edit page and an answer set them the same way.
        data = dict(data)
        duty = {key: data.pop(key) for key in ("duty_terms", "duty_estimate_percent") if key in data}
        for key, value in _columns(Tender, _delivery(data)).items():
            setattr(found, key, value)
        found.save()
        if duty:
            return self.set_tender_duty_terms(
                tender_id, duty.get("duty_terms", found.duty_terms), duty.get("duty_estimate_percent")
            )
        return _fresh(found)

    def set_tender_duty_terms(self, tender_id, duty_terms, duty_estimate_percent=None, on=None):
        """How import duties are handled for the tender. Idempotent: the same terms again change nothing.

        The day they were set is kept, so the page can say "set 2 Oct by
        Sophie" (who, from the revision). Clearing them to "" un-settles the
        round. An estimate only means something when we pay the duty; it is
        kept as given otherwise, and ignored.
        """
        from decimal import Decimal, InvalidOperation

        from connect_labs.supply_chain import records
        from connect_labs.supply_chain.data_access import _fresh

        found = self.get_tender(tender_id)
        if found is None:
            raise ValueError(f"tender {tender_id} not found")
        duty_terms = duty_terms or ""
        if duty_terms not in records.DUTY_TERMS:
            raise ValueError(f"duty_terms must be one of {', '.join(repr(t) for t in records.DUTY_TERMS)}")
        estimate = found.duty_estimate_percent
        if duty_estimate_percent not in (None, ""):
            try:
                estimate = Decimal(str(duty_estimate_percent))
            except InvalidOperation as exc:
                raise ValueError(f"duty_estimate_percent {duty_estimate_percent!r} is not a number") from exc
            if estimate < 0:
                raise ValueError("duty_estimate_percent cannot be negative")
        if found.duty_terms == duty_terms and found.duty_estimate_percent == estimate:
            return _fresh(found)
        day = date.fromisoformat(on) if isinstance(on, str) else (on or timezone.localdate())
        found.duty_terms = duty_terms
        found.duty_estimate_percent = estimate
        found.duty_terms_set_on = day if duty_terms else None
        found.save(update_fields=["duty_terms", "duty_estimate_percent", "duty_terms_set_on", "updated_at"])
        return _fresh(found)

    def set_tender_import_estimates(
        self, tender_id, clearing_estimate_per_unit=None, freight_estimate_per_unit=None, on=None
    ):
        """Our clearing & forwarding and freight estimates, USD per unit of the tender's line. Idempotent.

        A value left out (None) keeps what is recorded; "" clears it. The day
        they were recorded is kept, who from the revision.
        """
        from decimal import Decimal, InvalidOperation

        from connect_labs.supply_chain.data_access import _fresh

        found = self.get_tender(tender_id)
        if found is None:
            raise ValueError(f"tender {tender_id} not found")

        def parsed(name, given, current):
            if given is None:
                return current
            if given == "":
                return None
            try:
                value = Decimal(str(given))
            except InvalidOperation as exc:
                raise ValueError(f"{name} {given!r} is not a number") from exc
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
            return value

        clearing = parsed("clearing_estimate_per_unit", clearing_estimate_per_unit, found.clearing_estimate_per_unit)
        freight = parsed("freight_estimate_per_unit", freight_estimate_per_unit, found.freight_estimate_per_unit)
        if found.clearing_estimate_per_unit == clearing and found.freight_estimate_per_unit == freight:
            return _fresh(found)
        day = date.fromisoformat(on) if isinstance(on, str) else (on or timezone.localdate())
        found.clearing_estimate_per_unit = clearing
        found.freight_estimate_per_unit = freight
        found.import_estimates_set_on = day if (clearing is not None or freight is not None) else None
        found.save(
            update_fields=[
                "clearing_estimate_per_unit",
                "freight_estimate_per_unit",
                "import_estimates_set_on",
                "updated_at",
            ]
        )
        return _fresh(found)

    def open_tender(self, tender_id):
        """A tender cannot open until suppliers know where the goods go.

        Freight dominates the price, so an open tender that cannot say is not
        one anyone can answer. It says so by naming at least one delivery
        place, or by accepting collection from the supplier.
        """
        found = self.get_tender(tender_id)
        if found is None:
            raise ValueError(f"tender {tender_id} not found")
        places = [p for p in found.delivery_points or [] if p.get("city") or p.get("name")]
        if not places and not found.pickup_accepted:
            raise ValueError(
                "a tender needs a delivery point, or to accept collection from the supplier, before it can open"
            )
        found.status = "open"
        found.save(update_fields=["status", "updated_at"])
        return found

    def invite_org_to_tender(self, tender_id, org_id):
        """Put an organisation on a tender's invited list.

        Only an organisation registered as a supplier: a restricted tender is
        for suppliers to bid on, and an invitation to one that cannot bid is
        a list entry that does nothing.
        """
        from connect_labs.supply_chain.data_access import _fresh

        found, org = self._tender_and_org(tender_id, org_id)
        if not SupplierProfile.objects.filter(org=org).exists():
            raise ValueError(f"{org.name} is not registered as a supplier, so cannot be invited to bid")
        found.invited_orgs.add(org)
        return _fresh(found)

    def uninvite_org_from_tender(self, tender_id, org_id):
        from connect_labs.supply_chain.data_access import _fresh

        found, org = self._tender_and_org(tender_id, org_id)
        found.invited_orgs.remove(org)
        return _fresh(found)

    def _tender_and_org(self, tender_id, org_id):
        found = self.get_tender(tender_id)
        if found is None:
            raise ValueError(f"tender {tender_id} not found")
        org = self.get_org(org_id)
        if org is None:
            raise ValueError(f"organisation {org_id} does not exist")
        return found, org

    def close_tender(self, tender_id):
        found = self.get_tender(tender_id)
        if found is None:
            raise ValueError(f"tender {tender_id} not found")
        found.status = "closed"
        found.save(update_fields=["status", "updated_at"])
        return found

    # ---- outreach -------------------------------------------------------

    def list_outreach(self, tender_id=None):
        qs = Outreach.objects.filter(tender__program_id=self._require_program())
        if tender_id is not None:
            qs = qs.filter(tender_id=tender_id)
        return list(qs.select_related("supplier__org__supplier_profile").all())

    def get_outreach(self, outreach_id):
        return (
            Outreach.objects.filter(tender__program_id=self._require_program(), pk=outreach_id)
            .select_related("tender", "supplier__org__supplier_profile")
            .first()
        )

    def create_outreach(self, data):
        from connect_labs.supply_chain.data_access import _columns, _fresh

        return _fresh(
            Outreach.objects.create(
                tender=self._require_tender(data["tender_id"]),
                supplier=self._resolve_supplier(data.get("supplier_id")),
                **_columns(Outreach, data),
            )
        )

    def delete_outreach(self, outreach_id):
        """Remove an invitation that was recorded in error.

        A hard delete, unlike `quote_void`'s soft one, and the difference is
        the kind of thing each row is. A voided quote stays readable because
        it is a supplier's stated fact and the record of having received it
        matters even once superseded. An outreach row saying we contacted
        somebody we never contacted is not history -- it is a mistake, and
        leaving it readable would keep asserting the contact.

        Nothing references Outreach, so there is no cascade, and it is counted
        in exactly one place (summary.py) -- so no soft-delete flag has to be
        threaded through a count that could then disagree with the rows.
        """
        found = Outreach.objects.filter(tender__program_id=self._require_program(), pk=outreach_id).first()
        if found is None:
            raise ValueError(f"outreach {outreach_id} not found")
        tender_id, supplier_id = found.tender_id, found.supplier_id
        found.delete()
        return {"deleted": True, "outreach_id": outreach_id, "tender_id": tender_id, "supplier_id": supplier_id}

    def update_outreach(self, outreach_id, data):
        from connect_labs.supply_chain.data_access import _columns, _fresh

        found = Outreach.objects.filter(tender__program_id=self._require_program(), pk=outreach_id).first()
        if found is None:
            raise ValueError(f"outreach {outreach_id} not found")
        for key, value in _columns(Outreach, data).items():
            setattr(found, key, value)
        found.save()
        return _fresh(found)

    # ---- quotes ---------------------------------------------------------

    def _quotes(self):
        return Quote.objects.filter(tender__program_id=self._require_program()).select_related(
            "commodity", "superseded_by", "supersedes"
        )

    def list_quotes(self, tender_id=None):
        qs = self._quotes()
        if tender_id is not None:
            qs = qs.filter(tender_id=tender_id)
        return list(qs.all())

    def get_quote(self, quote_id):
        return self._quotes().filter(pk=quote_id).first()

    def create_quote(self, data, distinct_from=()):
        from connect_labs.supply_chain.data_access import _check_delivery, _columns, _fresh

        tender = self._require_tender(data["tender_id"])
        _check_delivery(tender, data.get("delivery_mode") or "delivered", data.get("delivery_point_keys") or [])
        commodity = self._require_commodity(data["commodity_slug"])
        supplier = self._resolve_supplier(data.get("supplier_id"))
        item = self._resolve_item(data.get("item_id"))
        self._refuse_suspected_duplicate(tender, supplier, commodity, item, data, distinct_from)
        return _fresh(
            Quote.objects.create(
                tender=tender,
                commodity=commodity,
                supplier=supplier,
                item=item,
                **_columns(Quote, data),
            )
        )

    def _refuse_suspected_duplicate(self, tender, supplier, commodity, item, data, distinct_from):
        """Refuse a second live quote from one supplier on one line, unless the caller says it is a second offer.

        Forwarding the same email twice used to make two live quotes: the
        replay key is the email's Message-ID plus the exact payload, and an
        inline forward carries no original Message-ID, while a second reading
        of the same email rarely produces the same payload twice. So the test
        here depends on neither. A live quote from this supplier, for this
        product and trade item, by the same delivery option, on this tender, is
        a suspected copy when it states the same offer by any one of three
        marks: the supplier's own reference, the price as quoted (amount, unit
        and currency), or the day it was received. The refusal shows that
        offer and its evidence, so the caller can see whether the email in hand
        is the same one. A genuinely separate offer is recorded by naming the
        quote it stands beside (`distinct_from_quote_ids`); a revised one
        replaces the old with quote_correct. Two quotes that differ on every
        mark -- two products' prices from one distributor -- are not refused:
        that is an ordinary second offer, and a guard that refused it would be
        overridden by reflex. (docs/superpowers/specs/
        2026-10-02-supply-tracking-reality.md, ruling 2.)
        """
        mode = data.get("delivery_mode") or "delivered"
        item_id = item.pk if item is not None else None
        rivals = [
            q
            for q in Quote.objects.filter(
                tender=tender, supplier=supplier, commodity=commodity, voided=False, superseded_by__isnull=True
            ).order_by("pk")
            if q.delivery_mode == mode and q.item_id == item_id and _same_offer(q, data)
        ]
        unacknowledged = [q for q in rivals if q.pk not in set(distinct_from or ())]
        if not unacknowledged:
            return
        raise SuspectedDuplicate(
            f"{supplier.name} already has a live quote for {commodity.name} on this tender: "
            + "; ".join(_quote_evidence(q) for q in unacknowledged)
            + ". Nothing was recorded. If the email in hand is that offer again (a re-forward, or the same "
            "quote read a second time), there is nothing to record -- to add a fact it now states, use "
            "quote_correct on that quote. If it is a revised offer that replaces it, use quote_correct. If it "
            "is a second offer that should stand beside it, call quote_record again with "
            f"distinct_from_quote_ids={[q.pk for q in unacknowledged]}."
        )

    @transaction.atomic
    def supersede_quote(self, quote_id, data, reason):
        """Corrections create a new version; the original stays readable.

        Overwriting would make a past award's frozen comparison
        unreproducible, and a comparison shown to a funder has to stay
        reconstructible.

        The two writes are one transaction. They used to be two statements
        with a logged failure in between, which could leave both versions
        reading as live and the superseded quote back in comparisons; a
        rollback is strictly better than a loud log.
        """
        from connect_labs.supply_chain.data_access import _check_delivery, _columns, _copy_of, _fresh

        existing = self.get_quote(quote_id)
        if existing is None:
            raise ValueError(f"quote {quote_id} not found")
        if not reason:
            raise ValueError("a correction needs a reason")

        merged = _copy_of(
            existing,
            {
                **_columns(Quote, data),
                "version": (existing.version or 1) + 1,
                "correction_reason": reason,
            },
        )
        _check_delivery(
            existing.tender, merged.get("delivery_mode") or "delivered", merged.get("delivery_point_keys") or []
        )
        replacement = Quote.objects.create(
            tender=existing.tender,
            commodity=self._resolve_commodity(data.get("commodity_slug")) or existing.commodity,
            supplier=self._resolve_supplier(data.get("supplier_id")) or existing.supplier,
            item=self._resolve_item(data.get("item_id")) or existing.item,
            **merged,
        )
        replacement = _fresh(replacement)
        existing.superseded_by = replacement
        existing.save(update_fields=["superseded_by", "updated_at"])
        return replacement

    def void_quote(self, quote_id, reason):
        """Leave the record readable; drop it out of comparisons.

        This is how a client cleans up its own duplicates, which is why labs
        needs no duplicate prevention of its own.
        """
        existing = self.get_quote(quote_id)
        if existing is None:
            raise ValueError(f"quote {quote_id} not found")
        if not reason:
            raise ValueError("voiding a quote needs a reason")
        existing.voided = True
        existing.void_reason = reason
        existing.save(update_fields=["voided", "void_reason", "updated_at"])
        return existing

    # ---- commitments: what we owe them -------------------------------------

    def list_commitments(self, tender_id=None, contract_id=None, open_only=False):
        qs = Commitment.objects.filter(program_id=self._require_program()).select_related("owed_to_org")
        if tender_id is not None:
            qs = qs.filter(tender_id=tender_id)
        if contract_id is not None:
            qs = qs.filter(contract_id=contract_id)
        if open_only:
            qs = qs.filter(resolved_on__isnull=True)
        return list(qs)

    def get_commitment(self, commitment_id):
        return (
            Commitment.objects.filter(program_id=self._require_program(), pk=commitment_id)
            .select_related("owed_to_org")
            .first()
        )

    def record_commitment(self, data):
        """A question a counterparty asked us, or something we promised them.

        Owed to an organisation: named directly, or as one of this program's
        suppliers. Hung on the tender or the order it is about, when there is
        one, so the tender's or the order's "waiting on us" can say so.
        """
        from connect_labs.supply_chain.data_access import _columns, _fresh

        tender = self._require_tender(data.get("tender_id")) if data.get("tender_id") else None
        contract = self._require_contract(data["contract_id"]) if data.get("contract_id") else None
        owed_to = None
        if data.get("supplier_id") is not None:
            supplier = self._resolve_supplier(data["supplier_id"])
            owed_to = supplier.org
        if data.get("owed_to_org_id") is not None:
            org = self.get_org(data["owed_to_org_id"])
            if org is None:
                raise ValueError(
                    f"organisation {data['owed_to_org_id']} does not exist; record it with org_upsert first"
                )
            if owed_to is not None and owed_to.pk != org.pk:
                raise ValueError("supplier_id and owed_to_org_id name two different organisations")
            owed_to = org
        if owed_to is None:
            raise ValueError("say who is waiting: supplier_id, or owed_to_org_id for anyone else")
        columns = {
            k: v
            for k, v in _columns(Commitment, data).items()
            if k not in ("tender_id", "contract_id", "owed_to_org_id", "program_id")
        }
        return _fresh(
            Commitment.objects.create(
                program_id=self._require_program(),
                tender=tender,
                contract=contract,
                owed_to_org=owed_to,
                **columns,
            )
        )

    def resolve_commitment(self, commitment_id, resolution, resolved_on=None):
        """Answered, or done. The row stays, with what was said and when."""
        from connect_labs.supply_chain.data_access import _fresh

        found = self.get_commitment(commitment_id)
        if found is None:
            raise ValueError(f"commitment {commitment_id} not found")
        if not found.is_open:
            raise ValueError(f"commitment {commitment_id} was already resolved on {found.resolved_on}")
        on = date.fromisoformat(resolved_on) if isinstance(resolved_on, str) else (resolved_on or date.today())
        if on < found.raised_on:
            raise ValueError(f"it cannot be resolved on {on}, before it was raised on {found.raised_on}")
        found.resolved_on = on
        found.resolution = resolution
        found.save(update_fields=["resolved_on", "resolution", "updated_at"])
        return _fresh(found)

    def mark_reply_sent(self, commitment_ids, sent_on=None):
        """The reply carrying these answers went out: each answered question becomes Answered.

        Idempotent: one already marked keeps its first day. An open question
        is refused -- a reply cannot have carried an answer nobody wrote.
        """
        from connect_labs.supply_chain.data_access import _fresh

        day = date.fromisoformat(sent_on) if isinstance(sent_on, str) else (sent_on or timezone.localdate())
        out = []
        for commitment_id in commitment_ids:
            found = self.get_commitment(commitment_id)
            if found is None:
                raise ValueError(f"commitment {commitment_id} not found")
            if found.is_open:
                raise ValueError(f"commitment {commitment_id} has no answer yet; mark it answered first")
            if found.reply_sent_on is None:
                found.reply_sent_on = day
                found.save(update_fields=["reply_sent_on", "updated_at"])
            out.append(_fresh(found))
        return out

    # ---- awards ---------------------------------------------------------

    def list_awards(self, tender_id=None):
        qs = Award.objects.filter(tender__program_id=self._require_program()).select_related("commodity")
        if tender_id is not None:
            qs = qs.filter(tender_id=tender_id)
        return list(qs.all())

    def get_award(self, award_id):
        """Scoped through the tender's programme, so a document cannot be
        attached to another programme's award."""
        return Award.objects.filter(tender__program_id=self._require_program(), pk=award_id).first()

    def create_award(self, data):
        from connect_labs.supply_chain.data_access import _columns, _fresh

        if not data.get("rationale"):
            raise ValueError("an award needs a rationale")
        found = self._require_tender(data["tender_id"])
        quote = self.get_quote(data["quote_id"]) if data.get("quote_id") else None
        if data.get("quote_id") and quote is None:
            raise ValueError(f"quote {data['quote_id']} does not exist")
        award = Award.objects.create(
            tender=found,
            quote=quote,
            supplier=self._resolve_supplier(data.get("supplier_id")) or (quote.supplier if quote else None),
            commodity=self._resolve_commodity(data.get("commodity_slug")) or (quote.commodity if quote else None),
            # Provisional when the comparison it froze was: an award made while
            # some suppliers could not yet be compared could still be beaten.
            **{
                "decided_on": date.today(),
                "provisional": bool((data.get("comparison_snapshot") or {}).get("provisional")),
                **_columns(Award, data),
            },
        )
        self._mark_awarded_when_complete(found)
        return _fresh(award)

    @staticmethod
    def _mark_awarded_when_complete(tender):
        """A tender whose every line has an award is awarded, and says so.

        The status existed and nothing set it, so a tender decided line by line
        still read "open" long past its deadline. Derived from the awards, not
        set by hand; a tender still missing an award on any line stays as it
        is, and a closed tender is never moved -- closing was somebody's call.
        """
        if tender.status not in ("draft", "open"):
            return
        wanted = {line.get("commodity_slug") for line in tender.lines or [] if isinstance(line, dict)}
        wanted.discard(None)
        if not wanted:
            return
        awarded = set(Award.objects.filter(tender=tender).values_list("commodity__slug", flat=True))
        if wanted <= awarded:
            tender.status = "awarded"
            tender.save(update_fields=["status", "updated_at"])

    # ---- approvals ------------------------------------------------------

    def list_approvals(self, award_id=None, status=None):
        qs = AwardApproval.objects.filter(award__tender__program_id=self._require_program()).select_related(
            "approver_org"
        )
        if award_id is not None:
            qs = qs.filter(award_id=award_id)
        if status is not None:
            qs = qs.filter(status=status)
        return list(qs.prefetch_related("documents"))

    def get_approval(self, approval_id):
        """Scoped through the award's tender, so a document cannot be attached
        to another programme's approval."""
        return (
            AwardApproval.objects.filter(award__tender__program_id=self._require_program(), pk=approval_id)
            .select_related("approver_org")
            .first()
        )

    def request_approval(self, data):
        from connect_labs.supply_chain.data_access import _fresh

        award = self.get_award(data["award_id"])
        if award is None:
            raise ValueError(f"award {data['award_id']} does not exist in this programme")
        approver = self.get_org(data["approver_org_id"])
        if approver is None:
            raise ValueError(f"organisation {data['approver_org_id']} does not exist")
        return _fresh(
            AwardApproval.objects.create(
                award=award,
                approver_org=approver,
                role=data["role"],
                status="requested",
                requested_on=data.get("requested_on") or date.today(),
                note=data.get("note") or "",
                rests_on_document=self._approval_rests_on(data.get("rests_on_document_id")),
            )
        )

    def _approval_rests_on(self, document_id):
        """The document an approval rests on, from this programme, or a refusal."""
        if document_id is None:
            return None
        document = self.get_document(document_id)
        if document is None:
            raise ValueError(f"document {document_id} does not exist in this programme")
        return document

    def decide_approval(
        self,
        approval_id,
        status,
        decided_on=None,
        note=None,
        rests_on_document_id=None,
        source=None,
        recorded_by_org_id=None,
    ):
        """Approved or declined, once.

        A reversal is a new request, so the refusal stays on the record: an
        approval that was declined and then quietly flipped would erase the
        one fact a later reader most needs.
        """
        from connect_labs.supply_chain.data_access import _fresh

        approval = self.get_approval(approval_id)
        if approval is None:
            raise ValueError(f"approval {approval_id} does not exist in this programme")
        if not approval.is_pending:
            raise ValueError(
                f"approval {approval_id} was already {approval.status} on {approval.decided_on}; "
                "request a new approval rather than overwrite a decision"
            )
        approval.status = status
        approval.decided_on = decided_on or date.today()
        if note:
            approval.decision_note = note
        approval.decision_source = source or "we_recorded"
        approval.decision_recorded_by_org_id = recorded_by_org_id
        approval.save(
            update_fields=[
                "status",
                "decided_on",
                "decision_note",
                "decision_source",
                "decision_recorded_by_org",
                "updated_at",
            ]
        )
        if rests_on_document_id is not None:
            approval.rests_on_document = self._approval_rests_on(rests_on_document_id)
            approval.save(update_fields=["rests_on_document", "updated_at"])
        return _fresh(approval)

    def blocking_approvals(self, award):
        """The approvals standing against an award, oldest first.

        The latest answer from each approver in each role decides. A refusal
        followed by a fresh request from the same approver in the same role
        is history, not a veto: that is how a funder who relents is recorded
        (see `AwardApproval`). The award page and the order guard both read
        this, so they cannot disagree.
        """
        latest = {}
        for approval in AwardApproval.objects.filter(award=award).select_related("approver_org"):
            key = (approval.approver_org_id, approval.role)
            if key not in latest or (approval.requested_on, approval.pk) >= (
                latest[key].requested_on,
                latest[key].pk,
            ):
                latest[key] = approval
        return sorted(
            (a for a in latest.values() if a.status in ("requested", "declined")),
            key=lambda a: (a.requested_on, a.pk),
        )

    def _require_approved_award(self, award_id):
        """The award, scoped to this programme, with nothing standing against it.

        A contract may not rest on an award whose approval is pending or was
        declined. That is refused rather than warned about because it is a
        fact about the award, not a judgement: the funder has not agreed, or
        said no. The refusal names the approval, so the reader knows exactly
        whose answer is outstanding.
        """
        award = self.get_award(award_id)
        if award is None:
            raise ValueError(f"award {award_id} does not exist in this programme")
        for approval in self.blocking_approvals(award):
            if approval.status == "requested":
                raise ValueError(
                    f"the award to {award.supplier.name} is awaiting {possessive(approval.approver_org.name)} "
                    f"{approval.role} approval (asked {approval.requested_on}); an order cannot be placed "
                    "against it until they have answered"
                )
            if approval.status == "declined":
                raise ValueError(
                    f"the award to {award.supplier.name} was declined by {approval.approver_org.name} "
                    f"({approval.role} approval, on {approval.decided_on}); an order cannot rest on it"
                )
        return award

    # ---- contracts ------------------------------------------------------

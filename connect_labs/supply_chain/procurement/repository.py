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

from connect_labs.supply_chain.models import Award, AwardApproval, Outreach, Quote, SupplierProfile, Tender


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
        for key, value in _columns(Tender, _delivery(data)).items():
            setattr(found, key, value)
        found.save()
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
        return list(qs.all())

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

    def create_quote(self, data):
        from connect_labs.supply_chain.data_access import _check_delivery, _columns, _fresh

        tender = self._require_tender(data["tender_id"])
        _check_delivery(tender, data.get("delivery_mode") or "delivered", data.get("delivery_point_keys") or [])
        return _fresh(
            Quote.objects.create(
                tender=tender,
                commodity=self._require_commodity(data["commodity_slug"]),
                supplier=self._resolve_supplier(data.get("supplier_id")),
                item=self._resolve_item(data.get("item_id")),
                **_columns(Quote, data),
            )
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
            **{"decided_on": date.today(), **_columns(Award, data)},
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
                    f"the award to {award.supplier.name} is awaiting {approval.approver_org.name}'s "
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

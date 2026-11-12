"""Reads and writes for the network and stock tiers.

A mixin rather than a second access class, so every client keeps one object
with one scope and the operation handlers stay `access.<verb>`. Split out of
data_access.py because these tiers own a different concern -- posting events
to an append-only ledger -- and a 1,500-line access class is a file nobody
can hold in their head.

Everything that implies movements goes through `stock.services.posting`; this
module creates the parent event and lets that decide what the ledger records.
"""

from django.db import transaction

from connect_labs.supply_chain.access_enforce import restrict
from connect_labs.supply_chain.models import (
    Consignment,
    Custodian,
    DispensingRule,
    Distribution,
    DistributionLine,
    Movement,
    StockCount,
    StockIssue,
    SupplyPoint,
)
from connect_labs.supply_chain.stock.services import posting


def _as_day(value):
    from datetime import date

    return value if isinstance(value, date) else date.fromisoformat(str(value))


class StockRepositoryMixin:
    # ---- supply points ---------------------------------------------------

    def _supply_points(self):
        return restrict(self, SupplyPoint.objects.filter(program_id=self._require_program()), SupplyPoint)

    def list_supply_points(self, opportunity_id=None, kind=None, include_inactive=False):
        qs = self._supply_points()
        if opportunity_id is not None:
            qs = qs.filter(opportunity_id=opportunity_id)
        if kind is not None:
            qs = qs.filter(kind=kind)
        if not include_inactive:
            qs = qs.filter(status="active")
        return list(qs.order_by("kind", "name"))

    def get_supply_point(self, supply_point_id):
        return self._supply_points().filter(pk=supply_point_id).first()

    def get_supply_point_by_username(self, connect_username, opportunity_id=None):
        """The holding of one field worker.

        How a CommCare form submission finds the place its reported stock
        belongs: the form carries a username, not a supply point id.
        """
        qs = self._supply_points().filter(kind="user_held", connect_username=connect_username)
        if opportunity_id is not None:
            qs = qs.filter(opportunity_id=opportunity_id)
        return qs.first()

    def _require_supply_point(self, supply_point_id, label="supply point"):
        found = self.get_supply_point(supply_point_id)
        if found is None:
            raise ValueError(f"{label} {supply_point_id} does not exist in this programme")
        return found

    def upsert_supply_point(self, data):
        """Create or update by slug within the programme.

        `full_clean` runs because a `user_held` point that names no Connect
        user is unusable -- nothing could ever post stock to it -- and the
        model says so.
        """
        parent = None
        if data.get("parent_supply_point_id") is not None:
            parent = self._require_supply_point(data["parent_supply_point_id"], "parent supply point")
        manager = None
        if data.get("managed_by_org_id") is not None:
            manager = self.get_org(data["managed_by_org_id"])
            if manager is None:
                raise ValueError(f"organisation {data['managed_by_org_id']} does not exist")

        from connect_labs.supply_chain.data_access import _columns, _fresh
        from connect_labs.supply_chain.stock.services.placement import place

        # Coordinates are placed below rather than written here, so a stand-in
        # echoed back by an edit form is not promoted to a recorded location.
        located = ("latitude", "longitude", "location_source", "location_precision", "location_label")
        defaults = _columns(SupplyPoint, {k: v for k, v in data.items() if k != "slug" and k not in located})
        defaults["parent"] = parent
        defaults["managed_by_org"] = manager
        point, _ = SupplyPoint.objects.update_or_create(
            program_id=self._require_program(), slug=data["slug"], defaults=defaults
        )
        point.full_clean(exclude=["parent", "managed_by_org"])
        # Every point carries a latitude and longitude: the one it was given,
        # else a stand-in from its organisation's head office (placement.py).
        submitted = (data["latitude"], data["longitude"]) if "latitude" in data and "longitude" in data else None
        place(point, submitted=submitted)
        return _fresh(point)

    # ---- the ledger ------------------------------------------------------

    def list_movements(self, supply_point_id=None, item_id=None, kind=None, since=None, until=None, limit=500):
        # The ledger shows removed entries, marked removed, beside their removals.
        qs = restrict(
            self, Movement.objects.for_program(self._require_program(), include_removed=True), Movement
        ).select_related("commodity", "reversal")
        if supply_point_id is not None:
            qs = qs.touching(self._require_supply_point(supply_point_id))
        if item_id is not None:
            qs = qs.filter(item_id=item_id)
        if kind is not None:
            qs = qs.filter(kind=kind)
        if since is not None:
            qs = qs.filter(occurred_on__gte=since)
        if until is not None:
            qs = qs.filter(occurred_on__lte=until)
        return list(qs[:limit])

    @transaction.atomic
    def remove_movement(self, movement_id, reason):
        """Remove one ledger entry that should not have been posted (`posting.post_removal`).

        The entry stays on the ledger, marked removed, with who removed it, when and
        why; the removal cancels it in every figure.
        """
        from connect_labs.supply_chain.data_access import _fresh

        original = (
            Movement.objects.for_program(self._require_program(), include_removed=True)
            .select_for_update()
            .filter(pk=movement_id)
            .first()
        )
        if original is None:
            raise ValueError(f"entry {movement_id} is not on this programme's ledger")
        from connect_labs.supply_chain.identity import person_name

        user = self.user if getattr(self.user, "is_authenticated", False) else None
        # Not get_full_name(): labs users have no first/last name, so it read "None None".
        removed_by = person_name(user)[:80]
        return _fresh(posting.post_removal(original, reason=reason, removed_by=removed_by))

    def record_movement(self, data):
        """Post one movement directly.

        For the movements no parent event produces -- a transfer between
        stores, a loss, an expiry write-off. A receipt or a distribution is
        recorded as that event instead, so the ledger keeps its link back to
        the paperwork.
        """
        from connect_labs.supply_chain.data_access import _columns, _fresh
        from connect_labs.supply_chain.stock.services.posting import VISIT_ONLY_FIELDS

        data = {key: value for key, value in data.items() if key not in VISIT_ONLY_FIELDS}
        frm = (
            self._require_supply_point(data["from_supply_point_id"], "from supply point")
            if data.get("from_supply_point_id") is not None
            else None
        )
        to = (
            self._require_supply_point(data["to_supply_point_id"], "to supply point")
            if data.get("to_supply_point_id") is not None
            else None
        )
        if frm is None and to is None:
            raise ValueError(
                "a movement must name a from_supply_point_id, a to_supply_point_id, or both -- "
                "one that touches neither changes no balance"
            )
        if frm is not None and to is not None and frm.pk == to.pk:
            # Out of a place and back into it: no balance changes, but the
            # ledger would still carry a row saying something moved.
            raise ValueError("a movement's from and to cannot be the same supply point")
        return _fresh(
            Movement.objects.create(
                program_id=self._require_program(),
                commodity=self._require_commodity(data["commodity_slug"]),
                item=self._resolve_item(data.get("item_id")),
                from_supply_point=frm,
                to_supply_point=to,
                **_columns(Movement, data),
            )
        )

    # ---- consignments: our own stock on the road ---------------------------

    IN_TRANSIT_SLUG = "in-transit"

    def _in_transit_point(self):
        """This program's in-transit point, created the first time goods leave.

        One per program is enough: the consignment says where each lot is
        going, so the point only has to be somewhere that is not a store.
        """
        point, _ = SupplyPoint.objects.get_or_create(
            program_id=self._require_program(),
            slug=self.IN_TRANSIT_SLUG,
            defaults={"name": "In transit", "kind": "in_transit", "source": "we_recorded"},
        )
        return point

    @transaction.atomic
    def dispatch_consignment(self, data):
        """Send stock from one of our places to another: it leaves now, arrives later.

        Posts the transfer out of the sender into the in-transit point in the
        same transaction as the consignment, so there is never a consignment
        whose goods are still counted at the sender, nor goods on the road
        with no consignment saying where they are going.
        """
        from connect_labs.supply_chain.data_access import _columns, _fresh

        sender = self._require_supply_point(data["from_supply_point_id"], "sending supply point")
        destination = self._require_supply_point(data["to_supply_point_id"], "destination supply point")
        if sender.pk == destination.pk:
            raise ValueError("a consignment cannot be sent to the place it leaves from")
        if "in_transit" in (sender.kind, destination.kind):
            raise ValueError("a consignment runs between two places stock rests, not to or from the road itself")
        expected_on = data.get("expected_on")
        if expected_on and str(expected_on) < str(data["dispatched_on"]):
            raise ValueError("a consignment cannot be expected before it was dispatched")
        via = self._in_transit_point()
        commodity = self._require_commodity(data["commodity_slug"])
        item = self._resolve_item(data.get("item_id"))
        consignment = Consignment(
            program_id=self._require_program(),
            from_supply_point=sender,
            to_supply_point=destination,
            via_supply_point=via,
            commodity=commodity,
            item=item,
            **_columns(Consignment, data),
        )
        consignment.save()
        consignment.dispatch_movement = posting.post_consignment_leg(
            consignment, frm=sender, to=via, quantity=consignment.quantity, occurred_on=consignment.dispatched_on
        )
        consignment.save(update_fields=["dispatch_movement", "updated_at"])
        return _fresh(consignment)

    @transaction.atomic
    def receive_consignment(self, consignment_id, data):
        """The goods arrived: move them off the road into the destination.

        A short arrival is received short, and the difference is written off
        as a `loss` from the in-transit point naming the consignment -- stock
        lost on the road is a fact the ledger should carry, not a balance left
        sitting on a road forever.
        """
        from decimal import Decimal

        from connect_labs.supply_chain.data_access import _fresh

        consignment = (
            Consignment.objects.select_for_update()
            .filter(program_id=self._require_program(), pk=consignment_id)
            .first()
        )
        if consignment is None:
            raise ValueError(f"consignment {consignment_id} does not exist in this program")
        if not consignment.is_open:
            raise ValueError(f"consignment {consignment_id} was already received on {consignment.received_on}")
        received_on = data["received_on"]
        if str(received_on) < str(consignment.dispatched_on):
            raise ValueError("a consignment cannot arrive before it left")
        received = Decimal(str(data.get("quantity_received", consignment.quantity)))
        if received < 0 or received > consignment.quantity:
            raise ValueError(
                f"received {received} {consignment.quantity_unit}; the consignment carried {consignment.quantity}"
            )
        if received > 0:
            consignment.receipt_movement = posting.post_consignment_leg(
                consignment,
                frm=consignment.via_supply_point,
                to=consignment.to_supply_point,
                quantity=received,
                occurred_on=received_on,
            )
        if received < consignment.quantity:
            posting.post_consignment_leg(
                consignment,
                frm=consignment.via_supply_point,
                to=None,
                quantity=consignment.quantity - received,
                occurred_on=received_on,
                kind="loss",
            )
        consignment.received_on = received_on
        consignment.quantity_received = received
        consignment.status = "received"
        consignment.save()
        return _fresh(consignment)

    def list_consignments(self, status=None, supply_point_id=None):
        from django.db.models import Q

        qs = Consignment.objects.filter(program_id=self._require_program()).select_related(
            "commodity", "from_supply_point", "to_supply_point"
        )
        if status is not None:
            qs = qs.filter(status=status)
        if supply_point_id is not None:
            qs = qs.filter(Q(from_supply_point_id=supply_point_id) | Q(to_supply_point_id=supply_point_id))
        return list(qs)

    # ---- counts ----------------------------------------------------------

    def get_stock_count(self, stock_count_id):
        """Scoped by programme, so a document cannot be attached to another
        programme's count."""
        return StockCount.objects.filter(program_id=self._require_program(), pk=stock_count_id).first()

    def list_stock_counts(self, supply_point_id=None, item_id=None, kind=None, limit=500):
        qs = StockCount.objects.filter(program_id=self._require_program()).select_related("commodity")
        if supply_point_id is not None:
            qs = qs.filter(supply_point_id=supply_point_id)
        if item_id is not None:
            qs = qs.filter(item_id=item_id)
        if kind is not None:
            qs = qs.filter(kind=kind)
        return list(qs[:limit])

    @transaction.atomic
    def record_stock_count(self, data):
        """Record what somebody says is there; post an adjustment if it is an override.

        A `self_reported` or `physical_count` is an observation and is simply
        kept -- the variance against the ledger is then visible, which is the
        point. An `override` is an assertion that the ledger is wrong, so it
        additionally posts the difference (see posting.post_override), and is
        refused if that difference cannot be computed.
        """
        from connect_labs.supply_chain.data_access import _columns, _fresh

        point = self._require_supply_point(data["supply_point_id"])
        count = StockCount.objects.create(
            program_id=self._require_program(),
            supply_point=point,
            commodity=self._require_commodity(data["commodity_slug"]),
            item=self._resolve_item(data.get("item_id")),
            **_columns(StockCount, data),
        )
        count.full_clean(exclude=["supply_point", "commodity", "item", "adjustment_movement", "recorded_by_org"])
        if count.kind == "override":
            posting.post_override(count, self._require_program())
        return _fresh(count)

    # ---- custodians ------------------------------------------------------

    def list_custodians(self, supply_point_id=None, current_only=False):
        qs = Custodian.objects.filter(program_id=self._require_program())
        if supply_point_id is not None:
            qs = qs.filter(supply_point_id=supply_point_id)
        if current_only:
            qs = qs.filter(ended_on__isnull=True)
        return list(qs)

    def custodian_at(self, supply_point_id, on_date):
        """The custodian responsible at this point on this day, or None."""
        from django.db.models import Q

        return (
            Custodian.objects.filter(program_id=self._require_program(), supply_point_id=supply_point_id)
            .filter(starts_on__lte=on_date)
            .filter(Q(ended_on__isnull=True) | Q(ended_on__gt=on_date))
            .first()
        )

    @transaction.atomic
    def assign_custodian(self, data):
        """Name who is responsible for a storage point from a day on, closing the one before.

        The earlier row is ended, never edited away: who held the stock on any
        past day stays answerable, and so does what security was in place.
        """
        from connect_labs.supply_chain.data_access import _columns, _fresh

        point = self._require_supply_point(data["supply_point_id"])
        starts_on = _as_day(data["starts_on"])
        for current in Custodian.objects.select_for_update().filter(supply_point=point, ended_on__isnull=True):
            if current.starts_on > starts_on:
                raise ValueError(
                    f"{current.name} has been custodian since {current.starts_on}, after {starts_on}; "
                    "a new custodian cannot start before the one it replaces"
                )
            current.ended_on = starts_on
            current.save(update_fields=["ended_on", "updated_at"])
        custodian = Custodian.objects.create(
            program_id=self._require_program(), supply_point=point, **_columns(Custodian, data)
        )
        return _fresh(custodian)

    # ---- stock issues ----------------------------------------------------

    def get_stock_issue(self, issue_id):
        return (
            StockIssue.objects.filter(program_id=self._require_program(), pk=issue_id)
            .select_related("commodity", "supply_point", "item")
            .first()
        )

    def list_stock_issues(self, supply_point_id=None, status=None, open_only=False, kind=None, limit=500):
        qs = (
            StockIssue.objects.filter(program_id=self._require_program())
            .select_related("commodity")
            .prefetch_related("follow_ups")
        )
        if supply_point_id is not None:
            qs = qs.filter(supply_point_id=supply_point_id)
        if status is not None:
            qs = qs.filter(status=status)
        if open_only:
            qs = qs.exclude(status="resolved")
        if kind is not None:
            qs = qs.filter(kind=kind)
        return list(qs[:limit])

    @transaction.atomic
    def report_stock_issue(self, data):
        """Record a problem with stock and take the quantity off the shelf.

        The loss is posted at once, linked to the issue: stock that is missing
        is not on hand while it is being followed up, and a recovery puts it
        back (see resolve_stock_issue). The responsible custodian is the one in
        post at that point on that day, unless the caller names another.
        """
        from connect_labs.supply_chain.data_access import _columns, _fresh

        data = dict(data)
        point = self._require_supply_point(data["supply_point_id"])
        occurred_on = _as_day(data["occurred_on"])
        custodian_id = data.pop("custodian_id", None)
        if custodian_id is not None:
            custodian = Custodian.objects.filter(program_id=self._require_program(), pk=custodian_id).first()
            if custodian is None:
                raise ValueError(f"custodian {custodian_id} does not exist in this programme")
        else:
            custodian = self.custodian_at(point.pk, occurred_on)
        issue = StockIssue.objects.create(
            program_id=self._require_program(),
            supply_point=point,
            commodity=self._require_commodity(data["commodity_slug"]),
            item=self._resolve_item(data.get("item_id")),
            custodian=custodian,
            **_columns(StockIssue, data),
        )
        issue.full_clean(
            exclude=["supply_point", "commodity", "item", "custodian", "recorded_by_org", "outcome", "resolved_on"]
        )
        if issue.quantity <= 0:
            raise ValueError("an issue is about some quantity of stock; a quantity of nothing is not an issue")
        if issue.kind in ("expired", "expiring"):
            # Only stock past its date is expired. A batch still in date is a warning to use it first or
            # move it, however it was reported: nothing leaves the shelf for it.
            expiry = posting.batch_expiry(issue)
            if issue.kind == "expired" and expiry is not None and expiry > issue.occurred_on:
                issue.kind = "expiring"
                issue.save(update_fields=["kind", "updated_at"])
        if issue.kind != "expiring":
            posting.post_issue_loss(issue)
        return _fresh(issue)

    @transaction.atomic
    def update_stock_issue(self, issue_id, data):
        """Follow-up on an open issue: move it to being followed up, add what is known, name who took it."""
        from connect_labs.supply_chain.data_access import _fresh

        issue = self._open_stock_issue(issue_id)
        for field in ("status", "taken_by", "evidence", "reported_by", "note"):
            if data.get(field) is not None:
                setattr(issue, field, data[field])
        if "custodian_id" in data and data["custodian_id"] is not None:
            custodian = Custodian.objects.filter(program_id=self._require_program(), pk=data["custodian_id"]).first()
            if custodian is None:
                raise ValueError(f"custodian {data['custodian_id']} does not exist in this programme")
            issue.custodian = custodian
        issue.save()
        return _fresh(issue)

    @transaction.atomic
    def resolve_stock_issue(self, issue_id, data):
        """Close an issue as recovered, replaced or written off.

        Recovered or replaced stock goes back on the ledger as a movement linked
        to this issue (all of it unless `quantity_recovered` says less; the rest
        stands as the loss already posted). Written off posts nothing more: the
        loss recorded at report is the write-off.
        """
        from connect_labs.supply_chain.data_access import _fresh

        issue = self._open_stock_issue(issue_id)
        outcome = data["outcome"]
        resolved_on = _as_day(data["resolved_on"])
        if resolved_on < issue.occurred_on:
            raise ValueError("an issue cannot be resolved before it happened")
        issue.outcome = outcome
        issue.status = "resolved"
        issue.resolved_on = resolved_on
        if data.get("note"):
            issue.note = data["note"]
        if issue.kind == "expiring":
            # A warning took nothing off the shelf. Written off means it did expire: the loss is posted now.
            # Anything else (used first, moved) leaves the stock where it is, so there is nothing to put back.
            issue.save()
            if outcome == "written_off":
                posting.post_issue_loss(issue, occurred_on=resolved_on)
        elif outcome in ("recovered", "replaced"):
            from decimal import Decimal

            back = Decimal(str(data.get("quantity_recovered", issue.quantity)))
            if back <= 0 or back > issue.quantity:
                raise ValueError(f"{back} {issue.quantity_unit} cannot come back from an issue of {issue.quantity}")
            issue.quantity_recovered = back
            issue.save()
            posting.post_issue_recovery(issue, quantity=back, occurred_on=resolved_on)
        else:
            issue.save()
        return _fresh(issue)

    def _open_stock_issue(self, issue_id):
        issue = (
            StockIssue.objects.select_for_update(of=("self",))
            .filter(program_id=self._require_program(), pk=issue_id)
            .select_related("commodity", "supply_point", "item")
            .first()
        )
        if issue is None:
            raise ValueError(f"stock issue {issue_id} does not exist in this programme")
        if issue.status == "resolved":
            raise ValueError(f"stock issue {issue_id} was already resolved ({issue.outcome})")
        return issue

    # ---- distributions ---------------------------------------------------

    def list_distributions(self, opportunity_id=None, supply_point_id=None, limit=200):
        qs = Distribution.objects.filter(program_id=self._require_program()).prefetch_related(
            "lines", "documents", "movements"
        )
        if opportunity_id is not None:
            qs = qs.filter(opportunity_id=opportunity_id)
        if supply_point_id is not None:
            qs = qs.filter(supply_point_id=supply_point_id)
        return list(qs[:limit])

    def get_distribution(self, distribution_id):
        return (
            Distribution.objects.filter(program_id=self._require_program(), pk=distribution_id)
            .prefetch_related("lines", "documents", "movements")
            .first()
        )

    @transaction.atomic
    def record_distribution(self, data):
        """One resupply run out to field workers, with its lines, posted to the ledger.

        A line may name a worker either by supply point id or by Connect
        username, because the two callers differ: a screen knows the id, and
        a partner uploading a run from a spreadsheet knows the username.
        Resolving both here means neither has to look the other up first.
        """
        from connect_labs.supply_chain.data_access import _columns, _fresh

        commodity = self._require_commodity(data["commodity_slug"])
        source_point = self._require_supply_point(data["supply_point_id"], "issuing supply point")
        lines = data.get("lines") or []
        if not lines:
            raise ValueError("a distribution with no lines moves nothing; give it at least one line")

        distribution = Distribution.objects.create(
            program_id=self._require_program(),
            supply_point=source_point,
            commodity=commodity,
            **_columns(Distribution, data),
        )

        for line in lines:
            destination = None
            if line.get("to_supply_point_id") is not None:
                destination = self._require_supply_point(line["to_supply_point_id"], "destination supply point")
            elif line.get("connect_username"):
                destination = self.get_supply_point_by_username(
                    line["connect_username"], opportunity_id=distribution.opportunity_id
                )
                if destination is None:
                    raise ValueError(
                        f"no supply point holds stock for {line['connect_username']!r} on this "
                        "opportunity -- create one of kind user_held first, so the stock has "
                        "somewhere to be"
                    )
            else:
                raise ValueError("each distribution line needs a to_supply_point_id or a connect_username")

            DistributionLine.objects.create(
                distribution=distribution,
                to_supply_point=destination,
                connect_username=line.get("connect_username") or destination.connect_username,
                item=self._resolve_item(line.get("item_id")),
                batch=line.get("batch") or "",
                quantity=line["quantity"],
                quantity_given=line["quantity"],
                quantity_unit=line["quantity_unit"],
            )

        posting.post_distribution(distribution, commodity)
        return _fresh(distribution)

    def _destination(self, line, opportunity_id):
        """The place a distribution line goes: by supply point id, or by a worker's Connect username."""
        if line.get("to_supply_point_id") is not None:
            return self._require_supply_point(line["to_supply_point_id"], "destination supply point")
        if line.get("connect_username"):
            found = self.get_supply_point_by_username(line["connect_username"], opportunity_id=opportunity_id)
            if found is None:
                raise ValueError(
                    f"no supply point holds stock for {line['connect_username']!r} on this "
                    "opportunity -- create one of kind user_held first, so the stock has "
                    "somewhere to be"
                )
            return found
        raise ValueError("each distribution line needs a to_supply_point_id or a connect_username")

    @transaction.atomic
    def plan_distribution(self, data):
        """A handout run that has not happened yet: who is to get how much, from which store.

        Nothing is posted to the ledger. The planned quantities are "committed" (promised, not
        gone) until the run is confirmed with `confirm_distribution`. With `from_forecast`, the
        lines are the saved plan's what-to-issue for each field worker (`distribution.issue_plan`).
        """
        from connect_labs.supply_chain.data_access import _columns, _fresh
        from connect_labs.supply_chain.distribution import issue_plan

        commodity = self._require_commodity(data["commodity_slug"])
        source_point = self._require_supply_point(data["supply_point_id"], "issuing supply point")
        lines = list(data.get("lines") or [])
        if data.get("from_forecast"):
            lines = issue_plan.lines_for(self, commodity, data["opportunity_id"], data.get("item_id"))
            if not lines:
                raise ValueError(
                    "the plan has nothing to issue: no field worker needs stock this week, or the "
                    "opportunity has no cases set up to forecast from. Give the lines yourself."
                )
        if not lines:
            raise ValueError("a handout plan with no lines plans nothing; give it at least one line")
        columns = _columns(Distribution, data)
        columns.pop("message_text", None)
        columns.pop("confirmed_on", None)
        distribution = Distribution.objects.create(
            program_id=self._require_program(), supply_point=source_point, commodity=commodity, **columns
        )
        for line in lines:
            destination = self._destination(line, distribution.opportunity_id)
            if destination.pk == source_point.pk:
                raise ValueError("a handout has to go somewhere else than the store it leaves")
            DistributionLine.objects.create(
                distribution=distribution,
                to_supply_point=destination,
                connect_username=line.get("connect_username") or destination.connect_username,
                item=self._resolve_item(line.get("item_id")),
                batch=line.get("batch") or "",
                quantity=line["quantity"],
                quantity_unit=line["quantity_unit"],
            )
        return _fresh(distribution)

    @transaction.atomic
    def confirm_distribution(self, distribution_id, data):
        """Say what was actually handed out on a planned run, and post it to the ledger.

        Each confirmed line posts a movement for the quantity GIVEN, whatever was planned: the
        ledger records what happened. Lines the confirmation does not mention stay planned, so a
        partial message confirms part of the run. A line with a `line_id` is one of the plan; one
        with only a destination is a handout nobody planned and is added (planned quantity 0).
        The message the confirmation came from is kept on the run as its evidence.
        """
        from datetime import date

        from connect_labs.supply_chain.data_access import _fresh

        distribution = (
            Distribution.objects.select_for_update()
            .filter(program_id=self._require_program(), pk=distribution_id)
            .first()
        )
        if distribution is None:
            raise ValueError(f"distribution {distribution_id} does not exist in this programme")
        given_lines = data.get("lines") or []
        if not given_lines:
            raise ValueError("a confirmation has to say what was given on at least one line")
        confirmed_on = _as_day(data.get("confirmed_on") or date.today())
        source = data.get("source") or "we_recorded"
        org_id = data.get("recorded_by_org_id")
        commodity = self._commodity_of_distribution(distribution)
        planned = {line.pk: line for line in distribution.lines.select_for_update()}
        seen = set()
        for entry in given_lines:
            given = entry["quantity_given"]
            if entry.get("line_id") is not None:
                line = planned.get(entry["line_id"])
                if line is None:
                    raise ValueError(f"line {entry['line_id']} is not on distribution {distribution_id}")
                if line.pk in seen:
                    raise ValueError(f"line {line.pk} is confirmed twice in one message")
                if line.is_confirmed:
                    raise ValueError(
                        f"line {line.pk} ({line.to_supply_point.name}) was already confirmed as "
                        f"{line.given} {line.quantity_unit}"
                    )
            else:
                destination = self._destination(entry, distribution.opportunity_id)
                line = DistributionLine.objects.create(
                    distribution=distribution,
                    to_supply_point=destination,
                    connect_username=entry.get("connect_username") or destination.connect_username,
                    item=self._resolve_item(entry.get("item_id")),
                    batch=entry.get("batch") or "",
                    quantity=0,
                    quantity_unit=entry["quantity_unit"],
                )
            seen.add(line.pk)
            line.quantity_given = given
            if float(given) > 0:
                movement = Movement(
                    program_id=distribution.program_id,
                    opportunity_id=distribution.opportunity_id,
                    kind="distribution",
                    occurred_on=confirmed_on,
                    from_supply_point=distribution.supply_point,
                    to_supply_point=line.to_supply_point,
                    item=line.item,
                    commodity=commodity,
                    batch=line.batch,
                    quantity=given,
                    quantity_unit=line.quantity_unit,
                    source=source,
                    recorded_by_org_id=org_id,
                    distribution=distribution,
                )
                movement.save()
                line.movement = movement
            line.save()
        message = (data.get("message_text") or "").strip()
        if message:
            distribution.message_text = (
                distribution.message_text + "\n\n" if distribution.message_text else ""
            ) + message
        distribution.confirmed_on = confirmed_on
        distribution.save()
        return _fresh(distribution)

    def _commodity_of_distribution(self, distribution):
        """The product a run is for: that of the movements it posted, else of its first line's item."""
        if distribution.commodity_id:
            return distribution.commodity
        movement = distribution.movements.select_related("commodity").first()
        if movement is not None:
            return movement.commodity
        for line in distribution.lines.select_related("item__commodity"):
            if line.item_id:
                return line.item.commodity
        raise ValueError("this run does not say which product it is for")

    @transaction.atomic
    def confirm_distribution_receipt(self, line_id, data):
        """The person a handout went to says what reached them. The other side of the same handover."""
        from datetime import date

        from connect_labs.supply_chain.data_access import _fresh

        line = (
            DistributionLine.objects.select_for_update()
            .select_related("distribution", "to_supply_point")
            .filter(distribution__program_id=self._require_program(), pk=line_id)
            .first()
        )
        if line is None:
            raise ValueError(f"distribution line {line_id} does not exist in this programme")
        if line.quantity_received is not None:
            raise ValueError(
                f"{line.to_supply_point.name} already confirmed receiving {line.quantity_received} "
                f"{line.quantity_unit} on {line.received_on}"
            )
        line.quantity_received = data["quantity_received"]
        line.received_on = _as_day(data.get("received_on") or date.today())
        line.received_by = (data.get("received_by") or line.connect_username or "")[:150]
        line.save()
        return _fresh(line.distribution)

    # ---- dispensing rules ------------------------------------------------

    def _dispensing_rules(self):
        return DispensingRule.objects.filter(program_id=self._require_program()).select_related(
            "item__commodity", "resupply_point"
        )

    def list_dispensing_rules(self, opportunity_id=None, include_inactive=False):
        qs = self._dispensing_rules()
        if opportunity_id is not None:
            qs = qs.filter(opportunity_id=opportunity_id)
        if not include_inactive:
            qs = qs.filter(status="active")
        return list(qs)

    def get_dispensing_rule(self, rule_id):
        return self._dispensing_rules().filter(pk=rule_id).first()

    @transaction.atomic
    def upsert_dispensing_rule(self, data):
        """Create or edit the rule for one item on one opportunity.

        Keyed on (opportunity, item) -- and by a database constraint, not only
        here -- because two rules for one item would post the same sachets
        twice. Omitting `reports`, `cases`, `forms` or `status` on an edit
        keeps what is there.
        """
        from django.core.exceptions import PermissionDenied

        from connect_labs.labs.access.scopes import may_use
        from connect_labs.supply_chain import scopes
        from connect_labs.supply_chain.data_access import _fresh
        from connect_labs.supply_chain.stock.services.dispensing import (
            validate_cases,
            validate_lines,
            validate_reports,
        )

        # The rule's opportunity is where visits are read FROM, so it is a scope
        # in its own right: labs-only and this programme's, before anything else
        # (a rule on a real opportunity must not be savable at all), and one the
        # caller may use.
        problem = scopes.opportunity_problem(self._require_program(), data["opportunity_id"])
        if problem:
            raise ValueError(f"a dispensing rule cannot name this opportunity: {problem}")
        denied = may_use(self.caller, opportunity_id=data["opportunity_id"])
        if denied:
            raise PermissionDenied(denied)

        item = self._resolve_item(data["item_id"])
        point = self._require_supply_point(data["resupply_point_id"], "resupply point")
        if point.kind in ("user_held", "in_transit"):
            raise ValueError(
                f"{point.name} is not a store: workers are resupplied from a store, so a rule's resupply "
                "point must be one"
            )
        if point.opportunity_id not in (None, data["opportunity_id"]):
            raise ValueError(
                f"{point.name} belongs to opportunity {point.opportunity_id}, not {data['opportunity_id']}"
            )
        defaults = {
            "lines": validate_lines(data["lines"], item),
            "resupply_point": point,
            "active_from": data["active_from"],
        }
        for key in ("forms", "status"):
            if key in data:
                defaults[key] = data[key]
        if "reports" in data:
            defaults["reports"] = validate_reports(data["reports"])
        if "cases" in data:
            defaults["cases"] = validate_cases(data["cases"])
        rule, _ = DispensingRule.objects.update_or_create(
            program_id=self._require_program(), opportunity_id=data["opportunity_id"], item=item, defaults=defaults
        )
        return _fresh(rule)

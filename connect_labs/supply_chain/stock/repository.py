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

from connect_labs.supply_chain.models import (
    Consignment,
    DispensingRule,
    Distribution,
    DistributionLine,
    Movement,
    StockCount,
    SupplyPoint,
)
from connect_labs.supply_chain.stock.services import posting


class StockRepositoryMixin:
    # ---- supply points ---------------------------------------------------

    def _supply_points(self):
        return SupplyPoint.objects.filter(program_id=self._require_program())

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

    def list_movements(self, supply_point_id=None, item_id=None, kind=None, since=None, limit=500):
        qs = Movement.objects.for_program(self._require_program()).select_related("commodity")
        if supply_point_id is not None:
            qs = qs.touching(self._require_supply_point(supply_point_id))
        if item_id is not None:
            qs = qs.filter(item_id=item_id)
        if kind is not None:
            qs = qs.filter(kind=kind)
        if since is not None:
            qs = qs.filter(occurred_on__gte=since)
        return list(qs[:limit])

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

    # ---- distributions ---------------------------------------------------

    def list_distributions(self, opportunity_id=None, supply_point_id=None, limit=200):
        qs = Distribution.objects.filter(program_id=self._require_program()).prefetch_related("lines")
        if opportunity_id is not None:
            qs = qs.filter(opportunity_id=opportunity_id)
        if supply_point_id is not None:
            qs = qs.filter(supply_point_id=supply_point_id)
        return list(qs[:limit])

    def get_distribution(self, distribution_id):
        return (
            Distribution.objects.filter(program_id=self._require_program(), pk=distribution_id)
            .prefetch_related("lines")
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
                quantity_unit=line["quantity_unit"],
            )

        posting.post_distribution(distribution, commodity)
        return _fresh(distribution)

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
        twice. Omitting `reports`, `forms` or `status` on an edit keeps what
        is there.
        """
        from django.core.exceptions import PermissionDenied

        from connect_labs.labs.access.scopes import may_use
        from connect_labs.supply_chain import scopes
        from connect_labs.supply_chain.data_access import _fresh
        from connect_labs.supply_chain.stock.services.dispensing import validate_lines, validate_reports

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
        rule, _ = DispensingRule.objects.update_or_create(
            program_id=self._require_program(), opportunity_id=data["opportunity_id"], item=item, defaults=defaults
        )
        return _fresh(rule)

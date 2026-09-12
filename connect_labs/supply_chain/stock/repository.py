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

from connect_labs.supply_chain.models import Distribution, DistributionLine, Movement, StockCount, SupplyPoint
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
        party = None
        if data.get("managed_by_party_id") is not None:
            party = self.get_party(data["managed_by_party_id"])
            if party is None:
                raise ValueError(f"party {data['managed_by_party_id']} does not exist")

        from connect_labs.supply_chain.data_access import _columns, _fresh

        defaults = _columns(SupplyPoint, {k: v for k, v in data.items() if k != "slug"})
        defaults["parent"] = parent
        defaults["managed_by_party"] = party
        point, _ = SupplyPoint.objects.update_or_create(
            program_id=self._require_program(), slug=data["slug"], defaults=defaults
        )
        point.full_clean(exclude=["parent", "managed_by_party"])
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

    # ---- counts ----------------------------------------------------------

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
        count.full_clean(exclude=["supply_point", "commodity", "item", "adjustment_movement", "recorded_by_party"])
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

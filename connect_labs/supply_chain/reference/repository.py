"""Reads and writes for the catalogue tier: products, trade items, suppliers.

A mixin rather than a second access class, for the reason
`StockRepositoryMixin` gives: every client keeps one object with one scope
and the operation handlers stay `access.<verb>`. Split out of data_access.py
because this tier owns a different concern from the rest -- what a programme
BUYS, as opposed to what it has decided, ordered or holds -- and a
1,200-line access class is a file nobody can hold in their head.

The scope rule these carry is the one that catches people out: a commodity,
a trade item and a supplier are scoped to the PROGRAMME, not to the
organisation. `mcp_tools` says so in its schema descriptions because the
opposite advice once made `commodity_list` come back empty against a
programme whose data was there.
"""

from django.db import transaction

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain import gs1
from connect_labs.supply_chain.models import Commodity, Item, Supplier


class ReferenceRepositoryMixin:
    def get_commodity(self, slug: str):
        return self._reference(Commodity).filter(slug=slug).first()

    def upsert_commodity(self, data: dict):
        from connect_labs.supply_chain.data_access import _columns, _fresh

        obj, _ = Commodity.objects.update_or_create(
            scope_key=self.scope_key,
            slug=data["slug"],
            defaults=_columns(Commodity, {k: v for k, v in data.items() if k != "slug"}),
        )
        return _fresh(obj)

    def list_items(self):
        return list(self._reference(Item).select_related("commodity").all())

    def items_by_id(self) -> dict:
        return {item.pk: item for item in self.list_items()}

    def get_item(self, item_id: int):
        return self._reference(Item).select_related("commodity").filter(pk=item_id).first()

    def get_item_by_sku(self, sku: str):
        return self._reference(Item).select_related("commodity").filter(sku=sku).first()

    def upsert_item(self, data: dict):
        """Create or update a trade item by SKU, validating any GS1 keys.

        A mistyped GTIN that silently persists is worse than a rejected one,
        and a mod-10 check digit is the cheapest guard there is. An item with
        no GTIN at all is perfectly normal and passes.
        """
        from connect_labs.supply_chain.data_access import GTIN_FIELDS, _columns, _fresh

        for field_name in GTIN_FIELDS:
            value = data.get(field_name)
            if value and not gs1.is_valid(str(value)):
                raise ValueError(f"{field_name} {value!r} fails its GS1 check digit")

        commodity = self._resolve_commodity(data.get("commodity_slug"))
        defaults = _columns(Item, {k: v for k, v in data.items() if k != "sku"})
        if commodity is not None:
            defaults["commodity"] = commodity
        if "components" in data:
            defaults["components"] = self._kit_components(data.get("components") or [])
        obj, _ = Item.objects.update_or_create(scope_key=self.scope_key, sku=data["sku"], defaults=defaults)
        return _fresh(obj)

    def _suppliers(self):
        return self._reference(Supplier).select_related("org", "org__supplier_profile")

    def list_suppliers(self, search: str | None = None):
        """The companies linked into this program as suppliers.

        Program-scoped on purpose: "is this supplier already on file" means
        on file HERE. A company another program buys from is linked in by
        `create_supplier`, which finds it rather than duplicating it.
        """
        found = list(self._suppliers().all())
        if not search:
            return found
        needle = search.lower().strip()
        return [
            supplier
            for supplier in found
            if needle in (supplier.name or "").lower()
            or any(needle in (c.get("email") or "").lower() for c in supplier.contacts)
        ]

    def get_supplier(self, supplier_id: int):
        return self._suppliers().filter(pk=supplier_id).first()

    def create_supplier(self, data: dict):
        """Link a company into this program as a supplier.

        The company is `org_id` when given; otherwise the organisation holding
        `connect_organization_id`, or the one the name already belongs to, or a
        new one (`identity.find_or_mint_supplier_org`). Linking a company that
        is already this program's supplier returns that supplier.
        """
        from connect_labs.supply_chain.data_access import _fresh

        org = None
        if data.get("org_id") is not None:
            org = self.get_org(data["org_id"])
            if org is None:
                raise ValueError(f"organisation {data['org_id']} does not exist")
        company = {k: v for k, v in data.items() if k != "org_id"}
        return _fresh(Supplier.objects.enrol(self.scope_key, org=org, **company))

    @transaction.atomic
    def update_supplier(self, supplier_id: int, data: dict):
        """Edit a supplier: the company's facts on the company, the program's on the link.

        Three rules about the company, each because someone else owns part of it:

          * Setting a Connect id another organisation already holds MOVES this
            program's supplier onto that organisation -- that is who the
            supplier turns out to be. Nothing else in the edit then touches the
            company: the facts on the form described the old one, and writing
            them onto the organisation it turned out to be would overwrite a
            company other programs share.
          * A name or country Connect has set is Connect's. A blank one may be
            filled in -- that is adding knowledge, not contradicting Connect.
          * A company in the LLO directory is named by the directory: renaming
            it here would make the next import treat it as gone and mint a
            second copy under the directory's name.
        """
        from connect_labs.supply_chain.data_access import _fresh

        supplier = self.get_supplier(supplier_id)
        if supplier is None:
            raise ValueError(f"supplier {supplier_id} not found")
        org = supplier.org
        rebound = False

        connect_id = data.get("connect_organization_id")
        if connect_id and connect_id != org.connect_organization_id:
            holder = LabsOrg.objects.filter(connect_organization_id=connect_id).first()
            if holder is not None:
                if Supplier.objects.filter(scope_key=self.scope_key, org=holder).exclude(pk=supplier.pk).exists():
                    raise ValueError(
                        f"{holder.name} is already a supplier in this program; "
                        "edit that supplier rather than binding a second one to it"
                    )
                supplier.org = holder
                org = holder
                rebound = True
            elif org.connect_organization_id is not None:
                raise ValueError(
                    f"{org.name} is Connect organisation {org.connect_organization_id}; "
                    "an organisation's Connect id is its identity and does not change"
                )
            else:
                org.connect_organization_id = connect_id
                org.save(update_fields=["connect_organization_id", "updated_at"])

        if not rebound:
            self._edit_company(org, data)
        for key in ("status", "notes"):
            if key in data:
                setattr(supplier, key, data[key] if data[key] is not None else "")
        supplier.save()
        return _fresh(supplier)

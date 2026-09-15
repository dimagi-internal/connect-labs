"""Write screens for the reference tier: products, trade items, suppliers.

These are group 2 of the supply UI. Group 1 (`procurement/views.py`) put the
sourcing lifecycle in a browser; this puts the things that lifecycle names
there, so a programme can be set up without an API client.

Every screen is an `OperationFormView` — see `form_views.py` for the one
substitution that module makes and why. Nothing here calls `.save()`.

**Scoping, and why it is repeated rather than centralised.** Each edit screen
looks its row up filtered by the caller's `scope_key`, and 404s rather than
403s when it misses. Filtering in the lookup means a row from another
programme is not found rather than found-and-then-refused, which is one fewer
place for a check to be forgotten — and a 404 says less to a caller probing
for ids than a 403 does.
"""

from django.http import Http404
from django.urls import reverse

from connect_labs.supply_chain.api_views import _access
from connect_labs.supply_chain.form_views import OperationFormView
from connect_labs.supply_chain.models import Commodity, Item, Supplier
from connect_labs.supply_chain.reference_forms import CommodityForm, ItemForm, SupplierForm


class _ScopedInstanceMixin:
    """An edit screen's row, found inside the caller's scope or not at all."""

    model = None
    lookup_kwarg = ""
    lookup_field = "pk"
    missing = "not found"

    def instance(self):
        if not hasattr(self, "_instance"):
            scope = _access(self.request).scope_key
            lookup = {self.lookup_field: self.kwargs[self.lookup_kwarg], "scope_key": scope}
            found = self.model.objects.filter(**lookup).first()
            if found is None:
                raise Http404(self.missing)
            self._instance = found
        return self._instance

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.instance()
        return kwargs


# ---- products ----------------------------------------------------------


class _ProductScreen(OperationFormView):
    operation = "commodity_upsert"
    form_class = CommodityForm

    def breadcrumb(self, **kwargs):
        return [{"label": "Catalogue", "href": reverse("supply_chain:catalogue")}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:catalogue")

    def redirect_to(self, result):
        return reverse("supply_chain:product_detail", args=[result["slug"]])


class ProductCreateView(_ProductScreen):
    title = "New product"
    intro = (
        "A product is a specification, not a thing you can order — “RUTF, 92 g sachet, "
        "150 to the carton” describes what every supplier's version has to be. Trade "
        "items are the orderable layer underneath."
    )
    submit_label = "Add product"


class ProductUpdateView(_ScopedInstanceMixin, _ProductScreen):
    title = "Edit product"
    submit_label = "Save changes"
    model = Commodity
    lookup_kwarg = "slug"
    lookup_field = "slug"
    missing = "no such product in this catalogue"
    footnote = (
        "Specification requirements and anything else set through the API are left as they are — "
        "this screen only writes what it shows."
    )


# ---- trade items -------------------------------------------------------


class _ItemScreen(OperationFormView):
    operation = "item_upsert"
    form_class = ItemForm

    def breadcrumb(self, **kwargs):
        return [{"label": "Catalogue", "href": reverse("supply_chain:catalogue")}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:catalogue")

    def redirect_to(self, result):
        return reverse("supply_chain:item_detail", args=[result["id"]])


class ItemCreateView(_ItemScreen):
    title = "New trade item"
    intro = (
        "One manufacturer's version of a product, packed their way. This is the layer that "
        "knows whether the carton holds 144 sachets or 150 — the difference a per-sachet "
        "comparison lives or dies on."
    )
    submit_label = "Add item"

    def get_initial(self):
        initial = super().get_initial()
        # Arriving from a product page, that product is the answer. A picker
        # that opens already correct is one fewer thing to get wrong.
        slug = self.request.GET.get("product")
        if slug:
            found = Commodity.objects.filter(scope_key=_access(self.request).scope_key, slug=slug).first()
            if found is not None:
                initial["commodity"] = found.pk
        return initial


class ItemUpdateView(_ScopedInstanceMixin, _ItemScreen):
    title = "Edit trade item"
    submit_label = "Save changes"
    model = Item
    lookup_kwarg = "item_id"
    missing = "no such trade item in this catalogue"


# ---- suppliers ---------------------------------------------------------


class _SupplierScreen(OperationFormView):
    form_class = SupplierForm

    def breadcrumb(self, **kwargs):
        return [{"label": "Suppliers", "href": reverse("supply_chain:suppliers")}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:suppliers")

    def redirect_to(self, result):
        return reverse("supply_chain:supplier_detail", args=[result["id"]])


class SupplierCreateView(_SupplierScreen):
    operation = "supplier_create"
    title = "New supplier"
    intro = (
        "Somebody who could supply this programme. Adding one costs nothing and commits "
        "nothing — the status says how far the relationship has got."
    )
    submit_label = "Add supplier"


class SupplierUpdateView(_ScopedInstanceMixin, _SupplierScreen):
    operation = "supplier_update"
    title = "Edit supplier"
    submit_label = "Save changes"
    model = Supplier
    lookup_kwarg = "supplier_id"
    missing = "no such supplier in this programme"
    footnote = "Contacts and qualifications are left as they are — this screen only writes what it shows."

    def fixed(self, **kwargs):
        return {"supplier_id": int(kwargs["supplier_id"])}

    def redirect_to(self, result):
        return reverse("supply_chain:supplier_detail", args=[self.kwargs["supplier_id"]])

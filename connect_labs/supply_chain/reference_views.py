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
from connect_labs.supply_chain.reference_forms import (
    CommodityForm,
    ComponentLineFormSet,
    ItemForm,
    RequirementLineFormSet,
    StatedFigureLineFormSet,
    SupplierForm,
    figure_choices,
)


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
    """A product, plus the requirements every trade item of it is checked against.

    The requirements are a formset, sent on every save that carried it -- an
    empty list included, because removing the last requirement is how a
    product stops having a specification. A post with no formset at all (an
    older client, a test) has said nothing about them and changes nothing.
    """

    operation = "commodity_upsert"
    form_class = CommodityForm
    template_name = "supply_chain/product_form.html"

    def initial_requirements(self):
        return []

    def requirement_formset(self, data=None):
        return RequirementLineFormSet(
            data,
            initial=None if data else self.initial_requirements(),
            prefix="requirements",
            form_kwargs={"figures": figure_choices(self.op("commodity_list"), self.op("item_list"))},
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if context.get("has_program_context") and "requirements" not in context:
            context["requirements"] = self.requirement_formset(
                self.request.POST if self.request.method == "POST" else None
            )
        return context

    def form_valid(self, form):
        if "requirements-TOTAL_FORMS" in self.request.POST:
            requirements = self.requirement_formset(self.request.POST)
            if not requirements.is_valid():
                return self.render_to_response(self.get_context_data(form=form, requirements=requirements))
            self._requirements = [
                row.requirement()
                for row in requirements.forms
                if row.cleaned_data and not row.cleaned_data.get("DELETE")
            ]
        return super().form_valid(form)

    def fixed(self, **kwargs):
        if not hasattr(self, "_requirements"):
            return {}
        return {"data": {"spec_requirements": self._requirements}}

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
    footnote = "Anything else set through the API is left as it is — this screen only writes what it shows."

    def initial_requirements(self):
        return [
            {
                "field": r.get("field", ""),
                "operator": r.get("operator", ""),
                "value": r.get("value", ""),
                "unit": r.get("unit", ""),
                "rationale": r.get("rationale", ""),
            }
            for r in self.instance().spec_requirements or []
        ]


# ---- trade items -------------------------------------------------------


class _ItemScreen(OperationFormView):
    """A trade item, plus what is inside it when it is a kit.

    The components are a formset, the way a round's lines are, and are sent
    on every save -- an empty list included, because removing the last
    component is how a kit stops being one.
    """

    operation = "item_upsert"
    form_class = ItemForm
    template_name = "supply_chain/item_form.html"

    def commodities(self):
        return [(c["slug"], c["name"]) for c in self.op("commodity_list")]

    def component_formset(self, data=None, initial=None):
        return ComponentLineFormSet(
            data, initial=initial, prefix="components", form_kwargs={"commodities": self.commodities()}
        )

    def initial_components(self):
        return []

    def initial_stated(self):
        return []

    def stated_targets(self):
        """Products a component figure may be stated for: those inside the kit."""
        names = dict(self.commodities())
        slugs = []
        for component in self.existing_components():
            slug = component.get("commodity_slug")
            if slug and slug not in slugs:
                slugs.append(slug)
        if self.request.method == "POST":
            for key, value in self.request.POST.items():
                if key.startswith("components-") and key.endswith("-commodity_slug") and value and value not in slugs:
                    slugs.append(value)
        return [(slug, f"{names.get(slug, slug)} (inside the kit)") for slug in slugs]

    def stated_formset(self, data=None):
        return StatedFigureLineFormSet(
            data,
            initial=None if data else self.initial_stated(),
            prefix="stated",
            form_kwargs={"targets": self.stated_targets()},
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if context.get("has_program_context") and "components" not in context:
            context["components"] = (
                self.component_formset(self.request.POST)
                if self.request.method == "POST"
                else self.component_formset(initial=self.initial_components())
            )
        if context.get("has_program_context") and "stated" not in context:
            context["stated"] = self.stated_formset(self.request.POST if self.request.method == "POST" else None)
        return context

    def _stated_figures(self, form, components):
        """(item figures, {slug: figures}) from the stated formset, or None if not sent."""
        if "stated-TOTAL_FORMS" not in self.request.POST:
            return None
        stated = self.stated_formset(self.request.POST)
        if not stated.is_valid():
            return self.render_to_response(self.get_context_data(form=form, components=components, stated=stated))
        own, per_product = {}, {}
        for row in stated.cleaned_data:
            if not row or row.get("DELETE") or not row.get("field"):
                continue
            from connect_labs.supply_chain.reference_forms import spec_value

            target = "" if row.get("applies_to") in (None, "", "item") else row["applies_to"]
            bucket = own if not target else per_product.setdefault(target, {})
            bucket[row["field"]] = spec_value(row["value"])
        return own, per_product

    def existing_components(self) -> list:
        return []

    def form_valid(self, form):
        if "components-TOTAL_FORMS" not in self.request.POST:
            # A post that carried no component rows at all -- not an empty
            # list, no formset -- has said nothing about the contents, so it
            # changes nothing. Sending [] here would silently unmake a kit.
            return super().form_valid(form)
        components = self.component_formset(self.request.POST)
        if not components.is_valid():
            return self.render_to_response(self.get_context_data(form=form, components=components))
        figures = self._stated_figures(form, components)
        if figures is not None and not isinstance(figures, tuple):
            return figures  # the stated-figures formset had an error: re-rendered
        per_product = figures[1] if figures else None
        if figures is not None:
            self._own_figures = figures[0]
        # A component's stated specification is not on this screen -- like the
        # item's own, it arrives through the API -- so an edit keeps what each
        # product already had rather than wiping it by omission.
        # Matched by the row's position in the stored list, not by product: a
        # kit may hold two formulations of one product, each with its own.
        existing = self.existing_components()
        kept = []
        for row in components.cleaned_data:
            if not row or row.get("DELETE") or not row.get("commodity_slug"):
                continue
            entry = {
                "commodity_slug": row["commodity_slug"],
                "quantity": str(row["quantity"]),
                "base_unit": row["base_unit"],
            }
            index = row.get("source_index")
            source = existing[index] if index is not None and index < len(existing) else None
            if per_product is not None:
                # The screen now says what each product states, so what it
                # sent is the answer -- including nothing. A product inside
                # the kit twice takes the figures on its first row.
                if per_product.get(row["commodity_slug"]):
                    entry["spec_attributes"] = per_product.pop(row["commodity_slug"])
            # A row switched to another product does not take the old one's figures.
            elif source and source.get("commodity_slug") == row["commodity_slug"] and source.get("spec_attributes"):
                entry["spec_attributes"] = source["spec_attributes"]
            kept.append(entry)
        self._components = kept
        return super().form_valid(form)

    def fixed(self, **kwargs):
        data = {}
        if hasattr(self, "_components"):
            data["components"] = self._components
        if hasattr(self, "_own_figures"):
            data["spec_attributes"] = self._own_figures
        return {"data": data} if data else {}

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

    def existing_components(self):
        return self.instance().components or []

    def initial_components(self):
        return [
            {
                "commodity_slug": c.get("commodity_slug"),
                "quantity": c.get("quantity"),
                "base_unit": c.get("base_unit"),
                "source_index": index,
            }
            for index, c in enumerate(self.existing_components())
        ]

    def initial_stated(self):
        rows = [
            {"applies_to": "item", "field": field, "value": value}
            for field, value in (self.instance().spec_attributes or {}).items()
        ]
        for component in self.existing_components():
            for field, value in (component.get("spec_attributes") or {}).items():
                rows.append({"applies_to": component.get("commodity_slug"), "field": field, "value": value})
        return rows


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

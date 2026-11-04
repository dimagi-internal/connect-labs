"""Screens that edit the people and credentials held on a supplier.

A supplier's contacts drive who its emails go to (`procurement/services/contacts.py`
reads the `use` marker: the one main contact, the people copied, and old addresses
kept as history). Until this screen the only way to set a marker was the API.

Both screens write through `supplier_update` -- the company's contacts are the
company's, shared by every programme that buys from it -- and follow the
`ItemForm` pattern: a repeating row per contact, sent whole on every save.
"""

from django import forms
from django.http import Http404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from connect_labs.supply_chain.form_views import OperationFormView
from connect_labs.supply_chain.forms import DATE, INPUT, SELECT, ScopedForm
from connect_labs.supply_chain.models import Commodity, Supplier
from connect_labs.supply_chain.procurement.services.contacts import clean_email
from connect_labs.supply_chain.reference import qualifications as quals
from connect_labs.supply_chain.reference.views import _ScopedInstanceMixin

# What a contact is used for, in the words a programme manager would use.
USE_CHOICES = [
    ("", _("Not set (the first one with an address is the main contact)")),
    ("to", _("Main contact: emails go to them")),
    ("cc", _("Copied on every email")),
    ("history", _("Old address: keep on file, never email")),
]

_CONTACT_KEYS = ("name", "email", "phone", "role", "use", "note")


class ContactLineForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=200, required=False, widget=forms.TextInput(attrs=INPUT))
    email = forms.CharField(label=_("Email"), max_length=254, required=False, widget=forms.TextInput(attrs=INPUT))
    phone = forms.CharField(label=_("Phone"), max_length=64, required=False, widget=forms.TextInput(attrs=INPUT))
    role = forms.CharField(
        label=_("Role"),
        max_length=64,
        required=False,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. sales")}),
    )
    use = forms.ChoiceField(
        label=_("Used for"), required=False, choices=USE_CHOICES, widget=forms.Select(attrs=SELECT)
    )
    note = forms.CharField(label=_("Note"), max_length=500, required=False, widget=forms.TextInput(attrs=INPUT))
    # Which stored contact this row began as, so a key this screen does not
    # show survives an edit (the same job `source_index` does for kit components).
    source_index = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput)

    def clean_email(self):
        raw = (self.cleaned_data.get("email") or "").strip()
        if not raw:
            return ""
        address = clean_email(raw)
        if not address:
            raise forms.ValidationError(_("That does not contain an email address. Leave it empty if there is none."))
        return address

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        if cleaned.get("use") in ("to", "cc") and not cleaned.get("email"):
            self.add_error("email", _("Someone who is emailed needs an address."))
        return cleaned


ContactLineFormSet = forms.formset_factory(ContactLineForm, extra=0, min_num=0, can_delete=True)


def merge_contacts(existing: list, rows: list[dict]) -> list[dict]:
    """The contact list the screen's rows describe.

    A row keeps any stored key the screen does not show (matched by its position in
    the stored list); a blank value drops that key rather than storing "". Rows with
    nothing in them are dropped.
    """
    kept = []
    for row in rows:
        index = row.get("source_index")
        source = existing[index] if index is not None and index < len(existing) else {}
        entry = {k: v for k, v in (source if isinstance(source, dict) else {}).items() if k not in _CONTACT_KEYS}
        for key in _CONTACT_KEYS:
            value = (row.get(key) or "").strip()
            if value:
                entry[key] = value
        if any(entry.get(k) for k in ("name", "email", "phone", "role", "note")):
            kept.append(entry)
    return kept


def contacts_problem(contacts: list[dict]) -> str:
    """Why this list cannot drive email, or "" when it can."""
    live = [c for c in contacts if c.get("use") != "history"]
    marked = any(c.get("use") in ("to", "cc") for c in live)
    mains = [c for c in live if c.get("use") == "to"]
    if len(mains) > 1:
        return "Only one person can be the main contact; make the others “copied”."
    if marked and not mains:
        return "People are marked to be copied but nobody is the main contact. Choose one."
    return ""


class _NoFields(ScopedForm):
    """The page's own fields are all in its formset."""

    class Meta:
        model = Supplier
        fields: list = []


class SupplierContactsView(_ScopedInstanceMixin, OperationFormView):
    operation = "supplier_update"
    title = "Who to contact"
    intro = (
        "Emails to this supplier go to the one main contact and copy the people marked “copied”. "
        "An address that has stopped working can stay on file as “old” and is left off new emails."
    )
    submit_label = "Save contacts"
    form_class = _NoFields
    template_name = "supply_chain/supplier_people_form.html"
    model = Supplier
    lookup_kwarg = "supplier_id"
    missing = "no such supplier in this programme"
    footnote = "Contacts belong to the company, so a change here shows in every programme that buys from them."
    prefix = "contacts"
    row_label = "contact"
    add_label = "+ Add a contact"
    columns = "minmax(0,1.3fr) minmax(0,1.6fr) minmax(0,1fr) minmax(0,1fr) minmax(0,1.6fr) minmax(0,1.3fr) auto"

    def stored(self) -> list:
        return [c for c in (self.instance().contacts or []) if isinstance(c, dict)]

    def formset(self, data=None):
        initial = None if data else [{**c, "source_index": i} for i, c in enumerate(self.stored())]
        return ContactLineFormSet(data, initial=initial, prefix=self.prefix)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if context.get("has_program_context"):
            if "rows" not in context:
                context["rows"] = self.formset(self.request.POST if self.request.method == "POST" else None)
            context.update(
                rows_id=f"{self.prefix}-rows",
                columns=self.columns,
                add_label=self.add_label,
                supplier=self.instance(),
                section_title=self.title,
            )
        return context

    def form_valid(self, form):
        if f"{self.prefix}-TOTAL_FORMS" not in self.request.POST:
            raise Http404("no rows posted")  # a post that says nothing changes nothing
        rows = self.formset(self.request.POST)
        if not rows.is_valid():
            return self.render_to_response(self.get_context_data(form=form, rows=rows))
        self._value = self.merge(rows)
        problem = self.problem(self._value)
        if problem:
            form.add_error(None, problem)
            return self.render_to_response(self.get_context_data(form=form, rows=rows))
        return super().form_valid(form)

    def merge(self, rows):
        live = [r for r in rows.cleaned_data if r and not r.get("DELETE")]
        return merge_contacts(self.stored(), live)

    def problem(self, value) -> str:
        return contacts_problem(value)

    def fixed(self, **kwargs):
        return {"supplier_id": int(kwargs["supplier_id"]), "data": {"contacts": self._value}}

    def breadcrumb(self, **kwargs):
        supplier = self.instance()
        return [
            {"label": "Suppliers", "href": reverse("supply_chain:suppliers")},
            {"label": supplier.name, "href": reverse("supply_chain:supplier_detail", args=[supplier.pk])},
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:supplier_detail", args=[self.kwargs["supplier_id"]])

    def redirect_to(self, result):
        return reverse("supply_chain:supplier_detail", args=[self.kwargs["supplier_id"]])


class QualificationLineForm(forms.Form):
    name = forms.CharField(
        label=_("Qualification"),
        max_length=200,
        widget=forms.TextInput(
            attrs={**INPUT, "list": "qualification-suggestions", "placeholder": _("e.g. UNICEF supplier approval")}
        ),
    )
    expires_on = forms.DateField(
        label=_("Valid until"), required=False, widget=forms.DateInput(attrs=DATE, format="%Y-%m-%d")
    )
    note = forms.CharField(
        label=_("Note"),
        max_length=500,
        required=False,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("reference number, where the proof is kept")}),
    )
    source_index = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput)


QualificationLineFormSet = forms.formset_factory(QualificationLineForm, extra=0, min_num=0, can_delete=True)


class SupplierQualificationsView(SupplierContactsView):
    """What a supplier holds (approvals, registrations), and what is usually asked for."""

    title = "Qualifications"
    intro = (
        "What this company holds that a buyer relies on: approvals, registrations, certificates. "
        "It is a fact about the company, not about one quote, so it is kept here."
    )
    submit_label = "Save qualifications"
    footnote = "Qualifications belong to the company, so a change here shows in every programme that buys from them."
    prefix = "quals"
    add_label = "+ Add a qualification"
    columns = "minmax(0,2fr) minmax(0,1fr) minmax(0,2fr) auto"

    def stored(self) -> list:
        return [{"name": q} if isinstance(q, str) else q for q in (self.instance().qualifications or []) if q]

    def formset(self, data=None):
        initial = None if data else [{**q, "source_index": i} for i, q in enumerate(self.stored())]
        return QualificationLineFormSet(data, initial=initial, prefix=self.prefix)

    def merge(self, rows):
        existing, kept = self.stored(), []
        for row in rows.cleaned_data:
            if not row or row.get("DELETE") or not (row.get("name") or "").strip():
                continue
            index = row.get("source_index")
            source = existing[index] if index is not None and index < len(existing) else {}
            entry = {k: v for k, v in source.items() if k not in ("name", "expires_on", "note")}
            entry["name"] = row["name"].strip()
            if row.get("expires_on"):
                entry["expires_on"] = row["expires_on"].isoformat()
            if (row.get("note") or "").strip():
                entry["note"] = row["note"].strip()
            kept.append(entry)
        return kept

    def problem(self, value) -> str:
        return ""

    def usual(self):
        categories = Commodity.objects.filter(scope_key=self.access().scope_key).values_list("category", flat=True)
        return quals.usual_for(dict.fromkeys(categories))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if context.get("has_program_context"):
            usual = self.usual()
            context["suggestions"] = [{"name": u.name, "why": u.why} for u in usual]
            context["suggestions_note"] = (
                "The usual ones for what this programme buys. Start typing a name to pick one." if usual else ""
            )
        return context

    def fixed(self, **kwargs):
        return {"supplier_id": int(kwargs["supplier_id"]), "data": {"qualifications": self._value}}

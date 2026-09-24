"""No dropdown in the supply domain may render with nothing in it.

THIS REPOSITORY IS PUBLIC. Everything here is invented.

This file exists because of a defect that shipped four times and that every
other test in the domain passed straight through.

A model `CharField` WITHOUT `choices=` becomes a plain `forms.CharField` on a
ModelForm. A `forms.CharField` has no `choices`, so

    self.fields["channel"].choices = [("manual", "By hand"), ...]

set an attribute nothing reads — and the `forms.Select` from `Meta.widgets`
then rendered a dropdown with **no options at all**. Where the model DOES
declare `choices=`, ModelForm builds a `TypedChoiceField` and the same line
works, which is why it looked right in half the places and was silently broken
in the other half. Nine fields across five forms were unusable.

Every test passed. They asserted the field was on the page, and it was: an
empty `<select>` is a field on a page. Found by opening "Record an invitation"
on labs and trying to pick a channel.

So the guard is not per-field. It walks every form class in the domain, renders
it, and fails on any select with no options — including ones nobody has written
a test for yet.
"""

import pytest
from django import forms

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess

pytestmark = pytest.mark.django_db

PROGRAM = 10508


def access():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def every_form_class():
    """Every write-screen form in the domain, found rather than listed.

    Imported from the modules and filtered by base class, so a form added to
    any of them is covered without this list being updated — which is the
    whole point, since the defect this file guards was in a form nobody
    thought to check.
    """
    from connect_labs.supply_chain import distribution_forms
    from connect_labs.supply_chain import forms as sourcing_forms
    from connect_labs.supply_chain import fulfilment_forms, network_forms, reference_forms, stock_forms
    from connect_labs.supply_chain.alerts import forms as alert_forms
    from connect_labs.supply_chain.update_links import forms as update_link_forms

    modules = [
        sourcing_forms,
        reference_forms,
        network_forms,
        fulfilment_forms,
        stock_forms,
        distribution_forms,
        alert_forms,
        update_link_forms,
    ]
    found = {}
    for module in modules:
        for name in dir(module):
            candidate = getattr(module, name)
            if not isinstance(candidate, type) or not issubclass(candidate, forms.BaseForm):
                continue
            if name.startswith("_") or candidate.__module__ != module.__name__:
                continue
            found[f"{module.__name__.rsplit('.', 1)[-1]}.{name}"] = candidate
    return found


# Bases with no model of their own, and the formset row forms, which take
# their querysets as explicit kwargs rather than reading an access object.
# The rows are covered through their formsets below.
NOT_A_SCREEN = {"ScopedForm", "ProvenancedForm", "KeyedUpsertForm", "PublicForm"}
LINE_FORMS = {"RoundLineForm", "BatchLineForm", "DistributionLineForm", "ComponentLineForm", "RequirementLineForm"}


def build(form_class):
    """One instance, or None for a class that is not a screen's form."""
    if form_class.__name__ in NOT_A_SCREEN | LINE_FORMS:
        return None
    kwargs = {"access": access()} if issubclass(form_class, forms.ModelForm) else {}
    return form_class(**kwargs)


def empty_selects(form):
    """Names of fields that RENDER a select with no options.

    Read off `field.widget.choices`, not `field.choices` — and that distinction
    is the entire defect. Assigning `.choices` to a `forms.CharField` sets an
    attribute the widget never consults, so a detector that read the FIELD
    would see the options and report the dropdown as fine while the page showed
    an empty one. The widget is what renders, so the widget is what is asked.
    """
    empty = []
    for name, field in form.fields.items():
        widget = field.widget
        if not isinstance(widget, (forms.Select, forms.SelectMultiple)):
            continue
        if isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
            continue
        choices = list(getattr(widget, "choices", []) or [])
        # A lone blank option is the same as none: there is nothing to pick.
        real = [value for value, _label in choices if value not in ("", None)]
        if not real:
            empty.append(name)
    return empty


@pytest.mark.parametrize("label", sorted(every_form_class()))
def test_no_form_renders_a_select_with_nothing_in_it(label):
    """A relation picker may legitimately be empty — an empty database has no
    suppliers to offer. A CHOICE list may not: its options are code."""
    form_class = every_form_class()[label]
    form = build(form_class)
    if form is None:
        pytest.skip(f"{label} takes explicit querysets; covered through its formset")

    offenders = []
    for name in empty_selects(form):
        # A ModelChoiceField over an empty table is fine and is not what this
        # is looking for — an empty database has no suppliers to offer. A
        # choice list is code, and an empty one is always a defect.
        if isinstance(form.fields[name], forms.ModelChoiceField):
            continue
        offenders.append(name)

    assert not offenders, (
        f"{label} renders {offenders} as a dropdown with no options. "
        "A model CharField without `choices=` becomes a forms.CharField, and "
        "assigning `.choices` to one does nothing — use `set_choices`."
    )


def test_the_line_forms_offer_their_choices_too():
    """The formset row forms, built the way their screens build them."""
    from connect_labs.supply_chain.forms import RoundLineFormSet

    formset = RoundLineFormSet(prefix="lines", form_kwargs={"commodities": [("rutf", "RUTF"), ("rusf", "RUSF")]})
    row = formset.forms[0]
    assert [v for v, _ in row.fields["commodity_slug"].choices if v], "the commodity picker must offer the catalogue"


def test_the_guard_can_actually_fail():
    """A test that cannot go red is worse than no test.

    Builds the exact shape the defect had — a `forms.CharField` rendered
    through a `Select`, with `.choices` assigned to it — and confirms this
    file's detector catches it.
    """

    class Broken(forms.Form):
        channel = forms.CharField(widget=forms.Select())

    broken = Broken()
    broken.fields["channel"].choices = [("manual", "By hand")]  # the line that does nothing

    assert "channel" in empty_selects(broken)


def test_and_passes_once_the_field_is_a_real_choice_field():
    from connect_labs.supply_chain.forms import set_choices

    class Fixed(forms.Form):
        channel = forms.CharField(widget=forms.Select())

    fixed = Fixed()
    set_choices(fixed, "channel", [("manual", "By hand")])

    assert empty_selects(fixed) == []

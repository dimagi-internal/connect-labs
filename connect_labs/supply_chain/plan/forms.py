"""The screens that change the stock plan: its three inputs, and a buffer for one place."""

from crispy_forms.helper import FormHelper
from django import forms
from django.utils.translation import gettext_lazy as _

from connect_labs.supply_chain.forms import INPUT, MONEY_INPUT, SEARCHABLE
from connect_labs.supply_chain.models import SupplyPoint
from connect_labs.supply_chain.plan import service
from connect_labs.supply_chain.plan.models import StockPlan


class _PlanForm(forms.Form):
    def __init__(self, *args, access=None, **kwargs):
        self.access = access
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.disable_csrf = True


class PlanInputsForm(_PlanForm):
    """The three choices a partner may change. Leave the rate empty for each worker's own recent rate."""

    enrolment_per_flw_per_week = forms.DecimalField(
        label=_("New children each field worker enrols a week"),
        required=False,
        min_value=0,
        max_value=50,
        decimal_places=2,
        widget=forms.NumberInput(attrs={**MONEY_INPUT, "placeholder": _("recent rate")}),
        help_text=_("Leave empty to use each worker's own recent rate, worked out from their visits."),
    )
    buffer_days = forms.IntegerField(
        label=_("Extra days of stock (buffer)"),
        min_value=0,
        max_value=60,
        initial=service.DEFAULT_BUFFER_DAYS,
        widget=forms.NumberInput(attrs=INPUT),
        help_text=_("Days of extra stock to hand out on top of the week, for every worker and facility."),
    )
    target_cases = forms.IntegerField(
        label=_("Target number of children"),
        min_value=1,
        max_value=1000000,
        initial=service.DEFAULT_TARGET_CASES,
        widget=forms.NumberInput(attrs=INPUT),
        help_text=_("How many children the programme means to treat in all: 500 now, 2,500 in phase 3."),
    )

    def __init__(self, *args, item_id=None, **kwargs):
        self.item_id = item_id
        super().__init__(*args, **kwargs)
        plan = (
            StockPlan.objects.filter(program_id=self.access.program_id, item_id=item_id).first()
            if self.access
            else None
        )
        if plan is not None and not self.is_bound:
            self.initial.update(
                enrolment_per_flw_per_week=plan.enrolment_per_flw_per_week,
                buffer_days=plan.buffer_days,
                target_cases=plan.target_cases,
            )

    def payload(self) -> dict:
        data = self.cleaned_data
        rate = data.get("enrolment_per_flw_per_week")
        return {
            # None, not dropped: empty means back to the default.
            "enrolment_per_flw_per_week": None if rate is None else float(rate),
            "buffer_days": data["buffer_days"],
            "target_cases": data["target_cases"],
            "item_id": self.item_id,
        }


class PlanBufferPlaceForm(_PlanForm):
    """Extra days for one field worker or facility, in place of the buffer for everyone."""

    supply_point = forms.ModelChoiceField(
        label=_("Field worker or facility"),
        queryset=SupplyPoint.objects.none(),
        widget=forms.Select(attrs=SEARCHABLE),
    )
    days = forms.IntegerField(
        label=_("Extra days"),
        required=False,
        min_value=0,
        max_value=60,
        widget=forms.NumberInput(attrs=INPUT),
        help_text=_("Leave empty to put this place back on the buffer for everyone."),
    )

    def __init__(self, *args, item_id=None, **kwargs):
        self.item_id = item_id
        super().__init__(*args, **kwargs)
        program = getattr(self.access, "program_id", None)
        self.fields["supply_point"].queryset = (
            SupplyPoint.objects.filter(program_id=program)
            .exclude(kind__in=("in_transit", "supplier_site", "customs"))
            .order_by("name")
            if program
            else SupplyPoint.objects.none()
        )
        self.fields["supply_point"].empty_label = _("Select a place…")

    def payload(self) -> dict:
        plan = StockPlan.objects.filter(program_id=self.access.program_id, item_id=self.item_id).first()
        places = dict(plan.buffer_days_by_point or {}) if plan else {}
        key = str(self.cleaned_data["supply_point"].pk)
        if self.cleaned_data["days"] is None:
            places.pop(key, None)
        else:
            places[key] = self.cleaned_data["days"]
        return {"item_id": self.item_id, "buffer_days_by_point": places}

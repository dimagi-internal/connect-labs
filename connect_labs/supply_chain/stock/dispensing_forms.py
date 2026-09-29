"""The dispensing rule as a page. Lines are JSON: they are a small program
over form paths, and a field-per-line form would hide the structure a person
has to get right. The operation validates; this only parses."""

from crispy_forms.helper import FormHelper
from django import forms
from django.utils.translation import gettext_lazy as _

from connect_labs.supply_chain.forms import INPUT, SEARCHABLE, SELECT, TEXTAREA
from connect_labs.supply_chain.models import Item, SupplyPoint


class DispensingRuleForm(forms.Form):
    opportunity_id = forms.IntegerField(min_value=1, label=_("Opportunity"), widget=forms.NumberInput(attrs=INPUT))
    item = forms.ModelChoiceField(
        queryset=Item.objects.none(), label=_("What is given out"), widget=forms.Select(attrs=SEARCHABLE)
    )
    resupply_point = forms.ModelChoiceField(
        queryset=SupplyPoint.objects.none(),
        label=_("Workers are resupplied from"),
        widget=forms.Select(attrs=SEARCHABLE),
        help_text=_("A worker seen for the first time gets a supply point under this store."),
    )
    active_from = forms.DateField(
        label=_("Read visits from"),
        widget=forms.DateInput(attrs={**INPUT, "type": "date"}),
        help_text=_("Earlier visits are never read, so turning a rule on does not invent history."),
    )
    lines = forms.JSONField(
        label=_("Lines"),
        widget=forms.Textarea(attrs={**TEXTAREA, "rows": 10}),
        help_text=_(
            'A list. Stated: {"kind": "stated", "paths": ["form.x"], "unit": "sachet"}. Protocol: '
            '{"kind": "protocol", "given_paths": ["form.y"], "given_values": ["yes"], "quantity": "4", '
            '"unit": "sachet"} (leave given_values out to accept any answer; it is never empty). '
            'Value map: {"kind": "value_map", "paths": ["form.z"], '
            '"map": {"<answer>": 10}, "unit": "tablet"}. Any line may add "forms": [...] and, for a '
            'protocol, "requires_paths": [...].'
        ),
    )
    # Not `forms`: a class attribute of that name would shadow django.forms for
    # every field declared after it.
    form_names = forms.CharField(
        required=False,
        label=_("Forms read"),
        widget=forms.Textarea(attrs={**TEXTAREA, "rows": 2}),
        help_text=_("One form name or xmlns per line. Leave empty to read every form."),
    )
    reports = forms.JSONField(
        required=False,
        label=_("What the worker's app reports"),
        widget=forms.Textarea(attrs={**TEXTAREA, "rows": 4}),
        help_text=_('Optional: {"balance_paths": [...], "receipt": {"quantity_paths": [...], "date_paths": [...]}}'),
    )
    status = forms.ChoiceField(
        choices=[("active", _("On")), ("inactive", _("Off"))], widget=forms.Select(attrs=SELECT)
    )

    def __init__(self, *args, access=None, editing=False, **kwargs):
        self.access = access
        super().__init__(*args, **kwargs)
        if access is not None:
            self.fields["item"].queryset = Item.objects.filter(scope_key=access.scope_key)
            self.fields["resupply_point"].queryset = SupplyPoint.objects.filter(program_id=access.program_id).exclude(
                kind__in=("user_held", "in_transit")
            )
        if editing:
            # A rule is identified by its opportunity and item. Letting an edit
            # change either would silently create a second rule instead, so
            # they are shown and not editable (a disabled field ignores what
            # is posted and keeps the initial value).
            for name in ("opportunity_id", "item"):
                self.fields[name].disabled = True
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.disable_csrf = True

    def payload(self) -> dict:
        cleaned = self.cleaned_data
        payload = {
            "opportunity_id": cleaned["opportunity_id"],
            "item_id": cleaned["item"].pk,
            "resupply_point_id": cleaned["resupply_point"].pk,
            "active_from": cleaned["active_from"].isoformat(),
            "lines": cleaned["lines"],
            "forms": [name.strip() for name in (cleaned.get("form_names") or "").splitlines() if name.strip()],
            "status": cleaned["status"],
        }
        if cleaned.get("reports"):
            payload["reports"] = cleaned["reports"]
        return payload

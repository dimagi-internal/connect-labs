"""The form behind /supply/alerts/new/ and its edit screen.

The check kinds are read from `checks.KIND_CATEGORIES` when the form is built,
not copied here, so a check added to the domain is offered here the day it
ships -- the same live list `alert_subscription_create` validates against.
"""

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, Fieldset, Layout, Row
from django import forms
from django.utils.translation import gettext_lazy as _

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain import records
from connect_labs.supply_chain.alerts.models import CADENCES
from connect_labs.supply_chain.forms import INPUT, SEARCHABLE, SELECT
from connect_labs.supply_chain.models import Commodity, SupplyPoint

__all__ = ["AlertSubscriptionForm", "check_kind_choices"]

# What kind of finding each is, in words a procurement lead uses. "a fact
# nobody supplied" and "past a bound in your own data" read as machine output
# beside "stock below minimum" (the CHC render, iteration 4).
_CATEGORY_WORDS = {
    "missing": _("something nobody has told us yet"),
    "conflict": _("two records disagree"),
    "threshold": _("a figure past a limit you set"),
}

_CADENCE_LABELS = {
    "immediate": _("As it happens (checked every five minutes)"),
    "daily_digest": _("One email a day"),
}


def check_kind_choices():
    """Every check `run_checks` can emit, grouped by what kind of finding it is."""
    from connect_labs.supply_chain.checks import CATEGORIES, KIND_CATEGORIES

    order = {category: index for index, category in enumerate(CATEGORIES)}
    kinds = sorted(KIND_CATEGORIES.items(), key=lambda pair: (order.get(pair[1], 99), pair[0]))
    from connect_labs.supply_chain.alerts.labels import watch_label

    return [(kind, f"{watch_label(kind)} — {_CATEGORY_WORDS.get(category, category)}") for kind, category in kinds]


class _PointChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.name} ({obj.kind.replace('_', ' ')})"


class AlertSubscriptionForm(forms.Form):
    label = forms.CharField(
        label=_("What to call it"),
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. Low chlorine stock → the donor")}),
        help_text=_("Used as the email's subject, so the recipient knows which alert this is."),
    )
    check_kinds = forms.MultipleChoiceField(
        label=_("Checks to watch"),
        required=False,
        choices=(),
        widget=forms.CheckboxSelectMultiple,
        help_text=_(
            "Each is sent once, when it becomes true (by email or as a Connect message, as you choose below). One "
            "that stays true is not sent again; one that clears and comes back is news again."
        ),
    )
    movement_kinds = forms.MultipleChoiceField(
        label=_("Stock movements to watch"),
        required=False,
        choices=[(kind, kind.replace("_", " ").capitalize()) for kind in records.MOVEMENT_KINDS],
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Every matching movement recorded from now on. The past is not sent."),
    )
    supply_point = _PointChoice(
        label=_("Only at this supply point"),
        required=False,
        queryset=SupplyPoint.objects.none(),
        widget=forms.Select(attrs=SEARCHABLE),
        help_text=_("Leave empty for the whole programme."),
    )
    partner = forms.ModelChoiceField(
        label=_("Only this partner's places"),
        required=False,
        queryset=LabsOrg.objects.none(),
        widget=forms.Select(attrs=SEARCHABLE),
        help_text=_(
            "A partner's facilities and field workers alert its own staff. Leave empty for the whole programme: "
            "the summary across partners."
        ),
    )
    commodity = forms.ModelChoiceField(
        label=_("Only for this product"),
        required=False,
        queryset=Commodity.objects.none(),
        widget=forms.Select(attrs=SEARCHABLE),
        help_text=_("Leave empty for every product."),
    )
    recipient = forms.ChoiceField(
        label=_("Send to"),
        choices=[("me", _("Me")), ("email", _("An email address")), ("connect_message", _("A Connect message"))],
        initial="me",
        widget=forms.RadioSelect,
    )
    recipient_email = forms.EmailField(
        label=_("Email address"),
        required=False,
        widget=forms.EmailInput(attrs={**INPUT, "placeholder": "stores@example.org"}),
        help_text=_(
            "Anyone with an address: you, a colleague, a partner or a donor. The email carries the facts "
            "and a link to each record."
        ),
    )
    recipient_connect_username = forms.CharField(
        label=_("Connect username"),
        required=False,
        max_length=64,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": "their Connect username"}),
        help_text=_(
            "The person's username in the Connect app. The message is sent as you, through Open Chat Studio, "
            "so it needs your Open Chat Studio connection; they must have agreed to messages in the Connect "
            "app. If it cannot be sent, the sent log says why."
        ),
    )
    recipient_name = forms.CharField(
        label=_("Their name"),
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. the stores officer")}),
        help_text=_("So the alert and its sent log say who is told, not just an address."),
    )
    cadence = forms.ChoiceField(
        label=_("How often"),
        choices=[(c, _CADENCE_LABELS[c]) for c in CADENCES],
        initial="immediate",
        widget=forms.Select(attrs=SELECT),
    )
    active = forms.BooleanField(label=_("On"), required=False, initial=True)

    def __init__(self, *args, access=None, subscription=None, **kwargs):
        self.access = access
        self.subscription = subscription
        if subscription is not None and "initial" not in kwargs:
            kwargs["initial"] = {
                "label": subscription.label,
                "check_kinds": list(subscription.check_kinds or []),
                "movement_kinds": list(subscription.movement_kinds or []),
                "supply_point": subscription.supply_point_id,
                "commodity": subscription.commodity_id,
                "partner": subscription.partner_org_id,
                "recipient": (
                    "me"
                    if subscription.recipient_user_id
                    else "connect_message" if subscription.channel == "connect_message" else "email"
                ),
                "recipient_connect_username": subscription.recipient_connect_username,
                "recipient_email": subscription.recipient_email,
                "recipient_name": subscription.recipient_name,
                "cadence": subscription.cadence,
                "active": subscription.active,
            }
        super().__init__(*args, **kwargs)
        self.fields["check_kinds"].choices = check_kind_choices()
        program_id = getattr(access, "program_id", None) if access else None
        if program_id:
            self.fields["supply_point"].queryset = SupplyPoint.objects.filter(program_id=program_id).order_by("name")
            self.fields["commodity"].queryset = Commodity.objects.filter(scope_key=f"prog:{program_id}")
            from connect_labs.supply_chain.stock.services.issues import partner_by_point

            running = {org for org in partner_by_point(program_id).values() if org}
            if subscription is not None and subscription.partner_org_id:
                running.add(subscription.partner_org_id)
            self.fields["partner"].queryset = LabsOrg.objects.filter(pk__in=running).order_by("name")
        self.fields["supply_point"].empty_label = _("Anywhere in the programme")
        self.fields["commodity"].empty_label = _("Any product")
        self.fields["partner"].empty_label = _("Every partner")

        user = getattr(access, "user", None) if access else None
        if getattr(user, "email", ""):
            self.fields["recipient"].choices = [
                ("me", _("Me (%(email)s)") % {"email": user.email}),
                ("email", _("An email address")),
                ("connect_message", _("A Connect message")),
            ]
        # Someone else's alert: offer to leave its recipient alone, and make
        # that the default, so changing its cadence does not quietly move it
        # to whoever is editing.
        if (
            subscription is not None
            and subscription.recipient_user_id
            and subscription.recipient_user_id != getattr(user, "pk", None)
        ):
            owner = subscription.recipient_user
            name = subscription.recipient_label
            if owner.email and owner.email != name:
                name = f"{name} ({owner.email})"
            self.fields["recipient"].choices = [("keep", _("Keep: %(name)s") % {"name": name})] + list(
                self.fields["recipient"].choices
            )
            if not self.is_bound:
                self.initial["recipient"] = "keep"
        if subscription is None:
            del self.fields["active"]

        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        rows = [
            "label",
            Fieldset(
                str(_("What to watch")),
                Row(Column("check_kinds"), Column("movement_kinds"), css_class="grid md:grid-cols-[2fr,1fr] gap-x-6"),
            ),
            Fieldset(
                str(_("Narrow it")),
                Row(
                    Column("partner"),
                    Column("supply_point"),
                    Column("commodity"),
                    css_class="grid md:grid-cols-3 gap-x-6",
                ),
            ),
            Fieldset(
                str(_("Who hears, and how often")),
                Row(
                    Column("recipient"),
                    Column("recipient_email", "recipient_connect_username", "recipient_name"),
                    css_class="grid md:grid-cols-2 gap-x-6",
                ),
                "cadence",
            ),
        ]
        if "active" in self.fields:
            rows.append("active")
        self.helper.layout = Layout(*rows)

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("check_kinds") and not cleaned.get("movement_kinds"):
            raise forms.ValidationError(_("Pick at least one check or movement — an alert has to watch something."))
        if cleaned.get("recipient") == "email" and not cleaned.get("recipient_email"):
            self.add_error("recipient_email", _("Give the address to send to."))
        if cleaned.get("recipient") == "connect_message":
            if not cleaned.get("recipient_connect_username"):
                self.add_error("recipient_connect_username", _("Give the Connect username to message."))
            user = getattr(self.access, "user", None) if self.access else None
            if not getattr(user, "pk", None):
                self.add_error("recipient", _("A Connect message is sent as you, so you need to be signed in."))
        if cleaned.get("recipient") == "me":
            user = getattr(self.access, "user", None) if self.access else None
            if not getattr(user, "pk", None):
                self.add_error("recipient", _("There is no signed-in user to send to; give an email address."))
        return cleaned

    def payload(self) -> dict:
        cleaned = self.cleaned_data
        data = {
            "label": cleaned.get("label") or "",
            "check_kinds": list(cleaned.get("check_kinds") or []),
            "movement_kinds": list(cleaned.get("movement_kinds") or []),
            "supply_point_id": cleaned["supply_point"].pk if cleaned.get("supply_point") else None,
            "commodity_slug": cleaned["commodity"].slug if cleaned.get("commodity") else None,
            "partner_org_id": cleaned["partner"].pk if cleaned.get("partner") else None,
            "cadence": cleaned["cadence"],
        }
        data["channel"] = "connect_message" if cleaned.get("recipient") == "connect_message" else "email"
        if cleaned.get("recipient") == "keep":
            data.pop("channel")  # the recipient (and how it is reached) is left as it is
        elif cleaned.get("recipient") == "connect_message":
            data["recipient_connect_username"] = cleaned["recipient_connect_username"].strip()
            data["recipient_name"] = (cleaned.get("recipient_name") or "").strip()
        elif cleaned.get("recipient") == "me":
            data["recipient_user_id"] = self.access.user.pk
        else:
            data["recipient_email"] = cleaned["recipient_email"]
            data["recipient_name"] = (cleaned.get("recipient_name") or "").strip()
        if "active" in self.fields:
            data["active"] = bool(cleaned.get("active"))
        if self.subscription is None:
            # Nothing to clear on a new subscription, and the operation's own
            # defaults read better than an explicit null.
            data = {key: value for key, value in data.items() if value is not None}
        return data

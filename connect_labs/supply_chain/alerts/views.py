"""Alerts in a browser: who is told about what, and what they were told.

One page lists the subscriptions and the sent log side by side, because the
question a person arrives with is usually "was the donor told?", and the answer
is a row in the log next to the subscription that produced it.
"""

from django.http import Http404
from django.urls import reverse
from django.utils.dateparse import parse_datetime

from connect_labs.supply_chain.alerts.forms import AlertSubscriptionForm
from connect_labs.supply_chain.alerts.models import AlertSubscription
from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.form_views import OperationActionView, OperationFormView
from connect_labs.supply_chain.views import OperationBase


class AlertListView(OperationBase):
    template_name = "supply_chain/alerts.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if context["has_program_context"]:
            context["subscriptions"] = self.op("alert_subscription_list")
            context["log"] = self.op("alert_log_list", limit=50)
            names = {s["id"]: s["label"] or f"Alert {s['id']}" for s in context["subscriptions"]}
            for notice in context["log"]:
                notice["subscription_name"] = names.get(notice["subscription_id"], "")
                notice["detected"] = parse_datetime(notice["detected_at"])
        return context


class _AlertScreen(OperationFormView):
    form_class = AlertSubscriptionForm

    def breadcrumb(self, **kwargs):
        return [{"label": "Alerts", "href": reverse("supply_chain:alerts")}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:alerts")

    def redirect_to(self, result):
        return reverse("supply_chain:alerts")


class AlertCreateView(_AlertScreen):
    operation = "alert_subscription_create"
    title = "New alert"
    intro = (
        "Email someone when a check becomes true — stock below its minimum, a shipment overdue — or when "
        "stock moves. Each email states what is now true and links to the record. It carries no advice "
        "and ranks nothing."
    )
    submit_label = "Create alert"


class AlertUpdateView(_AlertScreen):
    operation = "alert_subscription_update"
    title = "Edit alert"
    submit_label = "Save changes"

    def subscription(self):
        found = (
            AlertSubscription.objects.filter(
                pk=self.kwargs["subscription_id"], program_id=_access(self.request).program_id
            )
            .select_related("supply_point", "commodity", "recipient_user")
            .first()
        )
        if found is None:
            raise Http404(f"no alert {self.kwargs['subscription_id']} in this programme")
        return found

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["subscription"] = self.subscription()
        return kwargs

    def fixed(self, **kwargs):
        return {"subscription_id": int(kwargs["subscription_id"])}


class AlertDeleteView(OperationActionView):
    operation = "alert_subscription_delete"
    success_message = "Alert deleted. Nothing more will be sent for it."

    def fixed(self, **kwargs):
        return {"subscription_id": int(kwargs["subscription_id"])}

    def redirect_to(self, **kwargs):
        return reverse("supply_chain:alerts")

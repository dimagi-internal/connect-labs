"""Alerts in a browser: who is told about what, and what they were told.

One page lists the subscriptions and the sent log side by side, because the
question a person arrives with is usually "was the donor told?", and the answer
is a row in the log next to the subscription that produced it.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import dateformat, timezone
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.views import View

from connect_labs.supply_chain.alerts.forms import AlertSubscriptionForm
from connect_labs.supply_chain.alerts.models import AlertSubscription
from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.form_views import OperationActionView, OperationFormView
from connect_labs.supply_chain.templatetags.supply_chain_extras import check_readout
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
            recipients = {s["id"]: s["recipient"] for s in context["subscriptions"]}
            for notice in context["log"]:
                notice["subscription_name"] = names.get(notice["subscription_id"], "")
                # Who it went to, by name where the alert has one; the address
                # it was sent to stays beside it, because that is what was used.
                notice["sent_to_name"] = recipients.get(notice["subscription_id"]) or notice["sent_to"]
                # The figure that made it true, as the checks page says it: "2.8 months
                # of stock · minimum 3". A log row reading only "Below its own minimum"
                # did not say how far below (the CHC render).
                if notice["kind"] == "check":
                    notice["readout"] = check_readout({"kind": notice["subject_kind"], "facts": notice["facts"]})
                notice["detected"] = parse_datetime(notice["detected_at"])
        return context


@method_decorator(login_required, name="dispatch")
class AlertCheckNowView(View):
    """Evaluate this programme's subscriptions now, rather than at the next beat.

    The same pass the five-minute task runs (service.run_alerts), narrowed to
    the programme in context: new checks only, logged and delivered exactly as
    beat would. The answer is said on the page, including "nothing new".
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        from connect_labs.supply_chain.alerts import service

        if not has_program_context(request):
            return redirect("supply_chain:alerts")
        result = service.run_alerts(program_id=_access(request).program_id)
        if result.get("skipped"):
            messages.info(request, "A check is already running. Its results will appear in the log below.")
        else:
            found = result["new_checks"] + result["new_movements"]
            if not result["subscriptions"]:
                messages.info(request, "Nothing to check: no active alerts in this programme.")
            elif found:
                sent = (
                    f"; {result['emails']} email{'s' if result['emails'] != 1 else ''} sent"
                    if result["emails"]
                    else ""
                )
                messages.success(
                    request,
                    f"Checked now: {found} new notice{'s' if found != 1 else ''}{sent}. They are in the log below.",
                )
            else:
                messages.info(request, _nothing_new(service.found_so_far(_access(request).program_id)))
        return redirect("supply_chain:alerts")


# How a notice left, in the log's own words for it (alerts.html, Delivery).
_NOT_SENT = {
    "pending": "in the next digest",
    "email_disabled": "not sent — email is off",
    "no_address": "not sent — no address",
}


def _nothing_new(found) -> str:
    """ "Nothing new", with what is already true: the notices these alerts found before.

    "Nothing new since the last check" sat directly above the log rows it had
    found, and read as a contradiction. This says how many there are and how
    each left -- sent, and when, or not sent and why -- in the log's words. It
    never claims a notice was sent that the log records as not sent.
    """
    total = found["total"]
    if not total:
        return "Checked now: nothing new — these alerts have not found anything yet."
    by_delivery = found["by_delivery"]
    latest = found["latest_sent_at"]
    when = dateformat.format(timezone.localtime(latest), "j M Y, H:i") if latest else ""
    sent = by_delivery.get("queued", 0)
    if sent == total:
        if total == 1:
            return f"Checked now: nothing new — the notice these alerts found was already sent ({when})."
        return f"Checked now: nothing new — the {total} notices these alerts found were already sent (latest {when})."
    parts = [f"{sent} sent (latest {when})"] if sent else []
    parts += [f"{by_delivery[status]} {words}" for status, words in _NOT_SENT.items() if by_delivery.get(status)]
    noun = "notice" if total == 1 else "notices"
    return f"Checked now: nothing new — these alerts have found {total} {noun} before: {'; '.join(parts)}."


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

"""The outbox: drafts to finish and send, and what was sent.

Sending happens here and nowhere else (`service.send`): a person, signed in,
on the draft's page.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView

from connect_labs.communications import kinds as kind_registry
from connect_labs.communications import service
from connect_labs.communications.models import DRAFT, SENT, Communication
from connect_labs.solicitations.access import caller_for


class OutboxPage(LoginRequiredMixin, TemplateView):
    login_url = "/labs/login/"

    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except service.NotFound as e:
            raise Http404("Not found") from e
        except PermissionDenied as exc:
            from connect_labs.solicitations.views import forbidden

            return forbidden(request, str(exc) or None)


def _about_filter(request):
    """(about record or None, queryset filter) from ?kind=&about=."""
    kind_key, about_id = request.GET.get("kind", ""), request.GET.get("about", "")
    if not kind_key or not about_id:
        return None, None, {}
    try:
        kind = kind_registry.get(kind_key)
    except ValidationError as e:
        raise Http404("Not found") from e
    about = kind.resolve(about_id) if kind.about_model else None
    if about is None:
        raise Http404("Not found")
    ct = ContentType.objects.get_for_model(about)
    return kind, about, {"about_type": ct, "about_id": str(about.pk)}


class OutboxView(OutboxPage):
    template_name = "communications/outbox.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        kind, about, where = _about_filter(self.request)
        caller = caller_for(self.request)
        rows = service.visible(caller, Communication.objects.filter(**where).exclude(status="discarded"))
        sent_messages = []
        if about is not None:
            if not kind.can_prepare(caller, about):
                raise Http404("Not found")
            sent_messages = service.sent_about(caller, about)
        ctx.update(
            {
                "drafts": [c for c in rows if c.status == DRAFT],
                "sent": [c for c in rows if c.status == SENT],
                "kind": kind,
                "about": about,
                "about_label": kind.about_label(about) if kind else "",
                "about_url": kind.about_url(about) if kind else "",
                "can_start": bool(kind and about is not None and kind.can_prepare(caller, about)),
                "sent_messages": sent_messages,
            }
        )
        return ctx

    def post(self, request, *args, **kwargs):
        """Start a draft of ?kind about ?about, as the kind writes it -- or "Check delivery" on its messages."""
        kind, about, _ = _about_filter(request)
        if kind is None:
            raise Http404("Not found")
        if request.POST.get("action") == "check_delivery":
            _report_check(request, service.check_delivery_about(request, kind, about))
            return redirect(request.get_full_path())
        communication = service.create_draft(request, kind.key, about_id=about.pk)
        return redirect("communications:detail", pk=communication.pk)


def _report_check(request, result) -> None:
    parts = []
    if result["unknown"]:
        parts.append(f"{result['unknown']} with no word back marked outcome unknown")
    if result["followups_retried"]:
        parts.append(f"{result['followups_done']} of {result['followups_retried']} follow-ups done")
    messages.success(request, "Checked delivery: " + ("; ".join(parts) if parts else "nothing to settle") + ".")


class CommunicationView(OutboxPage):
    template_name = "communications/detail.html"

    def _context(self, communication, kind, about, *, errors=None, preview_id=None):
        caller = caller_for(self.request)
        recipients = list(communication.recipients.select_related("org"))
        chosen_preview = None
        if recipients:
            chosen_preview = service.preview(caller, communication.pk, recipient_id=preview_id)
        return self.get_context_data(
            communication=communication,
            kind=kind,
            about=about,
            about_label=kind.about_label(about),
            about_url=kind.about_url(about),
            recipients=recipients,
            included_count=sum(1 for r in recipients if r.included),
            preview=chosen_preview,
            # What stops it being sent, then what the last attempt said: one dict, so
            # the template never resolves a missing key as a filter argument.
            errors={**(service.problems(communication, kind) if communication.is_draft else {}), **(errors or {})},
            can_send=communication.is_draft and kind.can_send(caller, about, communication),
            emails_editable=kind.emails_editable,
            can_settle=kind.can_send(caller, about, communication),
            placeholders=kind.placeholders,
            per_recipient=any(r.body for r in recipients),
            sent_messages=list(communication.sent_messages.select_related("org", "sent_by")),
            outbox_url=(
                f"{reverse('communications:outbox')}?kind={kind.key}&about={about.pk}"
                if about is not None and kind.about_model
                else reverse("communications:outbox")
            ),
        )

    def get(self, request, *args, **kwargs):
        communication, kind, about = service.load(request, kwargs["pk"])
        preview_id = request.GET.get("preview")
        ctx = self._context(
            communication, kind, about, preview_id=int(preview_id) if str(preview_id).isdigit() else None
        )
        return self.render_to_response(ctx)

    def post(self, request, *args, **kwargs):
        pk = kwargs["pk"]
        action = request.POST.get("action", "save")
        try:
            if action == "discard":
                service.discard(request, pk)
                messages.success(request, "Draft discarded.")
                return redirect("communications:outbox")
            if action == "check_delivery":
                _report_check(request, service.check_delivery(request, pk))
                return redirect("communications:detail", pk=pk)
            if action in ("confirm_delivered", "retry_followup"):
                message_id = request.POST.get("message", "")
                if not message_id.isdigit():
                    raise Http404("Not found")
                if action == "confirm_delivered":
                    service.confirm_delivered(request, int(message_id))
                    messages.success(request, "Marked sent.")
                elif service.retry_followup(request, int(message_id)):
                    messages.success(request, "Follow-up done.")
                else:
                    messages.error(request, "The follow-up failed again; the reason is shown below.")
                return redirect("communications:detail", pk=pk)
            communication, kind, about = service.load(request, pk)
            if communication.is_draft:
                data = {k: request.POST[k] for k in ("title", "subject", "body", "reply_to") if k in request.POST}
                data["included"] = [i for i in request.POST.getlist("include") if i.isdigit()]
                if kind.emails_editable:
                    data["emails"] = {
                        r.pk: request.POST[f"email_{r.pk}"]
                        for r in communication.recipients.all()
                        if f"email_{r.pk}" in request.POST
                    }
                service.update_draft(request, pk, data)
            if action == "send":
                result = service.send(request, pk)
                count = result["queued"]
                messages.success(request, f"Sent {count} email{'s' if count != 1 else ''}.")
            else:
                messages.success(request, "Draft saved.")
        except ValidationError as e:
            communication, kind, about = service.load(request, pk)
            errs = e.message_dict if hasattr(e, "message_dict") else {"__all__": e.messages}
            ctx = self._context(
                communication,
                kind,
                about,
                errors={("general" if k == "__all__" else k): " ".join(v) for k, v in errs.items()},
            )
            response = self.render_to_response(ctx)
            response.status_code = 400
            return response
        return redirect("communications:detail", pk=pk)

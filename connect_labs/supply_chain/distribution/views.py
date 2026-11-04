"""The last two write screens: a resupply run, and correcting a quote.

With these, every operation the supply domain exposes has a screen.
"""

import base64
from decimal import Decimal, InvalidOperation

import jsonschema
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View

from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.banner import program_line
from connect_labs.supply_chain.distribution.forms import (
    DistributionForm,
    DistributionLineFormSet,
    DistributionPlanForm,
    DistributionPlanLineFormSet,
)
from connect_labs.supply_chain.form_views import OperationActionView, OperationFormView, SupplyWriteMixin
from connect_labs.supply_chain.models import Distribution, DistributionLine, Item, Quote, SupplyPoint
from connect_labs.supply_chain.navigation import supply_tabs
from connect_labs.supply_chain.procurement.forms import QuoteCorrectionForm


class DistributionRecordView(OperationFormView):
    """One resupply run out to field workers.

    A batch header over many movements: each line posts one, so the ledger has
    no special case for distribution and a worker's balance is a balance like
    any other. That is what the `kind="user_held"` ruling buys.
    """

    operation = "distribution_record"
    form_class = DistributionForm
    template_name = "supply_chain/distribution_form.html"
    title = "Record a resupply run"
    intro = (
        "One store, one day, one product, and a line per worker. Each line posts a movement, so "
        "the store's balance goes down and each worker's goes up — the same ledger as a transfer "
        "between stores."
    )
    submit_label = "Record the run"
    empty_message = "A run has to say who it went to."

    def workers(self):
        return SupplyPoint.objects.filter(program_id=_access(self.request).program_id, kind="user_held").order_by(
            "name"
        )

    def items(self):
        return Item.objects.filter(scope_key=_access(self.request).scope_key).order_by("name")

    def line_formset(self, data=None):
        return DistributionLineFormSet(
            data,
            prefix="lines",
            form_kwargs={"workers": self.workers(), "items": self.items()},
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if context.get("has_program_context") and "lines" not in context:
            context["lines"] = self.line_formset(self.request.POST if self.request.method == "POST" else None)
            # Said on the page rather than discovered by an empty picker: a
            # programme with no user-held points has nobody to distribute to,
            # and the fix is on a different screen.
            context["no_workers"] = not self.workers().exists()
        return context

    def form_valid(self, form):
        lines = self.line_formset(self.request.POST)
        if not lines.is_valid():
            return self.render_to_response(self.get_context_data(form=form, lines=lines))

        kept = []
        for row in lines.cleaned_data:
            if not row or row.get("DELETE") or row.get("quantity") is None:
                continue
            line = {
                "to_supply_point_id": row["to_supply_point"].pk,
                "quantity": str(row["quantity"]),
                "quantity_unit": row["quantity_unit"],
            }
            if row.get("item"):
                line["item_id"] = row["item"].pk
            if row.get("batch"):
                line["batch"] = row["batch"]
            kept.append(line)

        if not kept:
            form.add_error(None, self.empty_message)
            return self.render_to_response(self.get_context_data(form=form, lines=lines))

        self._lines = kept
        return super().form_valid(form)

    def fixed(self, **kwargs):
        return {"data": {"lines": getattr(self, "_lines", [])}}

    def breadcrumb(self, **kwargs):
        return [{"label": "Distribution", "href": reverse("supply_chain:distribution")}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:distribution")

    def redirect_to(self, result):
        return reverse("supply_chain:distribution")


class QuoteCorrectView(OperationFormView):
    """A corrected version of a quote, superseding the one it came from."""

    operation = "quote_correct"
    form_class = QuoteCorrectionForm
    title = "Correct a quote"
    intro = (
        "This writes a new version rather than editing this one. The original stays readable, so a "
        "comparison run last month still reproduces the numbers it showed then."
    )
    submit_label = "Save the correction"
    footnote = (
        "The old version stays on the supplier's record, marked as superseded, with your reason "
        "attached to the one that replaced it."
    )

    def quote(self):
        found = Quote.objects.filter(
            pk=self.kwargs["quote_id"], tender__program_id=_access(self.request).program_id
        ).first()
        if found is None:
            raise Http404(f"no quote {self.kwargs['quote_id']} in this programme")
        return found

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # Opening on the existing figures: a correction is usually one wrong
        # number among fifteen right ones, and retyping the other fourteen is
        # how a second mistake gets in.
        kwargs["instance"] = self.quote()
        return kwargs

    def form_valid(self, form):
        # `reason` is a top-level argument of `quote_correct`, describing the
        # correction rather than the offer, so it is lifted out of the data
        # here — the form deliberately does not know the operation's shape.
        self._reason = form.cleaned_data["reason"]
        return super().form_valid(form)

    def fixed(self, **kwargs):
        return {"quote_id": int(kwargs["quote_id"]), "reason": getattr(self, "_reason", "")}

    def breadcrumb(self, **kwargs):
        quote = self.quote()
        return [
            {"label": "Sourcing", "href": reverse("supply_chain:procurement_tender_board")},
            {
                "label": f"Tender {quote.tender_id}",
                "href": reverse("supply_chain:procurement_tender_detail", args=[quote.tender_id]),
            },
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:procurement_quote_detail", args=[kwargs["quote_id"]])

    def redirect_to(self, result):
        return reverse("supply_chain:procurement_quote_detail", args=[result["id"]])


class DistributionPlanView(DistributionRecordView):
    """A handout run before it happens: the Monday list, from the forecast or typed in."""

    operation = "distribution_plan"
    form_class = DistributionPlanForm
    title = "Plan a handout"
    intro = (
        "One store, one handout day, one product. Tick the box to fill the list from what the forecast says each "
        "field worker needs this week, or type the list. Nothing leaves the store until you confirm what was "
        "actually given: the planned amounts only count as promised until then."
    )
    submit_label = "Save the plan"
    empty_message = "Tick the box to fill the list from the forecast, or add at least one row."

    def workers(self):
        # A handout can go to a facility as well as to a field worker.
        return SupplyPoint.objects.filter(program_id=_access(self.request).program_id, status="active").order_by(
            "name"
        )

    def line_formset(self, data=None):
        return DistributionPlanLineFormSet(
            data,
            prefix="lines",
            form_kwargs={"workers": self.workers(), "items": self.items()},
        )

    def form_valid(self, form):
        lines = self.line_formset(self.request.POST)
        if not lines.is_valid():
            return self.render_to_response(self.get_context_data(form=form, lines=lines))
        kept = []
        for row in lines.cleaned_data:
            if not row or row.get("DELETE") or row.get("quantity") is None:
                continue
            line = {
                "to_supply_point_id": row["to_supply_point"].pk,
                "quantity": str(row["quantity"]),
                "quantity_unit": row["quantity_unit"],
            }
            if row.get("item"):
                line["item_id"] = row["item"].pk
            if row.get("batch"):
                line["batch"] = row["batch"]
            kept.append(line)
        if not kept and not form.cleaned_data.get("from_forecast"):
            form.add_error(None, self.empty_message)
            return self.render_to_response(self.get_context_data(form=form, lines=lines))
        self._lines = kept
        return OperationFormView.form_valid(self, form)

    def fixed(self, **kwargs):
        return {"data": {"lines": getattr(self, "_lines", [])}}


def _run(request, distribution_id):
    found = Distribution.objects.filter(program_id=_access(request).program_id, pk=distribution_id).first()
    if found is None:
        raise Http404(f"no handout {distribution_id} in this programme")
    return found


class DistributionConfirmAsPlannedView(OperationActionView):
    """One button: what was planned is what was given. The prepared list, confirmed."""

    operation = "distribution_confirm"
    success_message = "Confirmed as planned. The stock has moved."

    def fixed(self, **kwargs):
        run = _run(self.request, kwargs["distribution_id"])
        lines = [
            {"line_id": line.pk, "quantity_given": str(line.quantity)}
            for line in run.lines.all()
            if not line.is_confirmed
        ]
        return {
            "distribution_id": run.pk,
            "data": {"lines": lines, "source": self.request.POST.get("source") or "we_recorded"},
        }

    def redirect_to(self, **kwargs):
        return reverse("supply_chain:distribution")


class DistributionReceivedView(OperationActionView):
    """The person it went to says how much reached them."""

    operation = "distribution_receipt_confirm"
    success_message = "Thank you: what reached them is now on the record beside what was given."

    def fixed(self, **kwargs):
        line = DistributionLine.objects.filter(
            distribution__program_id=_access(self.request).program_id, pk=kwargs["line_id"]
        ).first()
        if line is None:
            raise Http404("no such handout line")
        return {
            "line_id": line.pk,
            "data": {
                "quantity_received": self.request.POST.get("quantity_received", ""),
                **({"received_by": self.request.POST["received_by"]} if self.request.POST.get("received_by") else {}),
            },
        }

    def redirect_to(self, **kwargs):
        return reverse("supply_chain:distribution")


@method_decorator(login_required, name="dispatch")
class DistributionFromMessageView(SupplyWriteMixin, View):
    """Record what was given from a pasted message or a photo of a list, matched against the plan.

    Two steps on one page. Step one takes the message (and/or a photo, kept as evidence at once);
    step two shows each planned place beside what the message says, with the difference, for a
    person to correct and confirm. Nothing is posted until the second step.
    """

    template_name = "supply_chain/distribution_message.html"
    MAX_PHOTO_BYTES = 8 * 1024 * 1024

    def _context(self, request, run, **extra):
        planned = [line for line in run.lines.select_related("to_supply_point") if not line.is_confirmed]
        places = SupplyPoint.objects.filter(program_id=run.program_id, status="active").order_by("name")
        return {
            "supply_tabs": supply_tabs(request),
            "supply_program_line": program_line(request),
            "has_program_context": True,
            "run": run,
            "planned": planned,
            "places": places,
            "documents": run.documents.all(),
            **extra,
        }

    def get(self, request, distribution_id):
        if not has_program_context(request):
            raise Http404("no programme selected")
        run = _run(request, distribution_id)
        return render(request, self.template_name, self._context(request, run, step="read"))

    def post(self, request, distribution_id):
        if not has_program_context(request):
            raise Http404("no programme selected")
        run = _run(request, distribution_id)
        if request.POST.get("step") == "confirm":
            return self._confirm(request, run)
        return self._read(request, run)

    # ---- step one: read the message, keep the photo --------------------------

    def _read(self, request, run):
        text = (request.POST.get("text") or "").strip()
        photo = request.FILES.get("photo")
        if not text and not photo:
            messages.error(request, "Paste the message, or choose a photo of the list.")
            return redirect(request.path)
        if photo is not None:
            if photo.size > self.MAX_PHOTO_BYTES:
                messages.error(request, "That file is over 8 MB. Send a smaller photo.")
                return redirect(request.path)
            try:
                self.op(
                    "document_attach",
                    data={
                        "kind": "photo" if (photo.content_type or "").startswith("image/") else "other",
                        "title": "Photo or file of the handout list",
                        "filename": photo.name,
                        "content_type": photo.content_type or "application/octet-stream",
                        "content_base64": base64.b64encode(photo.read()).decode("ascii"),
                        "distribution_id": run.pk,
                        "source": request.POST.get("source") or "partner_reported",
                    },
                )
            except (jsonschema.ValidationError, ValueError) as exc:
                messages.error(request, getattr(exc, "message", str(exc)))
                return redirect(request.path)
        proposal = None
        if text:
            proposal = self.op("distribution_message_match", distribution_id=run.pk, text=text)
        by_id = {row["line_id"]: row for row in (proposal or {}).get("lines", [])}
        rows = []
        for line in run.lines.select_related("to_supply_point"):
            if line.is_confirmed:
                continue
            found = by_id.get(line.pk) or {}
            rows.append({"line": line, **found})
        return render(
            request,
            self.template_name,
            self._context(
                request,
                _run(request, run.pk),
                step="review",
                text=text,
                rows=rows,
                extras=(proposal or {}).get("extras", []),
                unread=(proposal or {}).get("unread", []),
                source=request.POST.get("source") or "partner_reported",
                photo_only=not text,
            ),
        )

    # ---- step two: confirm what the person checked -----------------------------

    def _confirm(self, request, run):
        lines, problems = [], []
        for line in run.lines.all():
            if line.is_confirmed:
                continue
            raw = (request.POST.get(f"given_{line.pk}") or "").strip()
            if not raw:
                continue  # left blank: stays planned
            try:
                given = Decimal(raw)
                if given < 0:
                    raise InvalidOperation
            except InvalidOperation:
                problems.append(f"{line.to_supply_point.name}: “{raw}” is not a quantity.")
                continue
            lines.append({"line_id": line.pk, "quantity_given": str(given)})
        for key in request.POST:
            if not key.startswith("extra_place_"):
                continue
            number = key.removeprefix("extra_place_")
            place = (request.POST.get(key) or "").strip()
            raw = (request.POST.get(f"extra_quantity_{number}") or "").strip()
            if not place:
                continue  # left on "ignore"
            try:
                quantity = Decimal(raw)
            except InvalidOperation:
                problems.append(f"“{raw}” is not a quantity.")
                continue
            planned = run.lines.first()
            lines.append(
                {
                    "to_supply_point_id": int(place),
                    "quantity_given": str(quantity),
                    "quantity_unit": request.POST.get(f"extra_unit_{number}")
                    or (planned.quantity_unit if planned else ""),
                }
            )
        if problems:
            for problem in problems:
                messages.error(request, problem)
            return redirect(request.path)
        if not lines:
            messages.error(request, "Nothing to confirm: every quantity was left blank.")
            return redirect(request.path)
        data = {
            "lines": lines,
            "source": request.POST.get("source") or "partner_reported",
            "message_text": request.POST.get("text") or "",
        }
        try:
            result = self.op("distribution_confirm", distribution_id=run.pk, data=data)
        except (jsonschema.ValidationError, ValueError) as exc:
            messages.error(request, getattr(exc, "message", str(exc)))
            return redirect(request.path)
        left = sum(1 for line in result["lines"] if line["quantity_given"] is None)
        messages.success(
            request,
            "Recorded, and the message is kept with the handout."
            + (f" {left} place{'s' if left != 1 else ''} still waiting to be confirmed." if left else ""),
        )
        return redirect(reverse("supply_chain:distribution"))

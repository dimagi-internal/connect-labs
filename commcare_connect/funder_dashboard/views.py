import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404
from django.shortcuts import redirect
from django.views.generic import TemplateView

from commcare_connect.funder_dashboard.data_access import FunderDashboardDataAccess
from commcare_connect.funder_dashboard.forms import FundForm

logger = logging.getLogger(__name__)


class LabsLoginRequiredMixin(LoginRequiredMixin):
    login_url = "/labs/login/"


class ManagerRequiredMixin(LabsLoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated


def _has_org_context(request):
    labs_context = getattr(request, "labs_context", {})
    return bool(labs_context.get("organization_id"))


def _get_data_access(request):
    return FunderDashboardDataAccess(request=request)


class PortfolioDashboardView(ManagerRequiredMixin, TemplateView):
    template_name = "funder_dashboard/portfolio.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["has_context"] = _has_org_context(self.request)
        if not ctx["has_context"]:
            ctx["funds"] = []
            return ctx
        try:
            da = _get_data_access(self.request)
            ctx["funds"] = da.get_funds()
        except Exception:
            logger.exception("Failed to load funds for portfolio")
            ctx["funds"] = []
        return ctx


class FundDetailView(ManagerRequiredMixin, TemplateView):
    template_name = "funder_dashboard/fund_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = kwargs["pk"]
        try:
            da = _get_data_access(self.request)
            fund = da.get_fund_by_id(pk)
            if not fund:
                raise Http404("Fund not found")
            ctx["fund"] = fund
        except Http404:
            raise
        except Exception:
            logger.exception("Failed to load fund %s", pk)
            raise Http404("Fund not found")
        return ctx


class FundCreateView(ManagerRequiredMixin, TemplateView):
    template_name = "funder_dashboard/fund_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["has_context"] = _has_org_context(self.request)
        ctx["form"] = FundForm()
        ctx["is_create"] = True
        return ctx

    def post(self, request, *args, **kwargs):
        if not _has_org_context(request):
            ctx = self.get_context_data(**kwargs)
            ctx["error"] = "Please select an organization from the context selector."
            return self.render_to_response(ctx)

        form = FundForm(request.POST)
        if form.is_valid():
            data = form.to_data_dict()
            data["org_id"] = str(request.labs_context.get("organization_id", ""))
            try:
                da = _get_data_access(request)
                da.create_fund(data)
                return redirect("funder_dashboard:portfolio")
            except Exception:
                logger.exception("Failed to create fund")
                ctx = self.get_context_data(**kwargs)
                ctx["form"] = form
                ctx["error"] = "Failed to create fund. Please try again."
                return self.render_to_response(ctx)
        else:
            ctx = self.get_context_data(**kwargs)
            ctx["form"] = form
            return self.render_to_response(ctx)


class FundEditView(ManagerRequiredMixin, TemplateView):
    template_name = "funder_dashboard/fund_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["has_context"] = _has_org_context(self.request)
        pk = kwargs["pk"]
        try:
            da = _get_data_access(self.request)
            fund = da.get_fund_by_id(pk)
            if not fund:
                raise Http404("Fund not found")
            ctx["fund"] = fund
            initial = {
                "name": fund.name,
                "description": fund.description,
                "total_budget": fund.total_budget,
                "currency": fund.currency,
                "status": fund.status,
                "program_ids_json": json.dumps(fund.program_ids),
                "delivery_types_json": json.dumps(fund.delivery_types),
            }
            ctx["form"] = FundForm(initial=initial)
            ctx["is_create"] = False
        except Http404:
            raise
        except Exception:
            logger.exception("Failed to load fund %s for editing", pk)
            raise Http404("Fund not found")
        return ctx

    def post(self, request, *args, **kwargs):
        pk = kwargs["pk"]
        form = FundForm(request.POST)
        if form.is_valid():
            data = form.to_data_dict()
            try:
                da = _get_data_access(request)
                da.update_fund(pk, data)
                return redirect("funder_dashboard:portfolio")
            except Exception:
                logger.exception("Failed to update fund %s", pk)
                ctx = self.get_context_data(**kwargs)
                ctx["form"] = form
                ctx["error"] = "Failed to update fund. Please try again."
                return self.render_to_response(ctx)
        else:
            ctx = self.get_context_data(**kwargs)
            ctx["form"] = form
            return self.render_to_response(ctx)

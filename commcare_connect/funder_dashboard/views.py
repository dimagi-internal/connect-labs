import json
import logging
from collections.abc import Generator

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404
from django.shortcuts import redirect
from django.views.generic import TemplateView

from commcare_connect.funder_dashboard.data_access import FunderDashboardDataAccess
from commcare_connect.funder_dashboard.forms import FundForm
from commcare_connect.labs.analysis.sse_streaming import BaseSSEStreamView, send_sse_event

logger = logging.getLogger(__name__)


class LabsLoginRequiredMixin(LoginRequiredMixin):
    login_url = "/labs/login/"


class ManagerRequiredMixin(LabsLoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated


def _has_org_context(request):
    labs_context = getattr(request, "labs_context", {})
    return bool(labs_context.get("program_id") or labs_context.get("organization_id"))


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
            funds = da.get_funds()
            ctx["funds"] = funds
            ctx["active_count"] = sum(1 for f in funds if f.status == "active")
            ctx["total_programs"] = sum(len(f.program_ids) for f in funds)

            # Fetch solicitation counts per fund
            try:
                from commcare_connect.solicitations.data_access import SolicitationsDataAccess

                sol_da = SolicitationsDataAccess(request=self.request)
                sol_counts = {}
                for fund in funds:
                    try:
                        sols = sol_da.get_solicitations_by_fund_id(fund.pk)
                        sol_counts[fund.pk] = len(sols)
                    except Exception:
                        sol_counts[fund.pk] = 0
                ctx["solicitation_counts"] = sol_counts
            except Exception:
                logger.debug("Could not load solicitation counts for portfolio")
                ctx["solicitation_counts"] = {}
        except Exception:
            logger.exception("Failed to load funds for portfolio")
            ctx["funds"] = []
            ctx["active_count"] = 0
            ctx["total_programs"] = 0
            ctx["solicitation_counts"] = {}
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
            labs_context = getattr(self.request, "labs_context", {})
            org_slug = labs_context.get("organization_slug", "")
            ctx["connect_opp_base_url"] = (
                f"{settings.CONNECT_PRODUCTION_URL}/a/{org_slug}/opportunity" if org_slug else ""
            )
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
                "allocations_json": json.dumps(fund.allocations),
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


class FundPipelineDataView(BaseSSEStreamView):
    """SSE endpoint that streams visit pipeline + completed_works data for a fund's allocations."""

    login_url = "/labs/login/"

    def stream_data(self, request) -> Generator[str, None, None]:
        """Stream pipeline data for each allocation with an opportunity_id."""
        pk = self.kwargs["pk"]

        try:
            # Check OAuth token
            labs_oauth = request.session.get("labs_oauth", {})
            access_token = labs_oauth.get("access_token")
            if not access_token:
                yield send_sse_event("Error", error="No OAuth token found. Please log in to Connect.")
                return

            # Load the fund and its allocations
            da = _get_data_access(request)
            fund = da.get_fund_by_id(pk)
            if not fund:
                yield send_sse_event("Error", error=f"Fund {pk} not found")
                return

            allocations = fund.allocations
            opp_allocations = [a for a in allocations if a.get("opportunity_id")]
            if not opp_allocations:
                yield send_sse_event("No allocations with opportunity IDs", data={"visits": [], "payments": []})
                return

            yield send_sse_event(f"Processing {len(opp_allocations)} allocations...")

            all_visits = []
            all_payments = []

            for alloc in opp_allocations:
                opp_id = int(alloc["opportunity_id"])
                opp_name = alloc.get("opportunity_name", f"Opportunity {opp_id}")
                country = alloc.get("country", "")
                delivery_type = alloc.get("delivery_type", "")

                # Fetch user_visits CSV directly
                yield send_sse_event(f"Loading visits for {opp_name}...")
                try:
                    visits_csv = da.fetch_user_visits(opp_id)
                    opp_visits = []
                    for v in visits_csv:
                        opp_visits.append(
                            {
                                "visit_date": v.get("visit_date", ""),
                                "username": v.get("username", ""),
                                "entity_name": v.get("entity_name", ""),
                                "status": v.get("status", ""),
                                "location": v.get("location", ""),
                                "opp_id": opp_id,
                                "opp_name": opp_name,
                                "country": country,
                                "delivery_type": delivery_type,
                            }
                        )
                    all_visits.extend(opp_visits)
                    yield send_sse_event(f"Got {len(opp_visits)} visits for {opp_name}")
                except Exception as e:
                    logger.warning("Failed to load visits for opp %s: %s", opp_id, e)
                    yield send_sse_event(f"Skipping visits for {opp_name}: {e}")

                # Fetch completed_works CSV for payment data
                try:
                    yield send_sse_event(f"Fetching payments for {opp_name}...")
                    completed_works = da.fetch_completed_works(opp_id)

                    # Filter to approved rows only
                    approved_works = [w for w in completed_works if w.get("status") == "approved"]

                    opp_payments = []
                    for w in approved_works:
                        opp_payments.append(
                            {
                                "status_modified_date": w.get("status_modified_date", ""),
                                "payment_date": w.get("payment_date", ""),
                                "usd_flw": float(w.get("saved_payment_accrued_usd", 0) or 0),
                                "usd_org": float(w.get("saved_org_payment_accrued_usd", 0) or 0),
                                "opp_id": opp_id,
                                "opp_name": opp_name,
                                "country": country,
                                "delivery_type": delivery_type,
                            }
                        )
                    all_payments.extend(opp_payments)

                    yield send_sse_event(f"Got {len(opp_payments)} payments for {opp_name}")
                except Exception as e:
                    logger.warning("Completed works fetch failed for opp %s: %s", opp_id, e)
                    yield send_sse_event(f"Skipping payments for {opp_name}: {e}")

            # Send final SSE event with all data
            yield send_sse_event(
                f"Loaded {len(all_visits)} visits, {len(all_payments)} payments",
                data={"visits": all_visits, "payments": all_payments},
            )

        except Exception as e:
            logger.error("FundPipelineDataView error: %s", e, exc_info=True)
            yield send_sse_event("Error", error=str(e))

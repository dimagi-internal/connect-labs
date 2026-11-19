"""Stock by LLO and This week: the LLO pages of round 3's step 2. The arranging is in `supply_chain.llo_stock`."""

from __future__ import annotations

from datetime import date

from django.http import Http404

from connect_labs.supply_chain import llo_stock
from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.procurement.views import _Base


def _today(request) -> date:
    return getattr(request, "supply_as_of", None) or date.today()


class _LLOPage(_Base):
    def memo(self):
        """One dict for the request, so the plan is worked out once however many figures read it."""
        if not hasattr(self.request, "_llo_memo"):
            self.request._llo_memo = {}
        return self.request._llo_memo

    def visible(self, context):
        """The supply points this viewer may see, or None for all (a partner under read enforcement sees its own)."""
        if not context.get("acl_scoped"):
            return None
        return {p["id"] for p in self.op("supply_point_list")}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        return context


class StockByLLOView(_LLOPage):
    """Every LLO side by side: what it holds, its FLWs, open issues, the next delivery."""

    template_name = "supply_chain/stock_by_llo.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if not context["has_program_context"]:
            return context
        rows = llo_stock.llo_rows(
            _access(self.request).program_id,
            visible=self.visible(context),
            today=_today(self.request),
            memo=self.memo(),
        )
        context.update(rows=rows, roll=llo_stock.roll_up(rows))
        return context


class LLOStockView(_LLOPage):
    """One LLO: its RUTF stock locations and FLWs, what each holds, open issues, deliveries due."""

    template_name = "supply_chain/llo_stock.html"

    def get_context_data(self, org_id, **kwargs):
        context = super().get_context_data(**kwargs)
        if not context["has_program_context"]:
            return context
        detail = llo_stock.llo_detail(
            _access(self.request).program_id,
            org_id,
            visible=self.visible(context),
            today=_today(self.request),
            memo=self.memo(),
        )
        if detail is None:
            raise Http404(f"no LLO {org_id} with anything to hold in this program")
        context["llo"] = detail
        return context


class ThisWeekView(_LLOPage):
    """Every LLO's week side by side: handouts to confirm, FLWs low."""

    template_name = "supply_chain/this_week.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if not context["has_program_context"]:
            return context
        context["rows"] = llo_stock.week_rows(
            _access(self.request).program_id,
            visible=self.visible(context),
            today=_today(self.request),
            memo=self.memo(),
        )
        return context


class LLOWeekView(_LLOPage):
    """One LLO's week: what to hand out, record what was given, confirm it arrived, what to follow up."""

    template_name = "supply_chain/llo_week.html"

    def get_context_data(self, org_id, **kwargs):
        context = super().get_context_data(**kwargs)
        if not context["has_program_context"]:
            return context
        week = llo_stock.llo_week(
            _access(self.request).program_id,
            org_id,
            visible=self.visible(context),
            today=_today(self.request),
            memo=self.memo(),
        )
        if week is None:
            raise Http404(f"no LLO {org_id} with anything to hand out in this program")
        context["llo"] = week
        return context

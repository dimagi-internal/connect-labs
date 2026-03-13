from django.views.generic import TemplateView


class PortfolioDashboardView(TemplateView):
    template_name = "funder_dashboard/portfolio.html"


class FundDetailView(TemplateView):
    template_name = "funder_dashboard/fund_detail.html"


class FundCreateView(TemplateView):
    template_name = "funder_dashboard/fund_form.html"


class FundEditView(TemplateView):
    template_name = "funder_dashboard/fund_form.html"

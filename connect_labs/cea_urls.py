"""The cost-effectiveness explorer, mounted at /labs/cea/.

One explorer, tabs on every page (``templates/components/cea_tabs.html``):

* Health indicators: the baseline explorer, ``/labs/targeting/``.
* ``models/``: run any Connect model, change its inputs, see its chain and its sensitivity
  (``impact.explore``). The landing page.
* ``implementation/``: what delivered opportunities achieved, priced at what they cost
  (``pulse.cea``).
* ``market/``: what it would cost to reach everyone in chosen places, and where the models say it is worth it
  (``scenarios.market``). Malaria PMC, a one-off, is a small link on it (``targeting/pmc/``).

The old addresses of these pages redirect here (see ``pulse/urls.py`` and ``labs/indicators/urls.py``).
"""

from django.urls import path
from django.views.generic import RedirectView

from connect_labs.impact import views as impact_views
from connect_labs.labs.indicators import views as indicator_views
from connect_labs.pulse import views as pulse_views
from connect_labs.scenarios import views as scenario_views

app_name = "cea"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="cea:models", query_string=True, permanent=False), name="index"),
    path("models/", impact_views.ModelExplorerView.as_view(), name="models"),
    path("models/api/run/", impact_views.ModelRunView.as_view(), name="models_run"),
    path("implementation/", pulse_views.PulseCEAView.as_view(), name="implementation"),
    path("implementation/api/view/", pulse_views.PulseCEAViewData.as_view(), name="implementation_view"),
    path("implementation/opp/<int:opp_id>/", pulse_views.PulseCEAOppView.as_view(), name="implementation_opp"),
    path("market/", scenario_views.MarketSizeView.as_view(), name="market"),
    path("market/api/", scenario_views.MarketSizeDataView.as_view(), name="market_data"),
    path(
        "targeting/",
        RedirectView.as_view(pattern_name="cea:pmc", query_string=True, permanent=False),
        name="targeting",
    ),
    path("targeting/pmc/", indicator_views.PmcView.as_view(), name="pmc"),
    path("targeting/pmc/api/", indicator_views.PmcDataView.as_view(), name="pmc_data"),
    path("targeting/pmc/api/rank/", indicator_views.PmcRankView.as_view(), name="pmc_rank"),
    path("targeting/pmc/api/run/", indicator_views.PmcRunView.as_view(), name="pmc_run"),
    path("targeting/pmc/api/run/<int:pk>/", indicator_views.PmcRunStatusView.as_view(), name="pmc_run_status"),
]

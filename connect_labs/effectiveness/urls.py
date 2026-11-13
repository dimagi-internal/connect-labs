"""The cost-effectiveness explorer, mounted at /labs/cea/.

One explorer, tabs on every page (``templates/effectiveness/tabs.html``):

* Health indicators: the baseline explorer, ``/labs/indicators/``.
* ``models/``: run any Connect model, change its inputs, see its chain and its sensitivity
  (``impact.explore``). The landing page.
* ``implementation/``: what delivered opportunities achieved, priced at what they cost
  (``effectiveness.implementation``).
* ``market/``: what it would cost to reach everyone in chosen places, and where the models say it is worth it
  (``scenarios.market``).

The old addresses of these pages redirect here (see ``pulse/urls.py`` and ``labs/indicators/urls.py``).
"""

from django.urls import path
from django.views.generic import RedirectView

from connect_labs.effectiveness import views

app_name = "cea"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="cea:models", query_string=True, permanent=False), name="index"),
    path("models/", views.ModelExplorerView.as_view(), name="models"),
    path("models/api/run/", views.ModelRunView.as_view(), name="models_run"),
    path("implementation/", views.ImplementationView.as_view(), name="implementation"),
    path("implementation/api/view/", views.ImplementationDataView.as_view(), name="implementation_view"),
    path("implementation/opp/<int:opp_id>/", views.ImplementationOppView.as_view(), name="implementation_opp"),
    path("market/", views.MarketSizeView.as_view(), name="market"),
    path("market/api/", views.MarketSizeDataView.as_view(), name="market_data"),
    # Malaria PMC was retired on 13 Nov 2026: a saved link lands on Market size.
    path("targeting/", RedirectView.as_view(pattern_name="cea:market", permanent=False)),
    path("targeting/pmc/", RedirectView.as_view(pattern_name="cea:market", permanent=False)),
]

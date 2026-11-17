"""The effectiveness pages' views: CEA models, Implementations, Market size and the workspace."""

from connect_labs.effectiveness.views.implementation import (
    ImplementationDataView,
    ImplementationOppView,
    ImplementationView,
)
from connect_labs.effectiveness.views.market import MarketPlanCsvView, MarketSizeDataView, MarketSizeView
from connect_labs.effectiveness.views.models import ModelExplorerView, ModelRunView
from connect_labs.effectiveness.views.scenarios import ScenarioDetailView, ScenarioListView
from connect_labs.effectiveness.views.workspace import WorkspaceShapesView, WorkspaceView

__all__ = [
    "ImplementationDataView",
    "ImplementationOppView",
    "ImplementationView",
    "MarketPlanCsvView",
    "MarketSizeDataView",
    "MarketSizeView",
    "ModelExplorerView",
    "ModelRunView",
    "ScenarioDetailView",
    "ScenarioListView",
    "WorkspaceShapesView",
    "WorkspaceView",
]

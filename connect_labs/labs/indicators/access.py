"""Who may open the indicators pages: the gate, in one place for every page and API that reads the baseline."""

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin


class OpenLocallyMixin(LoginRequiredMixin):
    """Login-gated when deployed, open when running locally.

    This surface shows only public open data — WorldPop, DHS, UN IGME,
    geoBoundaries — and nothing specific to the signed-in user, so requiring the
    Connect OAuth round trip to look at it locally buys no protection and costs
    real friction: on a laptop with an expired CLI token the OAuth flow simply
    fails and the page is unreachable.

    Deployments keep the gate, matching every other labs page. If this should be
    public on labs too, drop the mixin rather than widening the exception.
    """

    def dispatch(self, request, *args, **kwargs):
        if settings.DEBUG:
            # Skip LoginRequiredMixin's check, keep the rest of the MRO.
            return super(LoginRequiredMixin, self).dispatch(request, *args, **kwargs)
        return super().dispatch(request, *args, **kwargs)

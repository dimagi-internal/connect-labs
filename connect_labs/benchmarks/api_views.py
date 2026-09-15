from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from connect_labs.benchmarks.data_access import benchmarks_for_opportunity


@login_required
@require_GET
def benchmarks_api(request, opportunity_id: int):
    """This opportunity's benchmarks. Returns an empty set rather than 403 when
    the caller lacks access -- there is nothing to reveal, including whether a
    cohort exists."""
    return JsonResponse(benchmarks_for_opportunity(request, opportunity_id))

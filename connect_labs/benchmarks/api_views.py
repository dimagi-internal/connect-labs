from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET

from connect_labs.benchmarks.data_access import benchmarks_for_opportunity


@require_GET
def benchmarks_api(request, opportunity_id: int):
    """This opportunity's benchmarks. Returns an empty set rather than 403 when
    the caller lacks access -- there is nothing to reveal, including whether a
    cohort exists.

    Anonymous callers get a bare 401, not `@login_required`'s 302 to a login
    page: this endpoint only ever answers JSON, and a redirect to HTML is
    something a fetch() reads as a success with an unparseable body."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    return JsonResponse(benchmarks_for_opportunity(request, opportunity_id))

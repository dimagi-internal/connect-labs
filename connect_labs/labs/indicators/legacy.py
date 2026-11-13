"""The explorer's old address, /labs/targeting/, which now lives at /labs/indicators/.

"Targeting" is what the indicators and the cost-effectiveness models do together (Market size), so it no longer
names the indicators' own pages. Every old address redirects with the query kept, and with a 307 so a
POST stays a POST.
"""

from django.http import HttpResponseRedirect


def moved(request, rest=""):
    target = "/labs/indicators/" + rest
    if request.META.get("QUERY_STRING"):
        target += "?" + request.META["QUERY_STRING"]
    response = HttpResponseRedirect(target)
    response.status_code = 307
    return response

"""Saved audit setups over HTTP: the creation wizard's "Save as setup".

    GET  /audit/api/setups/   {"setups": [{id, name, summary, opportunity_ids, created_by, updated_at}]}
    POST /audit/api/setups/   the wizard's body plus "name" -> {"setup": {...}}

Anyone signed in may list them; saving one is for Dimagi staff, who set up the audits that
reviews and workflows reuse. A setup holds no visit data, only how to audit.
"""

from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from connect_labs.audit import setups
from connect_labs.audit.setup_models import AuditSetup
from connect_labs.utils.dimagi_user import is_dimagi_staff


@login_required
@require_http_methods(["GET", "POST"])
def setups_api(request):
    if request.method == "GET":
        return JsonResponse({"setups": [setups.as_dict(s) for s in AuditSetup.objects.filter(archived=False)]})
    if not is_dimagi_staff(request.user):
        return JsonResponse({"error": "Saving an audit setup is for Dimagi staff."}, status=403)
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "The request body must be JSON."}, status=400)
    try:
        setup = setups.save(body, request.user)
    except setups.SetupRefused as e:
        return JsonResponse({"error": str(e)}, status=400)
    return JsonResponse({"setup": setups.as_dict(setup)}, status=201)

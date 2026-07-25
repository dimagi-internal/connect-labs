from django.http import JsonResponse

from .. import audit
from ..decorators import require_perm
from ..serializers import certification_dict, org_dict
from ..services import org_actions
from .common import handle_action_errors, json_body


@require_perm("org", "view")
@handle_action_errors
def profile(request):
    org = request.supply_actor.org
    if request.method == "POST":
        if not request.supply_actor.role == "supplier":
            return JsonResponse({"error": "forbidden"}, status=403)
        org_actions.update_profile(org, json_body(request))
        audit.log_action(request, "org.profile.update", "SupplierOrg", org.id)
    return JsonResponse({"org": org_dict(org)})


@require_perm("org", "edit")
@handle_action_errors
def certifications(request):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    org = request.supply_actor.org
    cert = org_actions.add_certification(org, json_body(request))
    audit.log_action(request, "org.certification.add", "Certification", cert.id, {"type": cert.cert_type})
    return JsonResponse({"certification": certification_dict(cert)})


@require_perm("org", "edit")
@handle_action_errors
def delete_certification(request, cert_id):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    org = request.supply_actor.org
    if not org_actions.delete_certification(org, cert_id):
        return JsonResponse({"error": "not found"}, status=404)
    audit.log_action(request, "org.certification.delete", "Certification", cert_id)
    return JsonResponse({"ok": True})

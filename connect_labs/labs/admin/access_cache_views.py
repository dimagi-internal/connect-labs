"""Labs Admin › Access cache: make labs ask Connect again everything it remembers
about what one person may access -- their whole labs context.

Labs keeps each person's organisations, programmes and opportunities for an hour
(``oauth._ORG_DATA_CACHE_TTL``), a web session keeps the copy it fetched at login,
and a few short per-person access checks are cached too. When someone is removed
from an organisation in Connect and that cannot wait, an admin clears it here
(``labs.context.forget_labs_context``). Clearing only discards caches -- Connect
still decides what the person may access -- so it is safe for any admin to do.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView

from connect_labs.audit_trail import service as audit_service
from connect_labs.audit_trail.models import Action as AuditAction
from connect_labs.labs.context import forget_labs_context
from connect_labs.labs.view_mixins import AdminRequiredMixin


class AccessCacheView(AdminRequiredMixin, TemplateView):
    template_name = "labs/admin/access_cache.html"

    def post(self, request):
        who = (request.POST.get("who") or "").strip()
        back = redirect(reverse("labs_admin:access_cache"))
        if not who:
            messages.error(request, "Enter a username or email.")
            return back
        users = list(get_user_model().objects.filter(Q(username__iexact=who) | Q(email__iexact=who))[:2])
        if len(users) != 1:
            messages.error(
                request,
                (
                    f"No labs user is {who}."
                    if not users
                    else f"More than one labs user matches {who}; use the username."
                ),
            )
            return back
        user = users[0]
        forget_labs_context(user)
        audit_service.record(
            AuditAction.DELETE,
            resource_type="org_data_cache",
            resource_id=user.username,
            metadata={"cleared_for": user.username},
            user=request.user,
        )
        messages.success(
            request,
            f"Cleared {user.username}'s labs context. Their next request in labs, and any open "
            f"session, asks Connect again what they may access.",
        )
        return back

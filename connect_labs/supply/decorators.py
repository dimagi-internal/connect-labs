from collections import namedtuple
from functools import wraps

from django.http import JsonResponse

from . import rbac, roles

Actor = namedtuple("Actor", "user role org")


def current_actor(request):
    role = roles.resolve_role(request.user)
    org = None
    if role == roles.SUPPLIER:
        org = request.user.supply_membership.org
    return Actor(request.user, role, org)


def require_perm(module, verb):
    """Gate a JSON view on the server permission matrix.

    Sets ``request.supply_actor`` for the view. 401 when the caller has no
    supply role at all, 403 when the role lacks the verb.
    """

    def deco(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            actor = current_actor(request)
            if actor.role is None:
                return JsonResponse({"error": "authentication required"}, status=401)
            if not rbac.can(actor.role, module, verb):
                return JsonResponse({"error": "forbidden"}, status=403)
            request.supply_actor = actor
            return view(request, *args, **kwargs)

        return wrapped

    return deco

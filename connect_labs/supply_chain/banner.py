"""The line under the Supply banner: which program, and who buys for it.

"RUTF · Lakeside Health Trust, buyer of record" -- the program by its name and
the organisation that is buyer of record for it, in that role. Every supply
page carries it, so a screen far from the overview still says whose purchasing
it is.

The buyer is read off the records, never assumed: the organisation named on
the program's orders where the program's own organisation is buyer of record
(`Contract.buyer_of_record == "programme_org"`), the most common one when there
are several. Before any order exists, the organisation acting in the program
(`identity.resolve_org`) -- the one the overview's heading already names.
Neither known, the line is the program's name alone.
"""

from collections import Counter

from django.core.exceptions import PermissionDenied


def program_line(request) -> str:
    """ "<program> · <organisation>, buyer of record"; "" with no program in scope."""
    from connect_labs.labs.context import get_org_data
    from connect_labs.supply_chain.api_views import _access, has_program_context

    if not has_program_context(request):
        return ""
    try:
        # The program the data access was authorised for: a program the caller
        # may not see is not named here either.
        access = _access(request)
    except PermissionDenied:
        return ""
    program_id = access.program_id
    programs = get_org_data(request).get("programs") or []
    name = next((p.get("name") for p in programs if str(p.get("id")) == str(program_id) and p.get("name")), None)
    name = name or f"Program {program_id}"
    buyer = _buyer_of_record(access, program_id)
    return f"{name} · {buyer}, buyer of record" if buyer else name


def _buyer_of_record(access, program_id) -> str:
    from connect_labs.supply_chain.identity import IdentityUnresolved, resolve_org
    from connect_labs.supply_chain.models import Contract

    named = Counter(
        Contract.objects.filter(program_id=program_id, buyer_of_record="programme_org", buyer_org__isnull=False)
        .values_list("buyer_org__name", flat=True)
        .order_by()
    )
    if named:
        return sorted(named.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    try:
        org = resolve_org(access)
    except IdentityUnresolved:
        return ""
    return org.name if org is not None else ""

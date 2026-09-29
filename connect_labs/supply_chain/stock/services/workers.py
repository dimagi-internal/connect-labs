"""Finding -- and on first sight, making -- the supply point a worker's stock rests at.

Hand-made worker points never keep up with a real roster (design 2026-09-28
section 2), so the reader makes one the first time a worker submits a visit:
kind `user_held`, under the rule's resupply store, slug
`user-{opp}-{username}` (the convention stock_report_ingest already used).

Matched on the username first and Connect's user UUID second: the username
is what every other supply screen and a distribution line use, and the UUID
is what survives a worker being renamed. A UUID alone finds a worker but
never makes one -- a point has to be named after somebody a person can
recognise.

Loaded once per run, so a thousand visits resolve their workers in memory
rather than with a query each.
"""

from connect_labs.supply_chain.models import SupplyPoint


def worker_slug(opportunity_id, username) -> str:
    return f"user-{opportunity_id}-{username}"[:96]


class WorkerIndex:
    def __init__(self, access, opportunity_id):
        self.access = access
        self.opportunity_id = opportunity_id
        points = list(
            SupplyPoint.objects.filter(program_id=access.program_id, opportunity_id=opportunity_id, kind="user_held")
        )
        self.by_username = {p.connect_username: p for p in points if p.connect_username}
        self.by_uuid = {p.connect_user_uuid: p for p in points if p.connect_user_uuid}
        self.created: list[int] = []

    def find(self, username, user_uuid):
        found = self.by_username.get(username or "")
        if found is None and user_uuid:
            found = self.by_uuid.get(user_uuid)
        return found

    def ensure(self, username, user_uuid, parent):
        """The worker's point, made if new; None when the visit names nobody usable."""
        username = (username or "").strip()
        user_uuid = (user_uuid or "").strip()
        found = self.find(username, user_uuid)
        if found is not None:
            if user_uuid and not found.connect_user_uuid:
                found.connect_user_uuid = user_uuid
                found.save(update_fields=["connect_user_uuid", "updated_at"])
                self.by_uuid[user_uuid] = found
            return found
        if not username:
            return None
        point = self.access.upsert_supply_point(
            {
                "slug": worker_slug(self.opportunity_id, username),
                "name": username,
                "kind": "user_held",
                "opportunity_id": self.opportunity_id,
                "connect_username": username,
                "connect_user_uuid": user_uuid,
                "parent_supply_point_id": parent.pk,
                "source": "connect_visit",
            }
        )
        self.by_username[username] = point
        if user_uuid:
            self.by_uuid[user_uuid] = point
        self.created.append(point.pk)
        return point

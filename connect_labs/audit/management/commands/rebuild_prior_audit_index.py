"""Rebuild the prior-audit projection for one opportunity, or verify it.

    manage.py rebuild_prior_audit_index --opportunity 1973
    manage.py rebuild_prior_audit_index --opportunity 1973 --verify-only
    manage.py rebuild_prior_audit_index --opportunity 1973 --json

``--verify-only`` is the one that matters right now. Nothing reads the
projection yet (#1246 step 1), so the only question worth asking is whether it
agrees with the live computation on real data. It exits non-zero when it does
not, so it can gate the switch-over rather than being read by eye.

Needs an OAuth access token to reach Connect's export API, the same as any other
audit read -- pass --token, or run it where a service token is configured.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from connect_labs.audit import prior_audit_projection as projection
from connect_labs.audit.data_access import AuditDataAccess, build_prior_audit_index


class Command(BaseCommand):
    help = "Rebuild (or verify) the local prior-audit projection for an opportunity."

    def add_arguments(self, parser):
        parser.add_argument("--opportunity", type=int, required=True, help="opportunity id")
        parser.add_argument("--token", default=None, help="OAuth access token for the export API")
        parser.add_argument("--username", default=None, help="record who built it, for scope diagnosis")
        parser.add_argument(
            "--verify-only",
            action="store_true",
            help="do not write; diff the existing projection against the live computation",
        )
        parser.add_argument("--json", action="store_true", help="machine-readable output")

    def handle(self, *args, **opts):
        opp = opts["opportunity"]
        data_access = AuditDataAccess(opportunity_id=opp, access_token=opts["token"])

        # One fetch, used for BOTH the rebuild and the live comparison. Fetching
        # twice would let the source change between them and turn an ordinary
        # race into what looks like a projection bug.
        sessions = [s for s in data_access.get_audit_sessions(status="completed") if s.opportunity_id == opp]
        live = build_prior_audit_index(sessions)

        result = None
        if not opts["verify_only"]:
            result = projection.rebuild_opportunity(opp, sessions, built_by=opts.get("username") or "")

        # Judged against what THIS token can see. The projection may legitimately
        # hold rows from sessions outside this scope -- it merges across
        # identities -- and calling those drift would make the gate unusable for
        # anyone without full visibility.
        verdict = projection.verify_opportunity(opp, live, visible_session_ids={s.id for s in sessions})

        if opts["json"]:
            self.stdout.write(
                json.dumps(
                    {
                        "rebuild": result,
                        "agrees": verdict.agrees,
                        "live_keys": verdict.live_keys,
                        "projected_keys": verdict.projected_keys,
                        "missing": verdict.missing[:50],
                        "extra": verdict.extra[:50],
                        "mismatched": verdict.mismatched[:50],
                    },
                    indent=2,
                    default=str,
                )
            )
        else:
            if result:
                self.stdout.write(
                    f"rebuilt opp {opp}: {result['sessions_contributing']}/{result['sessions_seen']} sessions "
                    f"contributed, {result['rows_written']} rows written ({result['rows_deleted']} removed)"
                )
            self.stdout.write(verdict.summary())
            # Truncated on purpose: a systemic disagreement produces thousands of
            # lines and the first few already say which way it is broken.
            for label, items in (("missing", verdict.missing), ("extra", verdict.extra)):
                for key in items[:10]:
                    self.stdout.write(f"  {label}: {key}")
                if len(items) > 10:
                    self.stdout.write(f"  ... and {len(items) - 10} more {label}")
            for m in verdict.mismatched[:10]:
                self.stdout.write(f"  mismatched: {m['key']} live={m['live']} projected={m['projected']}")

        if not verdict.agrees:
            raise CommandError("projection does NOT agree with the live index — do not switch the read path")

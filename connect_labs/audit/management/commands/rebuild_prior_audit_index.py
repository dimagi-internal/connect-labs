"""Rebuild the prior-audit projection for one opportunity, or verify it.

    manage.py rebuild_prior_audit_index --opportunity 1973
    manage.py rebuild_prior_audit_index --opportunity 1973 --verify-only
    manage.py rebuild_prior_audit_index --opportunity 1973 --json

``--verify-only`` is the gate: it diffs the projection against the live
computation and exits non-zero when they disagree, so it can block the
switch-over rather than being read by eye.

AUTHENTICATION: prefer ``--as <username>``, which looks up that labs user's
stored Connect token the way the reconcile command and Pulse's ingest do.
``--token`` still exists for local one-offs, but do NOT pass it through
run-labs-command -- that workflow takes the command line as a free-text
dispatch input, so the token would be recorded in the workflow inputs and the
Actions log. ``--as`` keeps the credential in the database where it already
lives.

Scope follows whichever identity is used: the export API returns what that
user's org membership can see. That is safe by construction here --
rebuild_opportunity merges and cannot delete rows for sessions it did not see
(#1260) -- but a narrow identity will simply build less, so prefer the widest
one available.
"""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from connect_labs.audit import prior_audit_projection as projection
from connect_labs.audit.data_access import AuditDataAccess, build_prior_audit_index
from connect_labs.labs.connect_tokens import ConnectTokenError, get_valid_access_token


class Command(BaseCommand):
    help = "Rebuild (or verify) the local prior-audit projection for an opportunity."

    def add_arguments(self, parser):
        parser.add_argument("--opportunity", type=int, required=True, help="opportunity id")
        parser.add_argument(
            "--as", dest="username", default=None, help="labs username whose stored Connect token to use"
        )
        parser.add_argument(
            "--token", default=None, help="raw OAuth token (local use only — never via run-labs-command)"
        )
        parser.add_argument(
            "--verify-only",
            action="store_true",
            help="do not write; diff the existing projection against the live computation",
        )
        parser.add_argument("--json", action="store_true", help="machine-readable output")

    def handle(self, *args, **opts):
        opp = opts["opportunity"]
        token = opts["token"]
        if not token:
            if not opts["username"]:
                raise CommandError("pass --as <username> (preferred) or --token")
            try:
                user = get_user_model().objects.get(username=opts["username"])
            except get_user_model().DoesNotExist:
                raise CommandError(
                    f"user {opts['username']!r} does not exist in labs "
                    "(they must have logged into labs in a browser at least once)"
                )
            try:
                token = get_valid_access_token(user)
            except ConnectTokenError as exc:
                # An expired refresh looks exactly like "nothing to build" if it
                # is allowed to fall through, so it has to stop the run.
                raise CommandError(f"no usable Connect token for {opts['username']!r}: {exc}") from exc
        data_access = AuditDataAccess(opportunity_id=opp, access_token=token)

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

"""Re-verify every built prior-audit projection against Connect, and repair drift.

    manage.py reconcile_prior_audit_index --as <username>
    manage.py reconcile_prior_audit_index --as <username> --repair
    manage.py reconcile_prior_audit_index --as <username> --opportunity 1973

Labs does not own the source. Audit sessions live in Connect and labs writes
them over the API, so a completion whose projection dual-write failed leaves the
two apart with nothing to announce it -- ``record_session`` deliberately swallows
its errors so a failed projection write cannot 500 an audit the user already
completed. This is what closes that loop.

WHY --as IS REQUIRED AND HAS NO DEFAULT
Reconciliation reads Connect through one user's stored OAuth token, and the
export API returns what THAT user's org membership can see. A narrower identity
does not error; it returns fewer sessions, and rebuilding from them would delete
prior verdicts that exist -- telling auditors an image has never been judged when
it has, which is the dangerous direction.

Pulse hit the same trap from the other side and documented it
(``pulse/client.get_poller_user``): falling back to whichever user happened to
have a token picked a narrower account and understated every headline figure ~5x,
silently. So the identity is named explicitly, recorded on the state row, and a
scope change shows up as a session-count drop rather than as quiet data loss.

Exit codes: 0 = everything agrees (or was repaired), 2 = drift found and not
repaired, 1 = usage/auth error.
"""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from connect_labs.audit import prior_audit_projection as projection
from connect_labs.audit.data_access import AuditDataAccess, build_prior_audit_index
from connect_labs.audit.prior_audit_models import PriorAuditProjectionState
from connect_labs.labs.connect_tokens import ConnectTokenError, get_valid_access_token


class Command(BaseCommand):
    help = "Verify built prior-audit projections against Connect; --repair rebuilds those that drifted."

    def add_arguments(self, parser):
        parser.add_argument("--as", dest="username", required=True, help="labs username whose Connect token to use")
        parser.add_argument("--opportunity", type=int, default=None, help="limit to one opportunity")
        parser.add_argument("--repair", action="store_true", help="merge this identity's view into ones that disagree")
        parser.add_argument(
            "--prune-unseen",
            action="store_true",
            help=(
                "ALSO delete projection rows for sessions this identity cannot see. "
                "Only safe from an identity known to see every session in the opportunity; "
                "it is the one operation here that can lose data."
            ),
        )
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **opts):
        username = opts["username"]
        try:
            user = get_user_model().objects.get(username=username)
        except get_user_model().DoesNotExist:
            raise CommandError(f"user {username!r} does not exist in labs (they must have logged in at least once)")
        try:
            token = get_valid_access_token(user)
        except ConnectTokenError as exc:
            # The expired-refresh case is the one most easily mistaken for
            # "nothing has drifted", so it must be an error, not an empty pass.
            raise CommandError(f"no usable Connect token for {username!r}: {exc}") from exc

        states = PriorAuditProjectionState.objects.all().order_by("opportunity_id")
        if opts["opportunity"] is not None:
            states = states.filter(opportunity_id=opts["opportunity"])
        # Only BUILT opportunities are reconciled. An unbuilt one is already
        # falling back to live computation, so it cannot be serving a wrong
        # answer and there is nothing here to repair.
        if not states.exists():
            self.stdout.write("no built projections to reconcile")
            return

        report = []
        drifted = 0
        for state in states:
            opp = state.opportunity_id
            data_access = AuditDataAccess(opportunity_id=opp, access_token=token)
            try:
                sessions = [s for s in data_access.get_audit_sessions(status="completed") if s.opportunity_id == opp]
                live = build_prior_audit_index(sessions)
                # Judge only what this identity can actually see. The projection
                # is a union across every identity that has built it, so rows
                # from sessions outside this scope are expected, not drift.
                visible = {s.id for s in sessions}
                verdict = projection.verify_opportunity(opp, live, visible_session_ids=visible)

                # Informational now rather than a veto: rebuild_opportunity
                # merges and cannot delete rows for sessions it did not see, so a
                # narrow identity can no longer destroy verdicts by repairing.
                # Still worth printing -- it is the cheapest signal that this
                # identity sees less than the projection was built from, which is
                # why an opportunity might stay short of complete.
                scope_warning = len(sessions) < state.source_sessions

                repaired = False
                if not verdict.agrees and opts["repair"]:
                    projection.rebuild_opportunity(opp, sessions, built_by=username, prune_unseen=opts["prune_unseen"])
                    repaired = True

                if not verdict.agrees and not repaired:
                    drifted += 1
                report.append(
                    {
                        "opportunity_id": opp,
                        "agrees": verdict.agrees,
                        "repaired": repaired,
                        "scope_warning": scope_warning,
                        "beyond_scope": verdict.beyond_scope,
                        "live_keys": verdict.live_keys,
                        "projected_keys": verdict.projected_keys,
                        "sessions_now": len(sessions),
                        "sessions_at_build": state.source_sessions,
                    }
                )
                if not opts["json"]:
                    status = "ok" if verdict.agrees else ("repaired" if repaired else "DRIFT")
                    beyond = f" beyond-scope={verdict.beyond_scope}" if verdict.beyond_scope else ""
                    self.stdout.write(
                        f"opp {opp}: {status} — live={verdict.live_keys} projected={verdict.projected_keys} "
                        f"missing={len(verdict.missing)} extra={len(verdict.extra)} "
                        f"mismatched={len(verdict.mismatched)} sessions={len(sessions)}{beyond}"
                        f"{' [SCOPE]' if scope_warning else ''}"
                    )
            finally:
                data_access.close()

        if opts["json"]:
            self.stdout.write(json.dumps({"drifted": drifted, "opportunities": report}, indent=2, default=str))

        if drifted:
            raise CommandError(f"{drifted} opportunit(ies) drifted from Connect — re-run with --repair")

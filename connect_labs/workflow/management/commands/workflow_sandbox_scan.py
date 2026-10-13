"""Report which workflows' render code would break in the sandbox frame.

For deciding whether ``LABS_WORKFLOW_SANDBOX`` can go to ``all`` (spec:
docs/superpowers/specs/2026-10-12-page-sandbox-design.md, Phase 2, "Rollout"). It
reads every workflow definition it can, resolves the render code the run page would
actually draw (stored copy, followed template, or a template workflow's published
version), and checks it for what the frame does not give it -- see
``workflow/sandbox_scan.py`` for the patterns and ``workflow/sandbox_scan_sources.py``
for what each source covers. It changes nothing: no record, no setting.

Coverage, plainly:

* with no flags: every labs-local (synthetic) definition of every owner, and every
  deployed seed template. Definitions on REAL opportunities live on production
  Connect and are NOT covered; the report says so.
* ``--as-user USERNAME`` (repeatable) / ``--all-users``: also every scope in each
  user's Connect org tree, read with their stored Connect token. A definition that no
  scanned user can read is still not covered.

On prod (output lands in the Actions log):

    gh workflow run run-labs-command.yml --repo dimagi-internal/connect-labs --ref main \\
        --field command="workflow_sandbox_scan --all-users"

Output: ids, names, scopes, pattern ids and line numbers. Never code, never data.
``--summary`` prints only the coverage and the counts (the rows run to hundreds).
Exits 0 whatever it finds; non-zero only when it cannot run.
"""

from __future__ import annotations

import json
from collections import Counter

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from connect_labs.workflow.sandbox import sandbox_enabled, workflow_sandbox_mode
from connect_labs.workflow.sandbox_scan import PATTERNS, SEVERITY_ORDER
from connect_labs.workflow.sandbox_scan_sources import Coverage, connect_targets, local_targets, template_targets

GAP = (
    "NOT COVERED: workflow definitions on real (non-synthetic) opportunities, programs and organizations. "
    "They live on production Connect, readable only per scope by a member; there is no cross-owner listing. "
    "Include them with --as-user USERNAME (repeatable) or --all-users, which read each user's org tree "
    "with their stored Connect token."
)
#: Patterns with a one-for-one replacement. `location_assign` and `location_reload`
#: are compiled to the host's navigate in the frame (rewrite.ts) and need none; a
#: `confirm` that the compile cannot make wait (not in an async function or handler)
#: still does.
MECHANICAL = {
    "confirm": "await view.confirm(text)  (in an async function)",
}
PARTIAL_GAP = (
    "PARTLY COVERED: real-opportunity definitions were read only in the scopes the scanned users belong to. "
    "Definitions in scopes none of them can read are NOT covered."
)


class Command(BaseCommand):
    help = "Report which workflows' effective render code would break in the sandbox frame (read-only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--as-user",
            action="append",
            default=[],
            metavar="USERNAME",
            help="Also read real-opportunity definitions as this user (stored Connect token). Repeatable.",
        )
        parser.add_argument(
            "--all-users", action="store_true", help="Also read as every user with a stored Connect token."
        )
        parser.add_argument("--no-local", action="store_true", help="Skip labs-local (synthetic) definitions.")
        parser.add_argument(
            "--min-severity",
            choices=list(SEVERITY_ORDER),
            default="changes",
            help="Least severe finding listed row by row (all are counted). Default: changes.",
        )
        parser.add_argument("--json", action="store_true", help="Machine-readable output.")
        parser.add_argument(
            "--summary",
            action="store_true",
            help="Only the coverage and the counts: no rows, no per-template table.",
        )
        parser.add_argument(
            "--fix-suggestions",
            action="store_true",
            help="Also print, per pattern found, the replacement that makes render code sandbox-safe. "
            "Suggestions only: no record is written.",
        )

    def handle(self, *args, **options):
        users = self._users(options["as_user"], options["all_users"])
        coverage = Coverage(users_requested=[u.username for u in users])

        definitions = []
        if not options["no_local"]:
            definitions = local_targets()
            coverage.local_definitions = len(definitions)
        if users:
            definitions += connect_targets(users, coverage, seen=set())
        templates = template_targets()

        report = {
            "mode": workflow_sandbox_mode(),
            "page_sandbox": sandbox_enabled(),
            "coverage": coverage.as_dict(),
            "coverage_gap": GAP if not coverage.real_opps_covered else PARTIAL_GAP,
            "definitions": [t.as_dict() for t in definitions],
            "templates": [t.as_dict() for t in templates],
        }
        if options["json"]:
            self.stdout.write(json.dumps(report, indent=2, default=str))
            return
        self._text(report, definitions, templates, options)

    # ------------------------------------------------------------------

    def _users(self, usernames, all_users):
        User = get_user_model()
        if all_users:
            from connect_labs.labs.models import UserConnectToken

            ids = UserConnectToken.objects.values_list("user_id", flat=True)
            return list(User.objects.filter(id__in=ids).order_by("username"))
        users = []
        for name in usernames:
            user = User.objects.filter(username=name).first()
            if user is None:
                raise CommandError(f"no labs user {name!r}")
            users.append(user)
        return users

    def _text(self, report, definitions, templates, options):
        w = self.stdout.write
        floor = SEVERITY_ORDER[options["min_severity"]]
        cov = report["coverage"]
        w("workflow_sandbox_scan -- render code that would break or change in the sandbox frame")
        w(f"LABS_WORKFLOW_SANDBOX={report['mode']}  LABS_PAGE_SANDBOX={report['page_sandbox']}")
        w("")
        w("== Coverage")
        w(f"labs-local definitions read: {cov['local_definitions']}")
        if cov["users_requested"]:
            w(
                f"users requested: {len(cov['users_requested'])}, scanned: {len(cov['users_scanned'])}, "
                f"without a usable token: {len(cov['users_without_token'])}"
            )
            w(
                f"Connect scopes read: {cov['scopes_read']}, refused (403/404): {cov['scopes_refused']}, "
                f"failed: {cov['scopes_failed']}; Connect definitions read: {cov['connect_definitions']}"
            )
        w(report["coverage_gap"])
        w("")

        for title, targets in (("Definitions", definitions), ("Deployed seed templates", templates)):
            self._section(title, targets, floor, summary=options["summary"])
        if options["summary"]:
            return

        if options["fix_suggestions"]:
            found = Counter(f.pattern for t in definitions + templates for f in t.findings)
            w("== Fix suggestions (nothing is written; edit the render code, or its template)")
            for p in PATTERNS:
                if found.get(p.id):
                    w(f"- {p.id} [{p.severity}] x{found[p.id]}: {p.what}")
                    w(f"    replace with: {p.fix}")
            if not found:
                w("(nothing to fix)")
            w("")
            # A proposed codemod for live copies, as a summary: which lines a mechanical
            # replacement would touch. It never prints code, and never writes a record.
            w("== Proposed mechanical replacements in live definitions (summary; nothing is written)")
            proposed = 0
            for t in definitions:
                lines_by = {}
                for f in t.findings:
                    if f.pattern in MECHANICAL and f.severity == "breaks":
                        lines_by.setdefault(f.pattern, []).append(f.line)
                for pattern, lines in lines_by.items():
                    proposed += 1
                    w(
                        f"  {t.origin}:{t.id} | {t.name[:60]} | {t.owner} | {t.render_source} | "
                        f"{pattern} -> {MECHANICAL[pattern]} | lines {', '.join(map(str, lines))}"
                    )
            if not proposed:
                w("(none)")
            w("")

    def _section(self, title, targets, floor, summary=False):
        w = self.stdout.write
        with_findings = [t for t in targets if t.findings]
        w(f"== {title}: {len(targets)} scanned, {len(with_findings)} with findings")
        by_worst = Counter(t.worst or "clean" for t in targets)
        w("by worst finding: " + ", ".join(f"{k}={by_worst[k]}" for k in ("breaks", "changes", "info", "clean")))
        w(
            "breaking under `all` but not sandboxed now: "
            f"{sum(1 for t in targets if t.worst == 'breaks' and t.sandboxed_all and not t.sandboxed_now)}; "
            f"breaking and sandboxed now: {sum(1 for t in targets if t.worst == 'breaks' and t.sandboxed_now)}"
        )
        pinned = sum(1 for t in targets if t.worst == "breaks" and t.exempt == "deprecated")
        recorded = sum(1 for t in targets if t.worst == "breaks" and t.exempt == "recorded")
        w(
            f"breaking but kept out of the sandbox under `all`: pinned deprecated {pinned}, "
            f"recorded exemption {recorded}; "
            f"breaking AND sandboxed under `all`: "
            f"{sum(1 for t in targets if t.worst == 'breaks' and t.sandboxed_all)}"
        )
        w(f"no render code found: {sum(1 for t in targets if not t.code_found)}")

        by_pattern = Counter()
        hits = Counter()
        for t in targets:
            for key in {(f.pattern, f.severity) for f in t.findings}:
                by_pattern[key] += 1
            for f in t.findings:
                hits[(f.pattern, f.severity)] += 1
        w("-- by pattern and severity (definitions / occurrences)")
        for p in PATTERNS:
            for sev in SEVERITY_ORDER:
                if by_pattern.get((p.id, sev)):
                    w(f"  {p.id:<18} {sev:<8} {by_pattern[(p.id, sev)]:>4} / {hits[(p.id, sev)]}")

        if summary:
            breaking = Counter((t.template_type or "-") for t in targets if t.worst == "breaks" and t.sandboxed_all)
            w("-- breaking AND sandboxed under `all`, by template")
            for tt, n in sorted(breaking.items()):
                w(f"  {tt:<32} {n}")
            if not breaking:
                w("  (none)")
            w("")
            return

        by_template = Counter()
        for t in targets:
            by_template[(t.template_type or "-", t.render_source.split(":")[0].split(" ")[0], t.worst or "clean")] += 1
        w("-- by template / render source / worst")
        for (tt, rs, worst), n in sorted(by_template.items()):
            w(f"  {tt:<32} {rs:<18} {worst:<8} {n}")

        w("-- rows: origin id | name | owner | render source | sandboxed now/all | pattern | severity | line | detail")
        for t in sorted(targets, key=lambda t: (SEVERITY_ORDER.get(t.worst, 9), str(t.owner), str(t.id))):
            for f in t.findings:
                if SEVERITY_ORDER[f.severity] > floor:
                    continue
                w(
                    f"  {t.origin}:{t.id} | {t.name[:60]} | {t.owner} | {t.render_source} | "
                    f"{'yes' if t.sandboxed_now else 'no'}/{'yes' if t.sandboxed_all else 'no'} | "
                    f"{f.pattern} | {f.severity} | {f.line} | {f.detail}"
                )
            if t.note:
                w(f"  {t.origin}:{t.id} | note: {t.note}")
        w("")

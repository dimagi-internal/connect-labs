"""Local demo data for the Photo Verification Success Rate report.

Creates two labs-only synthetic opportunities (EHA, C3HD) standing in for
Readers Nigeria opps 1996/1997, and seeds AuditSession records with a
hand-checkable pass/fail/duplicate mix so the report's three numbers are
known in advance:

    EHA   (auditor's audits):  pass 17 / (17+2+1) = 20  -> 85.00%
    C3HD  (auditor's audits):  pass  9 / ( 9+3+0) = 12  -> 75.00%
    Combined (pooled):         pass 26 / 32           -> 81.25%

Everything is stored in the labs DB via ``LabsLocalRecord`` (opp_id >=
10_000), so nothing touches production. The same helper backs both the
endpoint integration test and the ``seed_photo_verification_demo``
management command, so what is asserted in tests is exactly what a human
sees clicking through the report.
"""

from connect_labs.labs.synthetic.models import LabsLocalRecord, SyntheticOpportunity

# ── Fixed identifiers, referenced by tests and the management command ──────────
# The program id is in the labs-only range (>= 10_000) so program-scoped reads
# route to the local backend, and both demo opps are filed under it.
PROGRAM_ID = 10009
PROGRAM_NAME = "Readers Nigeria (demo)"
EHA_OPP_ID = 10001
C3HD_OPP_ID = 10002
AUDITOR = "Abdoul Ndiaye"
OTHER_AUDITOR = "Other Auditor"

# The five audit IDs the report is scoped to (as Abdoul supplied them). Here
# four are used as workflow-run ids and one (7735) as a session's own id, to
# exercise both match paths.
FILTER_IDS = [5064, 6425, 12446, 7735, 6837]
IDS_CSV = ",".join(str(i) for i in FILTER_IDS)

# Session ids that must be EXCLUDED by the filters (asserted in tests).
EXCLUDED_BY_IDS_SESSION_ID = 80003  # its run (9999) is not in FILTER_IDS
EXCLUDED_BY_AUDITOR_SESSION_ID = 80004  # in-list run 5064 but a different auditor
OWN_ID_MATCH_SESSION_ID = 7735  # matched by its own id, has no run link


def _visit_results(n_pass, n_fail, n_dup, n_pending):
    """Build a ``visit_results`` blob with the requested assessment verdicts.

    ``get_assessment_stats`` only reads each assessment's ``result``, so one
    visit holding all the assessments is enough to produce the right totals.
    """
    assessments = {}
    i = 0

    def _add(n, result):
        nonlocal i
        for _ in range(n):
            i += 1
            assessments[f"blob{i}"] = {"result": result, "question_id": "group/photo"}

    _add(n_pass, "pass")
    _add(n_fail, "fail")
    _add(n_dup, "duplicate_fake")
    _add(n_pending, None)  # None -> pending (unreviewed)
    return {"v1": {"assessments": assessments}}


OPP_NAMES = {EHA_OPP_ID: "EHA (demo)", C3HD_OPP_ID: "C3HD (demo)"}


def _session(session_id, opp_id, run_id, username, counts, title):
    LabsLocalRecord.objects.update_or_create(
        id=session_id,
        defaults=dict(
            experiment="audit",
            type="AuditSession",
            opportunity_id=opp_id,
            labs_record_id=run_id,
            username=username,
            data={
                "title": title,
                "status": "completed",
                "opportunity_id": opp_id,
                # The OPPORTUNITY's name (consistent across its sessions) -- what
                # the report groups by. Distinct from the session's own title.
                "opportunity_name": OPP_NAMES.get(opp_id, f"Opportunity {opp_id}"),
                "visit_results": _visit_results(*counts),
            },
        ),
    )


def demo_org_data():
    """The ``organization_data`` shape a labs session needs so that program-mode
    reads (and the context picker) can see the two demo opps under the demo
    program. Injected into a local session for browser/test use.
    """
    return {
        "programs": [{"id": PROGRAM_ID, "name": PROGRAM_NAME}],
        "opportunities": [
            {"id": EHA_OPP_ID, "name": "EHA (demo)", "program": PROGRAM_ID},
            {"id": C3HD_OPP_ID, "name": "C3HD (demo)", "program": PROGRAM_ID},
        ],
    }


def seed_demo():
    """Create/refresh the two synthetic opps and their audit sessions.

    Idempotent: safe to run repeatedly. Returns the identifiers and expected
    figures so callers don't hardcode them.
    """
    for opp_id, label in (
        (EHA_OPP_ID, "Readers Nigeria — EHA (demo)"),
        (C3HD_OPP_ID, "Readers Nigeria — C3HD (demo)"),
    ):
        SyntheticOpportunity.objects.update_or_create(
            opportunity_id=opp_id,
            defaults=dict(
                labs_only=True,
                enabled=True,
                label=label,
                org_name=label,
                program_id=PROGRAM_ID,
                program_name=PROGRAM_NAME,
            ),
        )

    # EHA (opp 10001) -- (pass, fail, dup, pending)
    _session(80001, EHA_OPP_ID, 5064, AUDITOR, (10, 1, 1, 0), "EHA weekly review A")
    _session(80002, EHA_OPP_ID, 6425, AUDITOR, (7, 1, 0, 3), "EHA weekly review B")
    _session(EXCLUDED_BY_IDS_SESSION_ID, EHA_OPP_ID, 9999, AUDITOR, (5, 5, 0, 0), "EHA off-list run")
    _session(EXCLUDED_BY_AUDITOR_SESSION_ID, EHA_OPP_ID, 5064, OTHER_AUDITOR, (4, 0, 0, 0), "EHA other auditor")

    # C3HD (opp 10002)
    _session(80010, C3HD_OPP_ID, 12446, AUDITOR, (6, 2, 0, 0), "C3HD weekly review A")
    _session(OWN_ID_MATCH_SESSION_ID, C3HD_OPP_ID, None, AUDITOR, (3, 1, 0, 0), "C3HD session-id match")
    _session(80012, C3HD_OPP_ID, 6837, AUDITOR, (0, 0, 0, 5), "C3HD all pending")

    return {
        "program_id": PROGRAM_ID,
        "program_name": PROGRAM_NAME,
        "eha_opp_id": EHA_OPP_ID,
        "c3hd_opp_id": C3HD_OPP_ID,
        "auditor": AUDITOR,
        "ids_csv": IDS_CSV,
        "excluded_by_ids_session_id": EXCLUDED_BY_IDS_SESSION_ID,
        "excluded_by_auditor_session_id": EXCLUDED_BY_AUDITOR_SESSION_ID,
        "own_id_match_session_id": OWN_ID_MATCH_SESSION_ID,
        "expected": {
            "eha": {"pass": 17, "denom": 20, "rate": 85.0},
            "c3hd": {"pass": 9, "denom": 12, "rate": 75.0},
            "combined": {"pass": 26, "denom": 32, "rate": 81.25},
        },
    }

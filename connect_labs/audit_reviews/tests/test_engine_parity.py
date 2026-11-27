"""engine.py gives the same world as the browser engine it replaced.

The cases are generated data run through ``parity/engine_reference.js`` (a verbatim copy of the
browser engine) by ``parity/make_cases.js``. Rebuild them with
``node connect_labs/audit_reviews/tests/parity/make_cases.js`` only when the reference changes.
"""

import json
from pathlib import Path

import pytest

from connect_labs.audit_reviews import engine

CASES = json.loads((Path(__file__).parent / "parity" / "cases.json").read_text(encoding="utf-8"))


def _norm(x):
    """JSON.stringify drops undefined; Python keeps None. Compare without empty keys."""
    if isinstance(x, dict):
        return {k: _norm(v) for k, v in x.items() if v is not None}
    if isinstance(x, list):
        return [_norm(v) for v in x]
    return x


def _serialize(W):
    return {
        "goLiveIdx": W["goLiveIdx"],
        "latestRi": W["latestRi"],
        "pf": W["pf"],
        "entityOrder": [list(m.keys()) for m in W["ents"]],
        "entityInfo": W["entityInfo"],
        "history": W["history"],
        "reviews": W["reviews"],
        "openBy": list(W["openBy"].keys()),
        "proposalsByRun": W["proposalsByRun"],
    }


def _without_escalate(cfg):
    """Escalate was added after the browser engine was retired, so the reference never offers it."""
    cfg = dict(cfg)
    cfg["levelActions"] = {lv: [a for a in acts if a != "escalate"] for lv, acts in cfg["levelActions"].items()}
    return cfg


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_config_matches(case):
    got = _without_escalate(engine.default_config(case["D"], case["saved"]))
    assert _norm(got) == _norm(case["config"])


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_world_matches(case):
    cfg = engine.default_config(case["D"], case["saved"])
    world = engine.replay(case["D"], cfg, case["decisions"], case["opts"])
    got = _norm(json.loads(json.dumps(_serialize(world))))
    want = _norm(case["world"])
    for key in want:
        assert got[key] == want[key], key


def test_cases_exercise_the_rules():
    """The generated data must reach every outcome, or parity proves little."""
    seen = set()
    statuses = set()
    for c in CASES:
        for props in c["world"]["proposalsByRun"]:
            for p in props or []:
                if p.get("cls"):
                    seen.add(p["cls"])
        statuses |= {r["status"] for r in c["world"]["reviews"]}
    assert {"open", "close", "worsened", "improved", "stuck", "nochange", "absent"} <= seen
    assert {"open", "closed_improved", "closed_suspended", "closed_other"} <= statuses

"""Scenarios: what page, what to say, and what a right answer contains.

A suite file (YAML) holds:

* ``opportunity_sets`` -- named lists of synthetic opportunity ids.
* ``pages`` -- named places a chat lives: a workflow run (``workflow: {id, program_id |
  opportunity_id, run: latest | <run id>}``) or the Data page (``data: {programs: [...]}`` or
  ``{opportunities: [...]}``), each with the opportunity set its ground truth reads.
* ``scenarios`` -- each a page, a list of turns (``say`` + ``checks``) and ``truth``: named
  SQL the harness runs itself through labs' ``data_query`` as ace, over the page's
  opportunities, so a check can say "the reply names ``truth.total.n``" exactly.

A truth query returns one row; ``<name>.<column>`` is that row's cell. A check value may
be a literal or such a reference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

LABS = "https://labs.connect.dimagi.com"
SURFACES = ("workflow_run", "data_page")


@dataclass
class Page:
    name: str
    surface: str
    url: str
    opportunity_ids: list[int]
    describe: str = ""


@dataclass
class Turn:
    say: str
    checks: list[dict] = field(default_factory=list)


@dataclass
class Scenario:
    id: str
    title: str
    page: Page
    turns: list[Turn]
    truth_sql: dict[str, str]
    covers: str = ""
    rubric: str = ""
    truth: dict[str, dict] = field(default_factory=dict)  # resolved: name -> {column: value}
    truth_error: str = ""


class SuiteError(ValueError):
    pass


def load(path: Path, mcp=None) -> list[Scenario]:
    """Read a suite. With ``mcp`` (a ``LabsMCP``), resolve ``run: latest`` to a run id."""
    data = yaml.safe_load(Path(path).read_text())
    sets = {k: [int(x) for x in v] for k, v in (data.get("opportunity_sets") or {}).items()}
    pages = {name: _page(name, spec, sets, mcp) for name, spec in (data.get("pages") or {}).items()}
    out = []
    seen = set()
    for raw in data.get("scenarios") or []:
        sid = raw["id"]
        if sid in seen:
            raise SuiteError(f"duplicate scenario id {sid}")
        seen.add(sid)
        if raw["page"] not in pages:
            raise SuiteError(f"{sid}: unknown page {raw['page']}")
        turns = [Turn(say=t["say"], checks=list(t.get("checks") or [])) for t in raw["turns"]]
        if not turns:
            raise SuiteError(f"{sid}: no turns")
        out.append(
            Scenario(
                id=sid,
                title=raw.get("title", sid),
                page=pages[raw["page"]],
                turns=turns,
                truth_sql=dict(raw.get("truth") or {}),
                covers=raw.get("covers", ""),
                rubric=raw.get("rubric", ""),
            )
        )
    return out


def _page(name: str, spec: dict, sets: dict, mcp) -> Page:
    opps = spec.get("opportunities")
    opportunity_ids = sets[opps] if isinstance(opps, str) else [int(x) for x in (opps or [])]
    if "workflow" in spec:
        wf = spec["workflow"]
        scope_key = "program_id" if "program_id" in wf else "opportunity_id"
        scope = int(wf[scope_key])
        run = wf.get("run", "latest")
        if run == "latest":
            run = _latest_run(mcp, int(wf["id"]), scope_key, scope) if mcp else None
        url = f"{LABS}/labs/workflow/{wf['id']}/run/?{scope_key}={scope}"
        if run:
            url += f"&run_id={run}"
        return Page(name, "workflow_run", url, opportunity_ids, spec.get("describe", ""))
    if "data" in spec:
        sel = spec["data"] or {}
        if sel.get("programs"):
            query = "programs=" + ",".join(str(p) for p in sel["programs"])
        else:
            query = "opportunities=" + ",".join(str(o) for o in sel.get("opportunities") or opportunity_ids)
        return Page(name, "data_page", f"{LABS}/labs/data/?{query}", opportunity_ids, spec.get("describe", ""))
    raise SuiteError(f"page {name}: needs workflow: or data:")


def _latest_run(mcp, definition_id: int, scope_key: str, scope: int):
    runs = mcp.call("workflow_history_runs", {"definition_id": definition_id, scope_key: scope}).get("runs") or []
    done = [r for r in runs if r.get("status") == "completed"]
    return (done or runs or [{}])[-1].get("run_id")


def resolve_truth(scenario: Scenario, mcp) -> None:
    """Run each truth query as ace (labs ``data_query``) over the page's opportunities."""
    for name, sql in scenario.truth_sql.items():
        try:
            result = mcp.query(sql, scenario.page.opportunity_ids, max_rows=5)
        except Exception as e:  # noqa: BLE001 -- recorded on the scenario, reported, never fatal
            scenario.truth_error = f"{name}: {e}"
            return
        rows = result.get("rows") or []
        if not rows:
            scenario.truth_error = f"{name}: no rows"
            return
        scenario.truth[name] = dict(zip(result["columns"], rows[0]))


_REF = re.compile(r"^([a-zA-Z_][\w-]*)\.([\w?]+)$")


def value(ref, truth: dict):
    """A check's value: a literal, or ``name.column`` from the resolved truth."""
    if isinstance(ref, str):
        m = _REF.match(ref)
        if m and m.group(1) in truth:
            return truth[m.group(1)].get(m.group(2))
    return ref


_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][\w-]*)\.([\w?]+)\}")


def render(text: str, truth: dict) -> str:
    """``say`` with ``{name.column}`` filled from the ground truth (e.g. a visit id picked live)."""
    return _PLACEHOLDER.sub(lambda m: str(truth.get(m.group(1), {}).get(m.group(2), m.group(0))), text)

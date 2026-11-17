"""The loader stages are declared once, and everything that depends on the list agrees with it.

A rebuild from source is the stages in order. These tests are what stops a stage, a source or the docs from being added
without the others: each failure says what is still missing.
"""

from pathlib import Path

from django.core.management import call_command

from connect_labs.labs.indicators import stages
from connect_labs.labs.indicators.management.commands import indicators_bootstrap, load_indicators
from connect_labs.labs.indicators.models import Source

HERE = Path(__file__).resolve().parent.parent

#: Sources whose rows are written under another stage's loader or label, so no stage names them as ``Source.X``.
WRITTEN_BY_ANOTHER_STAGE = {
    Source.DERIVED: "every derive.load_* call (``_derive``)",
    Source.UNICEF_LBW_REGIONAL: "unicef_lbw.load, in the lbw stage",
}


def test_every_declared_stage_has_a_loader_on_the_command_and_nothing_else_does():
    declared = set(stages.NAMES)
    implemented = {n.removeprefix("_stage_") for n in dir(load_indicators.Command) if n.startswith("_stage_")}
    # sub-steps a stage calls (a source within a stage) carry a longer name; they are not stages
    assert declared <= implemented, f"stages declared but not implemented: {sorted(declared - implemented)}"
    stray = sorted(n for n in implemented - declared if not any(n.startswith(d + "_") for d in declared))
    assert not stray, f"_stage_ methods no declared stage runs: {stray} (declare them in stages.py)"


def test_a_stage_comes_after_everything_it_needs_and_names_are_unique():
    assert len(set(stages.NAMES)) == len(stages.NAMES)
    seen = set()
    for s in stages.STAGES:
        assert set(s.needs) <= seen, f"{s.name} needs {sorted(set(s.needs) - seen)}, which must run before it"
        seen.add(s.name)


def test_the_command_choices_and_bootstrap_run_the_declared_stages_not_a_list_of_their_own():
    assert load_indicators.STAGES == stages.NAMES
    assert not hasattr(indicators_bootstrap, "STAGE_ORDER")


def test_a_bootstrap_runs_every_stage_in_order_and_skip_heavy_leaves_the_heavy_ones(monkeypatch, db):
    ran = []
    real = call_command

    def spy(name, *args, **kw):
        if name == "load_indicators":
            ran.append(args[args.index("--stage") + 1])
        elif name not in ("indicators_bootstrap",):
            return None  # boundaries, parent links
        return None

    monkeypatch.setattr(indicators_bootstrap, "call_command", spy)
    real("indicators_bootstrap", "--skip-boundaries")
    assert ran == list(stages.NAMES)
    ran.clear()
    real("indicators_bootstrap", "--skip-boundaries", "--skip-heavy")
    assert ran == [s.name for s in stages.STAGES if not s.heavy]
    ran.clear()
    real("indicators_bootstrap", "--skip-boundaries", "--from-stage", "births")
    assert ran[0] == "births" and ran[-1] == stages.NAMES[-1]


def _cells(table: str) -> list[list[str]]:
    """A markdown table as its cells, ignoring the padding prettier adds to align the columns."""
    rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in table.strip().splitlines()]
    return [r for r in rows if not all(set(c) <= {"-", " "} for c in r)]  # the separator row is layout, not content


def test_bootstrap_md_carries_the_table_the_stages_render():
    text = (HERE / "BOOTSTRAP.md").read_text()
    block = text.split(stages.TABLE_START, 1)[1].split(stages.TABLE_END, 1)[0]
    assert _cells(block) == _cells(stages.markdown_table()), (
        "BOOTSTRAP.md's stage table is out of date; replace what is between the stages: markers with "
        "stages.markdown_table():\n" + stages.markdown_table()
    )


def test_every_source_is_written_by_some_stage():
    code = (HERE / "management" / "commands" / "load_indicators.py").read_text()
    orphans = [s.name for s in Source if s not in WRITTEN_BY_ANOTHER_STAGE and f"Source.{s.name}" not in code]
    assert not orphans, (
        f"sources {orphans} are in the model but no load_indicators stage writes them, so a rebuild would never "
        "restore them: add a stage (stages.py + _stage_<name>) or list them in WRITTEN_BY_ANOTHER_STAGE with where"
    )

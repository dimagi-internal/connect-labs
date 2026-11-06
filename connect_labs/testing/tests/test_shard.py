"""`--shard=K/N`: every file runs in exactly one shard, and the shards come out even."""

import json
from pathlib import Path

import pytest

import connect_labs
from connect_labs.testing.shard import assign, load_durations, parse, shard_of

pytest_plugins = ["pytester"]

# A bare pytest in a scratch dir: none of this repo's Django setup, no cache dir.
_REPO = str(Path(connect_labs.__file__).resolve().parents[1])
_ISOLATED = ("-p", "no:django", "-p", "no:cacheprovider", "-p", "no:logfire")


def test_every_file_lands_in_exactly_one_shard_and_the_loads_are_even():
    weights = {f"connect_labs/app{i}/tests/test_{i}.py": (i * 37) % 50 + 0.5 for i in range(300)}
    owner = assign(weights, 3)
    assert set(owner) == set(weights)
    assert set(owner.values()) == {1, 2, 3}
    loads = [sum(w for f, w in weights.items() if owner[f] == k) for k in (1, 2, 3)]
    assert max(loads) - min(loads) <= max(weights.values())


def test_one_slow_file_is_balanced_by_time_not_by_its_test_count():
    # The shape that left one shard two minutes behind: a file of a few slow
    # tests among many files of fast ones. By time, it gets a shard to itself.
    weights = {"slow.py": 90.0, **{f"fast_{i}.py": 10.0 for i in range(18)}}
    owner = assign(weights, 3)
    loads = sorted(sum(w for f, w in weights.items() if owner[f] == k) for k in (1, 2, 3))
    assert loads == [90.0, 90.0, 90.0]


def test_the_deal_does_not_depend_on_the_tables_order():
    weights = {"b.py": 5.0, "a.py": 5.0, "c.py": 9.0, "d.py": 1.0}
    reordered = dict(reversed(list(weights.items())))
    assert assign(weights, 2) == assign(reordered, 2)


def test_a_file_the_table_does_not_know_lands_in_the_same_shard_every_time():
    owner = assign({"known.py": 1.0}, 4)
    first = shard_of("connect_labs/new/tests/test_new.py", 4, owner)
    assert 1 <= first <= 4
    assert all(shard_of("connect_labs/new/tests/test_new.py", 4, owner) == first for _ in range(5))
    assert shard_of("known.py", 4, owner) == owner["known.py"]


def test_a_missing_or_broken_table_is_an_empty_one(tmp_path):
    broken = tmp_path / "durations.json"
    broken.write_text("{not json")
    assert load_durations(broken) == {}
    assert load_durations(tmp_path / "absent.json") == {}


@pytest.fixture
def scratch(pytester, monkeypatch):
    """A scratch pytest that can import this repo's shard plugin, and nothing else of it."""
    monkeypatch.setenv("PYTHONPATH", _REPO)
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
    pytester.makeconftest('pytest_plugins = ["connect_labs.testing.shard"]')
    return pytester


@pytest.mark.parametrize("spec", ["0/3", "4/3", "3", "a/b", ""])
def test_a_spec_it_cannot_read_is_refused_rather_than_ignored(spec):
    with pytest.raises(ValueError):
        parse(spec)


def test_a_spec_with_spaces_is_read():
    assert parse(" 2 / 3 ") == (2, 3)


def test_the_shards_run_every_test_once_and_never_import_each_others_files(scratch):
    pytester = scratch
    # Twelve files; each one that is imported leaves a marker. A shard must
    # import ONLY its own files -- skipping them before import is the point.
    pytester.makeini("[pytest]\npython_files = test_*.py\n")
    for i in range(12):
        pytester.makepyfile(
            **{
                f"test_m{i}": (
                    "import pathlib\n"
                    f"pathlib.Path('imported_{i}').touch()\n"
                    f"def test_a(): pass\n"
                    f"def test_b(): pass\n"
                )
            }
        )
    seen = []
    for k in (1, 2, 3):
        for marker in pytester.path.glob("imported_*"):
            marker.unlink()
        result = pytester.runpytest_subprocess(f"--shard={k}/3", *_ISOLATED, "-rA")
        ran = {line.split("::")[0] for line in _passed(result)}
        imported = {f"test_m{p.name.split('_')[1]}.py" for p in pytester.path.glob("imported_*")}
        assert imported == ran
        seen.extend(_passed(result))
    assert len(seen) == len(set(seen)) == 24


def test_the_durations_written_are_each_files_total(scratch):
    pytester = scratch
    pytester.makepyfile(test_x="def test_a(): pass\ndef test_b(): pass\n")
    out = pytester.path / "times.json"
    pytester.runpytest_subprocess(f"--shard-durations-out={out}", *_ISOLATED).assert_outcomes(passed=2)
    times = json.loads(out.read_text())
    assert list(times) == ["test_x.py"] and times["test_x.py"] >= 0


def _passed(result):
    outcomes = result.parseoutcomes()
    assert not outcomes.get("failed") and not outcomes.get("errors"), outcomes
    # -rA ends with one "PASSED <nodeid>" line per test.
    return [line.split(" ", 1)[1] for line in result.outlines if line.startswith("PASSED ")]

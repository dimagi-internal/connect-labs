"""dump_yaml writes exactly what yaml.dump writes, value by value instead of all at once."""

import datetime as dt
import math
import random

import numpy as np
import pytest
import yaml

from connect_labs.labs.synthetic.generator.fixtures.manifest import YAML_DUMPER, dump_yaml


def _reference(data):
    return yaml.dump(data, Dumper=YAML_DUMPER, default_flow_style=False, sort_keys=False)


AWKWARD_SCALARS = [
    None,
    True,
    False,
    0,
    -7,
    2**70,
    1.5,
    -0.0,
    1e-05,
    1e300,
    math.inf,
    -math.inf,
    math.nan,
    "",
    " ",
    "plain",
    "yes",
    "No",
    "on",
    "null",
    "~",
    "1.0",
    "0x1F",
    "1e3",
    "007",
    "2024-01-01",
    "a: b",
    "- dash",
    "#hash",
    "quote's",
    'dq"',
    "multi\nline\n",
    "trailing ",
    "\ttab",
    "ünïcödé",
    "x" * 300,
    "word " * 60,
    dt.date(2025, 6, 1),
    dt.datetime(2025, 6, 1, 13, 5, 7),
]


def _random_value(r, depth=0):
    roll = r.random()
    if depth > 3 or roll < 0.5:
        return r.choice(AWKWARD_SCALARS)
    if roll < 0.75:
        return [_random_value(r, depth + 1) for _ in range(r.randint(0, 4))]
    keys = [r.choice(["a", "b c", "yes", "1", "k" * 90, "é"]) for _ in range(r.randint(0, 4))] + [r.randint(0, 5)]
    return {k: _random_value(r, depth + 1) for k in keys}


@pytest.mark.parametrize("seed", range(200))
def test_the_same_bytes_as_yaml_dump(seed):
    data = {"top": _random_value(random.Random(seed)), "pool": [_random_value(random.Random(seed + 1))]}
    assert dump_yaml(data) == _reference(data)


@pytest.mark.parametrize("data", [{}, [], "only", 3, None, [[[]]], {"a": {}}, {"a": []}])
def test_the_same_bytes_for_edge_documents(data):
    assert dump_yaml(data) == _reference(data)


def test_a_shared_list_still_gets_yaml_dumps_anchor():
    shared = [1, 2]
    data = {"a": shared, "b": shared}
    assert dump_yaml(data) == _reference(data)
    assert "&id001" in dump_yaml(data)


def test_a_value_the_safe_dumper_refuses_is_still_refused():
    with pytest.raises(yaml.representer.RepresenterError):
        dump_yaml({"x": np.float64(1.0)})


def test_a_tuple_is_written_as_yaml_dump_writes_it():
    assert dump_yaml({"x": (1, 2)}) == _reference({"x": (1, 2)})

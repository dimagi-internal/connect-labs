"""`--shard=K/N`: every file runs in exactly one shard, and the shards come out even."""

import pytest

from connect_labs.testing.shard import assign, parse


def test_every_file_lands_in_exactly_one_shard_and_the_loads_are_even():
    counts = {f"connect_labs/app{i}/tests/test_{i}.py": (i * 37) % 50 + 1 for i in range(300)}
    owner = assign(counts, 3)
    assert set(owner) == set(counts)
    assert set(owner.values()) == {1, 2, 3}
    loads = [sum(c for f, c in counts.items() if owner[f] == k) for k in (1, 2, 3)]
    assert max(loads) - min(loads) <= max(counts.values())


def test_the_deal_does_not_depend_on_collection_order():
    counts = {"b.py": 5, "a.py": 5, "c.py": 9, "d.py": 1}
    reordered = dict(reversed(list(counts.items())))
    assert assign(counts, 2) == assign(reordered, 2)


@pytest.mark.parametrize("spec", ["0/3", "4/3", "3", "a/b", ""])
def test_a_spec_it_cannot_read_is_refused_rather_than_ignored(spec):
    with pytest.raises(ValueError):
        parse(spec)


def test_a_spec_with_spaces_is_read():
    assert parse(" 2 / 3 ") == (2, 3)

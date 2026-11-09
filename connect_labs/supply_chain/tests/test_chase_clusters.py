"""A run of the same kind of chase line reads once, with a count.

THIS REPOSITORY IS PUBLIC. Every organisation, product and figure here is invented.
"""

from connect_labs.supply_chain.portfolio import chase


def _row(kind, ask, days=None, audience="internal"):
    return {
        "kind": kind,
        "audience": audience,
        "who": "",
        "what": ask,
        "ask": ask,
        "about": "",
        "amount": "",
        "hint_term": "",
        "gap": None,
        "days": days,
        "age_text": "",
        "href": f"/x/{ask}",
        "program": "P",
        "program_id": 1,
    }


def test_sixteen_expiring_lots_are_one_line_with_a_count_and_the_lots_behind_it():
    rows = [_row("stock_expiring", f"lot {n} expires", days=n) for n in range(16)]
    rows.append(_row("catalogue_gap", "No ration table"))
    (group,) = chase.grouped(rows)
    heads = [i["ask"] for i in group["items"]]
    assert heads == ["Stock expiring soon or already expired: 16 lots", "No ration table"]
    cluster = group["items"][0]
    assert len(cluster["parts"]) == 16
    assert cluster["days"] == 15
    assert cluster["href"] == ""


def test_two_of_a_kind_stay_as_the_lines_they_are():
    rows = [_row("stock_expiring", "lot a expires"), _row("stock_expiring", "lot b expires")]
    (group,) = chase.grouped(rows)
    assert [i["ask"] for i in group["items"]] == ["lot a expires", "lot b expires"]

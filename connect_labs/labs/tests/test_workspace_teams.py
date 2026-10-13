"""Which teams a Connect organisation tree puts someone on (labs.workspaces).

Pure functions over the tree Connect hands labs at sign-in -- no database. The
organisations and program ids are made up.
"""

from connect_labs.labs.workspaces import delivering_orgs, delivering_teams

LINKED = [9101, 9102]


def _tree(orgs, opps):
    return {
        "organizations": [{"slug": s, "name": n} for s, n in orgs],
        "opportunities": [{"id": i, "organization": o, "program": p} for i, (o, p) in enumerate(opps)],
    }


def test_a_member_of_the_delivering_org_is_on_its_team():
    tree = _tree([("riverbend-health", "Riverbend Health")], [("riverbend-health", 9101)])
    assert delivering_teams(tree, LINKED) == (("riverbend-health",), {"riverbend-health": "Riverbend Health"})


def test_each_delivering_org_is_a_team_in_tree_order():
    tree = _tree(
        [("hilltop-partners", "Hilltop Partners"), ("riverbend-health", "Riverbend Health")],
        [("riverbend-health", 9101), ("hilltop-partners", 9102), ("riverbend-health", 9102)],
    )
    teams, _ = delivering_teams(tree, LINKED)
    assert teams == ("riverbend-health", "hilltop-partners")


def test_seeing_a_partners_opportunity_is_not_delivering_it():
    # A Connect program manager sees every opportunity in its program.
    tree = _tree([("program-owner", "Program Owner")], [("riverbend-health", 9101), ("hilltop-partners", 9102)])
    assert delivering_teams(tree, LINKED) == ((), {})
    # ...which is exactly what lists the teams for a Program Manager.
    assert delivering_orgs(tree, LINKED) == {
        "riverbend-health": "riverbend-health",
        "hilltop-partners": "hilltop-partners",
    }


def test_unlinked_programs_labs_only_entries_and_empty_input_give_no_team():
    tree = _tree([("riverbend-health", "Riverbend Health")], [("riverbend-health", 9199)])
    assert delivering_teams(tree, LINKED) == ((), {})
    tree["opportunities"].append({"id": 7, "organization": "riverbend-health", "program": 9101, "labs_only": True})
    assert delivering_teams(tree, LINKED) == ((), {})
    assert delivering_teams(None, LINKED) == ((), {})
    assert delivering_teams(_tree([("a", "A")], [("a", 9101)]), []) == ((), {})


def test_a_program_id_given_as_a_string_still_matches():
    tree = _tree([("riverbend-health", "Riverbend Health")], [("riverbend-health", "9101")])
    assert delivering_teams(tree, LINKED)[0] == ("riverbend-health",)

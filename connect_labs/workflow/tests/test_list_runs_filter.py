"""Listing one workflow's runs filters on the SERVER, not after downloading them all."""

from types import SimpleNamespace

from connect_labs.workflow.data_access import WorkflowDataAccess


def _run(rid, definition_id):
    return SimpleNamespace(id=rid, data={"definition_id": definition_id})


class _Labs:
    def __init__(self, runs, refuse=False):
        self.runs, self.refuse, self.calls = runs, refuse, []

    def get_records(self, **kwargs):
        self.calls.append(kwargs)
        wanted = kwargs.get("definition_id__iexact")
        if wanted is not None and self.refuse:
            raise RuntimeError("unknown lookup")
        return [r for r in self.runs if wanted is None or str(r.data["definition_id"]) == wanted]


def _wda(labs):
    wda = WorkflowDataAccess(access_token="tok", opportunity_id=523)
    wda.labs_api = labs
    return wda


def test_one_workflows_runs_are_filtered_by_the_server():
    labs = _Labs([_run(1, 19778), _run(2, 21115), _run(3, 19778)])
    assert [r.id for r in _wda(labs).list_runs(19778)] == [1, 3]
    assert labs.calls[0]["definition_id__iexact"] == "19778"
    assert len(labs.calls) == 1


def test_a_refused_lookup_falls_back_to_the_full_list_filtered_here():
    labs = _Labs([_run(1, 19778), _run(2, 21115)], refuse=True)
    assert [r.id for r in _wda(labs).list_runs(19778)] == [1]
    assert "definition_id__iexact" not in labs.calls[-1]


def test_listing_every_run_sends_no_filter():
    labs = _Labs([_run(1, 19778), _run(2, 21115)])
    assert [r.id for r in _wda(labs).list_runs()] == [1, 2]
    assert "definition_id__iexact" not in labs.calls[0]

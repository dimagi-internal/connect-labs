from connect_labs.audit.data_access import filter_out_prior_audited


def test_drops_audited_images_and_counts_them():
    all_visit_images = {
        "111": [{"blob_id": "b1"}, {"blob_id": "b2"}],
        "222": [{"blob_id": "b3"}],
    }
    prior_index = {"111:b1": {"result": "pass"}, "222:b3": {"result": "fail"}}
    filtered, excluded = filter_out_prior_audited(all_visit_images, prior_index)
    assert excluded == 2
    assert filtered == {"111": [{"blob_id": "b2"}]}  # visit 222 emptied and dropped


def test_no_index_keeps_everything():
    all_visit_images = {"111": [{"blob_id": "b1"}]}
    filtered, excluded = filter_out_prior_audited(all_visit_images, {})
    assert excluded == 0
    assert filtered == {"111": [{"blob_id": "b1"}]}

from connect_labs.audit.data_access import drop_images_shared_across_visits


def test_shared_blob_kept_on_lowest_visit_only():
    # One RUTF Screening form -> two visits (CHC Distribution + SAM enrollment),
    # both reporting the form's single MUAC photo.
    all_visit_images = {
        "1969769": [{"blob_id": "muac"}],
        "1969768": [{"blob_id": "muac"}],
    }
    deduped, dropped = drop_images_shared_across_visits(all_visit_images)
    assert dropped == 1
    assert deduped == {"1969768": [{"blob_id": "muac"}], "1969769": []}


def test_only_shared_blobs_are_dropped():
    all_visit_images = {
        "10": [{"blob_id": "a"}, {"blob_id": "b"}],
        "11": [{"blob_id": "a"}, {"blob_id": "c"}],
        "12": [{"blob_id": "d"}],
    }
    deduped, dropped = drop_images_shared_across_visits(all_visit_images)
    assert dropped == 1
    assert deduped == {
        "10": [{"blob_id": "a"}, {"blob_id": "b"}],
        "11": [{"blob_id": "c"}],
        "12": [{"blob_id": "d"}],
    }


def test_visit_ids_ordered_numerically_not_lexically():
    deduped, _ = drop_images_shared_across_visits({"100": [{"blob_id": "x"}], "99": [{"blob_id": "x"}]})
    assert deduped == {"99": [{"blob_id": "x"}], "100": []}


def test_images_without_blob_id_are_left_alone():
    all_visit_images = {"1": [{"blob_id": ""}], "2": [{"blob_id": ""}]}
    deduped, dropped = drop_images_shared_across_visits(all_visit_images)
    assert dropped == 0
    assert deduped == all_visit_images

import random

from commcare_connect.labs.synthetic.generator.images import assign_visit_images
from commcare_connect.labs.synthetic.generator.manifest import ImageConfig


def test_assign_images_adds_blob_ids():
    visits = [
        {"id": "v1", "form_json": {"form": {"case": {"update": {"soliciter_muac_cm": "14.2"}}}}, "images": []},
        {"id": "v2", "form_json": {"form": {"case": {"update": {"soliciter_muac_cm": "13.1"}}}}, "images": []},
        {"id": "v3", "form_json": {"form": {}}, "images": []},
    ]
    config = ImageConfig(stock_image_count=5, probability=1.0)
    rng = random.Random(42)
    assign_visit_images(visits, config, rng)
    assert len(visits[0]["images"]) == 1
    assert visits[0]["images"][0]["blob_id"].startswith("synth-muac-")
    assert len(visits[1]["images"]) == 1
    assert len(visits[2]["images"]) == 0


def test_assign_images_respects_probability():
    visits = [
        {"id": f"v{i}", "form_json": {"form": {"case": {"update": {"soliciter_muac_cm": "14.0"}}}}, "images": []}
        for i in range(100)
    ]
    config = ImageConfig(stock_image_count=5, probability=0.0)
    rng = random.Random(42)
    assign_visit_images(visits, config, rng)
    assert all(len(v["images"]) == 0 for v in visits)


def test_assign_images_sets_form_json_photo_path():
    visits = [
        {"id": "v1", "form_json": {"form": {"case": {"update": {"soliciter_muac_cm": "14.2"}}}}, "images": []},
    ]
    config = ImageConfig(
        question_path="form.muac_group.muac_display_group_1.muac_photo",
        stock_image_count=5,
        probability=1.0,
    )
    rng = random.Random(42)
    assign_visit_images(visits, config, rng)
    fj = visits[0]["form_json"]
    photo_val = fj["form"]["muac_group"]["muac_display_group_1"]["muac_photo"]
    assert photo_val == visits[0]["images"][0]["name"]


def test_assign_images_round_robin_stock():
    visits = [
        {"id": f"v{i}", "form_json": {"form": {"case": {"update": {"soliciter_muac_cm": "14.0"}}}}, "images": []}
        for i in range(10)
    ]
    config = ImageConfig(stock_image_count=3, probability=1.0)
    rng = random.Random(42)
    assign_visit_images(visits, config, rng)
    blob_ids = [v["images"][0]["blob_id"] for v in visits]
    for i, bid in enumerate(blob_ids):
        expected_num = (i % 3) + 1
        assert bid == f"synth-muac-{expected_num:03d}"

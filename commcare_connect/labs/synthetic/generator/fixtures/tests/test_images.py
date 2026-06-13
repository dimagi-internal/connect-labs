import random

from commcare_connect.labs.synthetic.generator.fixtures.images import assign_visit_images
from commcare_connect.labs.synthetic.generator.fixtures.manifest import ImageConfig


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


def _muac_visit(idx, username):
    return {
        "id": f"v{idx}",
        "username": username,
        "form_json": {"form": {"case": {"update": {"soliciter_muac_cm": "14.0"}}}},
        "images": [],
    }


def test_pool_mode_targets_only_flagged_flw():
    # 4 FLWs, 20 visits each. Only Amina has bad_rate=1.0; everyone else stays clean.
    visits = []
    flws = ["amina", "bola", "chioma", "danjuma"]
    for w in flws:
        for i in range(20):
            visits.append(_muac_visit(f"{w}_{i}", w))
    config = ImageConfig(
        good_image_count=8,
        bad_image_count=13,
        default_bad_rate=0.0,
        flw_bad_rates={"amina": 1.0},
        probability=1.0,
    )
    rng = random.Random(0)
    assign_visit_images(visits, config, rng)
    per_flw = {w: {"good": 0, "bad": 0} for w in flws}
    for v in visits:
        bid = v["images"][0]["blob_id"]
        bucket = "bad" if "-bad-" in bid else "good"
        per_flw[v["username"]][bucket] += 1
    assert per_flw["amina"] == {"good": 0, "bad": 20}
    for w in ("bola", "chioma", "danjuma"):
        assert per_flw[w] == {"good": 20, "bad": 0}


def test_pool_mode_round_robins_within_each_pool():
    # 30 amina visits at bad_rate=1.0 should cycle synth-muac-bad-001..013 then wrap.
    visits = [_muac_visit(i, "amina") for i in range(30)]
    config = ImageConfig(
        good_image_count=8,
        bad_image_count=13,
        flw_bad_rates={"amina": 1.0},
        probability=1.0,
    )
    rng = random.Random(0)
    assign_visit_images(visits, config, rng)
    expected = [f"synth-muac-bad-{(i % 13) + 1:03d}" for i in range(30)]
    assert [v["images"][0]["blob_id"] for v in visits] == expected


def test_pool_mode_partial_bad_rate_mixes():
    # 100 visits, bad_rate=0.3 → roughly 30 bad, 70 good. RNG-stable for seed=42.
    visits = [_muac_visit(i, "amina") for i in range(100)]
    config = ImageConfig(
        good_image_count=8,
        bad_image_count=13,
        flw_bad_rates={"amina": 0.3},
        probability=1.0,
    )
    rng = random.Random(42)
    assign_visit_images(visits, config, rng)
    bad_count = sum("-bad-" in v["images"][0]["blob_id"] for v in visits)
    assert 20 <= bad_count <= 40, f"bad_count={bad_count} outside expected 20-40 range"


def test_legacy_mode_when_good_count_unset():
    # If good_image_count is not set, behavior matches the pre-pool default.
    visits = [_muac_visit(i, "amina") for i in range(5)]
    config = ImageConfig(stock_image_count=3, probability=1.0)
    rng = random.Random(0)
    assign_visit_images(visits, config, rng)
    for v in visits:
        bid = v["images"][0]["blob_id"]
        # Legacy ids never contain "good" or "bad" segments.
        assert "-good-" not in bid and "-bad-" not in bid
        assert bid.startswith("synth-muac-")

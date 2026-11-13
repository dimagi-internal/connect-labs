from connect_labs.effectiveness import engine, interventions


def test_a_preset_that_follows_a_model_is_priced_by_that_model():
    followed = [i for i in interventions.all_interventions() if i.model]
    assert {i.slug for i in followed} == {"kmc", "ors", "ors_annual"}
    for i in followed:
        assert i.unit_cost_usd == engine.defaults(i.model)["cost"], i.slug


def test_the_ors_presets_no_longer_disagree_with_the_chc_model():
    assert interventions.get("ors").unit_cost_usd == interventions.get("ors_annual").unit_cost_usd == 1.8

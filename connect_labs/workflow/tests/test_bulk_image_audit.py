def test_render_code_includes_all_three_ai_classifiers():
    from connect_labs.workflow.templates import get_template

    rc = get_template("bulk_image_audit")["render_code"]
    assert "'MUAC OverZoom'" in rc
    assert "'MUAC Mismatch'" in rc
    assert "'KMC Scale Comparison'" in rc
    assert "muac_match" in rc
    assert "scale_validation" in rc
    assert "muac_overzoom" in rc


def test_render_code_uses_checkboxes_not_a_dropdown_for_ai_agents():
    from connect_labs.workflow.templates import get_template

    rc = get_template("bulk_image_audit")["render_code"]
    assert "selectedAiAgents" in rc
    assert "toggleAiAgent" in rc
    # The old single-select dropdown is gone.
    assert "None – Skip AI review" not in rc
    assert "setSelectedAiAgent(" not in rc


def test_render_code_builds_image_audits_not_legacy_ai_agent_id():
    from connect_labs.workflow.templates import get_template

    rc = get_template("bulk_image_audit")["render_code"]
    assert "image_audits: imageAudits" in rc
    assert "reviewersForType" in rc
    # aiAutoApplyActionsByAgent is scoped per-agent to avoid action-key
    # collisions across agents (e.g. scale_validation and muac_match both
    # use "fail_unmatched").
    assert "aiAutoApplyActionsByAgent" in rc


def test_render_code_scopes_reviewers_by_required_image_type():
    from connect_labs.workflow.templates import get_template

    rc = get_template("bulk_image_audit")["render_code"]
    assert "requiresImageType" in rc
    assert "/weight/i.test(t.id)" in rc
    assert "/muac/i.test(t.id)" in rc

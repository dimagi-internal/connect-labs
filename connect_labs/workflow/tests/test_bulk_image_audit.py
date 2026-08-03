"""Tests for the Bulk Image Audit workflow's AI Review Agent checkboxes.

There is no JS/Babel test runner in this repo, so these are Python-level
substring assertions against the rendered JSX source string (`render_code`).
They pin that specific tokens/patterns are PRESENT (or absent) -- they cannot
verify runtime behavior (e.g. that a checkbox's onChange actually fires the
right state update), so a green run here is a smoke check, not proof the
logic is wired correctly end to end.
"""


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
    # The legacy single-agent keys must not ride along in the same call --
    # a future edit re-adding both old and new keys side by side would
    # double-send AI config to the backend.
    create_audit_call = rc.split("actions.createAudit({")[1].split("});")[0]
    assert "ai_agent_id" not in create_audit_call
    assert "ai_auto_apply_actions" not in create_audit_call


def test_render_code_scopes_reviewers_by_a_single_shared_predicate():
    """agentAppliesToType is the one place image-type applicability is
    decided -- the cleanup effect, the payload builder, and the checkbox
    render all call it rather than each re-deriving weight/muac matching
    independently (which previously risked drifting out of sync)."""
    from connect_labs.workflow.templates import get_template

    rc = get_template("bulk_image_audit")["render_code"]
    assert "const agentAppliesToType = (agentId, typeId) =>" in rc
    # 3 call sites: the cleanup effect, reviewersForType, and the render's agentOpts.
    assert rc.count("agentAppliesToType(") >= 3
    assert "/weight/i.test(typeId)" in rc
    assert "/muac/i.test(typeId)" in rc


def test_render_code_wires_comparison_field_for_reading_dependent_agents():
    """scale_validation and muac_match default to requires_reading=True with
    no fallback -- without a comparison_field, every image assigned to them
    is silently skipped server-side. Both must get a config.comparison_field
    using the same known CommCare question paths weekly_dual_track_audit.py
    hardcodes for the same form structure."""
    from connect_labs.workflow.templates import get_template

    rc = get_template("bulk_image_audit")["render_code"]
    assert "MUAC_READING_FIELD" in rc
    assert "muac_group/muac_display_group_2/muac_colour_display/soliciter_muac_cm" in rc
    assert "KMC_WEIGHT_READING_FIELD" in rc
    assert "'child_weight_visit'" in rc
    assert "comparisonField:" in rc
    assert "config: { comparison_field: comparisonField }" in rc


def test_render_code_reads_legacy_single_select_config_forward():
    """A not-yet-created run configured under the old single-select code
    (instance.state.config.ai_agent_id / ai_auto_apply_actions) must still
    hydrate correctly into the new array/dict shape on reopen."""
    from connect_labs.workflow.templates import get_template

    rc = get_template("bulk_image_audit")["render_code"]
    assert "instance.state?.config?.ai_agent_ids" in rc
    assert "instance.state?.config?.ai_agent_id) return [instance.state.config.ai_agent_id]" in rc
    assert "instance.state?.config?.ai_auto_apply_actions_by_agent" in rc
    assert "[instance.state.config.ai_agent_id]: instance.state.config.ai_auto_apply_actions" in rc


def test_render_code_prunes_stale_auto_apply_actions_on_deselect():
    """Unchecking an agent (directly, or automatically when its required
    image type is deselected) must drop its per-agent auto-apply choices --
    otherwise re-checking the same agent later in the same session silently
    resurrects a previously-ticked auto-tag with no action from the user."""
    from connect_labs.workflow.templates import get_template

    rc = get_template("bulk_image_audit")["render_code"]
    assert "setAiAutoApplyActionsByAgent(prev =>" in rc
    assert "stillApplies" in rc

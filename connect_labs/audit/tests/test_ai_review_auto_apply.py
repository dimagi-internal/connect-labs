"""
Unit tests for the per-action AI auto-apply selection logic.

`_build_ai_to_human_result` decides which AI verdicts get pre-tagged as a human
result at audit-creation time. The contract:

  - auto_apply_actions is None  → legacy behavior: honor the agent's
    `auto_apply_result` flag (all actions if True, none if False).
  - auto_apply_actions is a list → ONLY the named action keys auto-apply
    (an empty list means "flag only — nothing pre-tagged").
"""

from connect_labs.audit.tasks import _build_ai_to_human_result


class _FakeAgent:
    def __init__(self, result_actions, auto_apply_result=False):
        self.result_actions = result_actions
        self.auto_apply_result = auto_apply_result


# Mirrors the real muac_overzoom agent: a single fail action, auto-apply ON by default.
MUAC_ACTIONS = {
    "fail_overzoomed": {"ai_result": "no_match", "human_result": "fail", "button_label": "Fail all Hyperzoomed"},
}

# Mirrors the real scale_validation agent: two actions, auto-apply OFF by default.
SCALE_ACTIONS = {
    "pass_matched": {"ai_result": "match", "human_result": "pass", "button_label": "Pass all Matched"},
    "fail_unmatched": {"ai_result": "no_match", "human_result": "fail", "button_label": "Fail all Unmatched"},
}


class TestLegacyNoneBehavior:
    def test_none_with_auto_apply_true_applies_all_actions(self):
        agent = _FakeAgent(MUAC_ACTIONS, auto_apply_result=True)
        assert _build_ai_to_human_result(agent, None) == {"no_match": "fail"}

    def test_none_with_auto_apply_false_applies_nothing(self):
        agent = _FakeAgent(SCALE_ACTIONS, auto_apply_result=False)
        assert _build_ai_to_human_result(agent, None) == {}


class TestExplicitSelection:
    def test_empty_list_is_flag_only_even_when_agent_default_is_on(self):
        agent = _FakeAgent(MUAC_ACTIONS, auto_apply_result=True)
        assert _build_ai_to_human_result(agent, []) == {}

    def test_explicit_subset_applies_only_named_actions(self):
        agent = _FakeAgent(SCALE_ACTIONS, auto_apply_result=False)
        # Auto-pass matches, but leave unmatched for the human to decide.
        assert _build_ai_to_human_result(agent, ["pass_matched"]) == {"match": "pass"}

    def test_explicit_full_set_applies_all(self):
        agent = _FakeAgent(SCALE_ACTIONS, auto_apply_result=False)
        assert _build_ai_to_human_result(agent, ["pass_matched", "fail_unmatched"]) == {
            "match": "pass",
            "no_match": "fail",
        }

    def test_explicit_overrides_agent_default_off(self):
        # Agent defaults to flag-only, but the auditor opts a verdict into auto-fail.
        agent = _FakeAgent(MUAC_ACTIONS, auto_apply_result=False)
        assert _build_ai_to_human_result(agent, ["fail_overzoomed"]) == {"no_match": "fail"}

    def test_unknown_action_keys_are_ignored(self):
        agent = _FakeAgent(MUAC_ACTIONS, auto_apply_result=True)
        assert _build_ai_to_human_result(agent, ["does_not_exist"]) == {}


class TestRobustness:
    def test_missing_auto_apply_attr_defaults_off(self):
        agent = _FakeAgent(MUAC_ACTIONS)
        del agent.auto_apply_result
        assert _build_ai_to_human_result(agent, None) == {}

    def test_action_missing_keys_is_skipped(self):
        agent = _FakeAgent({"weird": {"ai_result": "no_match"}}, auto_apply_result=True)
        assert _build_ai_to_human_result(agent, None) == {}

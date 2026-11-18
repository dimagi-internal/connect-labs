"""What the canopy agent is told about the scenario pages' own actions (static/effectiveness/agent.js).

Canopy names each registered page action ``page_<name>``; the agent sees their descriptions and arguments, and this
says when to use them rather than the ``cea_*`` tools. Returned by ``cea_scenarios`` and ``cea_scenario_groups`` to a
caller that is canopy's panel (the only caller that has the actions), as the Data page's ``data_describe`` does.
``tests/test_agent_guidance.py`` holds this to the actions ``agent.js`` registers, so neither can drift from the other.
"""

PAGE_ACTIONS = """\
On the scenario pages (the Workspace /labs/cea/workspace/, Market size, and a group's page /labs/cea/groups/<id>/) you
can do what the person can do with the page's own buttons. Each runs in THEIR browser, as them, and the page shows it
happening. What they are looking at is in the page state you are given (the open scenario, its settings as link keys,
the map colouring, the selected place, the headline figures; on a group page, each scenario's result).
- Prefer an action to the cea_* tool when the person is on the page and the change is theirs to see: it changes
  what is on screen, which a saved row in the database does not. Use cea_scenario_save / cea_scenario_group_save when
  there is no page, or to build several scenarios in one go.
- page_changeScenario {changes}: change settings the way the controls do, by link key (models, target, min_place,
  max_countries, max_conflict, max_remote, trust, place_risk, grain, cost_basis, <model>_<dial>, i.<model>.<input>);
  null puts a setting back at the model's own. It changes only what is on screen until page_saveScenario.
- page_resetScenario, page_setMapColouring {lens: market|worth|plan}, page_selectPlace {place}.
- page_saveScenario {name, note, shared, group_id}, page_updateScenario {name, note, shared},
  page_moveScenarioToGroup {group_id | new_group_name}, page_createGroup {name, question, summary}.
- On the owner's group page: page_moveScenarioInGroup {scenario_id, position | direction},
  page_removeScenarioFromGroup, page_editGroup {name, question, summary, shared}, page_rebaselineGroup,
  page_compareAgainst, page_checkAgainstToday, page_highlightScenario.
- page_deleteScenario {scenario_id} and page_deleteGroup: the person is asked to confirm in their tab; a "no" comes
  back as ok: false. Say what you are about to delete before you call it.
- page_openScenario {scenario_id}, page_openGroup {group_id}: move the person to another page.
If an action throws, read the reason and do not retry the same input.
"""

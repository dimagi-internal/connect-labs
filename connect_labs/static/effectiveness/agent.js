/* What the canopy agent can do on the scenario pages: change the scenario, save it, put it in a group,
 * reorder and delete, and move the person around. Each action runs in the person's own tab, as them, and calls the very
 * functions the page's buttons call (`EffApi`, `EffScenarios.current`, `EffGroup`, `EffPage`), so there is one way to do each
 * thing and the page shows it happening. A delete asks the person in their tab first, exactly as its button does.
 * Registered once canopy's widget is ready (`canopy:ready`). The spec's `parameters` are what the agent is told. */
(function () {
  'use strict';

  var G = /\/groups\/(\d+)\/$/;

  function here() {
    return new URL(window.location.href);
  }
  function go(url) {
    window.location.assign(url);
    return { ok: true };
  }
  function id(v, what) {
    var n = Number(v);
    if (!Number.isInteger(n) || n <= 0)
      throw new Error(what + ' must be an id (a whole number above zero).');
    return n;
  }
  function onGroupPage() {
    if (!G.test(here().pathname) || !window.EffGroup)
      throw new Error('This is not a group page.');
    return window.EffGroup;
  }
  function onScenarioPage() {
    if (!window.EffPage) throw new Error('This page has no scenario open.');
    return window.EffPage;
  }
  function panel() {
    if (!window.EffScenarios || !window.EffScenarios.current)
      throw new Error('This page has no scenario panel.');
    return window.EffScenarios.current;
  }
  function text(sel, max) {
    var el = document.querySelector(sel);
    var t = el && !el.hidden ? el.innerText.trim() : '';
    return t.length > (max || 600) ? t.slice(0, max || 600) + '…' : t;
  }
  // A name for a scenario id, from the person's saved list (what the delete question is put in).
  function nameOf(scenarioId) {
    var card = document.querySelector(
      '[data-scenario="' + scenarioId + '"] h3',
    );
    if (card) return Promise.resolve(card.textContent.trim());
    return window.EffApi.list().then(function (l) {
      var all = l.mine.concat(l.shared);
      var hit = all.filter(function (s) {
        return s.id === scenarioId;
      })[0];
      if (!hit) throw new Error('No saved scenario has id ' + scenarioId + '.');
      return hit.name;
    });
  }
  var SCENARIO_ID = {
    type: 'object',
    properties: {
      scenario_id: { type: 'integer', description: 'The saved scenario’s id.' },
    },
    required: ['scenario_id'],
  };
  var NONE = { type: 'object', properties: {} };

  // What the person is looking at, lent to the agent the way the SDK intends: pulled when a conversation opens
  // (`provideContext`) and pushed whenever it changes (`setPageState`), so it is in the agent's context and no action has to
  // be called to read it.
  function pageState() {
    var m = G.exec(here().pathname);
    if (m) {
      return {
        page: 'group',
        group_id: Number(m[1]),
        status: text('#status', 300),
        scenarios: [].map.call(
          document.querySelectorAll('[data-testid="group-cards"] .sc'),
          function (c) {
            return {
              scenario_id: Number(c.dataset.scenario),
              name: c.querySelector('h3').textContent.trim(),
              result: text(
                '[data-scenario="' + c.dataset.scenario + '"] .big',
                200,
              ),
              reference: c.classList.contains('ref'),
            };
          },
        ),
      };
    }
    var P = window.EffPage;
    if (!P) return { page: here().pathname };
    var loaded = P.data.loaded;
    return {
      page: /workspace/.test(here().pathname) ? 'workspace' : 'market',
      saved_scenario: loaded
        ? {
            id: loaded.id,
            name: loaded.name,
            mine: loaded.mine,
            group: loaded.group && {
              id: loaded.group.id,
              name: loaded.group.name,
            },
            matches_saved: window.EffScenarios.same(
              P.scn.params(),
              loaded.params,
            ),
          }
        : null,
      settings: Object.fromEntries(P.scn.params().entries()),
      map_colouring: P.lens ? P.lens() : null,
      selected_place: P.selected ? P.selected() : null,
      figures: text('#tiles', 800),
      problem: text('#error', 300) || text('#notice', 300),
    };
  }

  var ACTIONS = {
    // ---- changing the scenario in the Workspace or Market size
    changeScenario: [
      function (a) {
        var P = onScenarioPage();
        if (!a.changes || typeof a.changes !== 'object')
          throw new Error('Give `changes`: an object of settings to change.');
        P.scn.apply(a.changes);
        return new Promise(function (resolve) {
          // The numbers follow a moment later; say what the page says about them.
          setTimeout(function () {
            resolve({
              ok: true,
              settings: Object.fromEntries(P.scn.params().entries()),
              problem: text('#error', 300) || text('#notice', 300),
            });
          }, 900);
        });
      },
      {
        description:
          'Change the open scenario’s settings in the page, as the person would with the controls; the map and figures follow. `changes` uses the link’s own keys (the same ones cea_scenario_save takes, spelt as the scenario link spells them): models (comma list: chc, kc, mbw), indicator, threshold, iso (comma list of country codes), bar, target (people), min_place, max_countries, max_conflict, max_remote, trust, place_risk (0 turns it off), grain (country or territory), cost_basis (model or observed), <model>_<dial> such as kc_reach or chc_extra (a dial is extra, effect or reach), and i.<model>.<input> for a model assumption. A value of null puts that setting back at the model’s own. This changes only what is on screen until saveScenario.',
        parameters: {
          type: 'object',
          properties: {
            changes: {
              type: 'object',
              description:
                'Settings to change, by link key, e.g. {"target": 1000000, "kc_reach": 80, "max_remote": null}.',
            },
          },
          required: ['changes'],
        },
      },
    ],
    resetScenario: [
      function () {
        onScenarioPage().scn.reset();
        return { ok: true };
      },
      {
        description:
          'Put every dial and assumption back at the models as published (the places and the target stay).',
        parameters: NONE,
      },
    ],
    setMapColouring: [
      function (a) {
        var P = onScenarioPage();
        if (!P.setLens) throw new Error('This page has no map.');
        P.setLens(a.lens);
        return { ok: true, map_colouring: P.lens() };
      },
      {
        description:
          'Colour the Workspace map by: market (cost to reach everyone), worth (cost-effectiveness, as a multiple of the benchmark) or plan (only the places in the plan). It changes the map only, not the numbers.',
        parameters: {
          type: 'object',
          properties: {
            lens: { type: 'string', enum: ['market', 'worth', 'plan'] },
          },
          required: ['lens'],
        },
      },
    ],
    selectPlace: [
      function (a) {
        var P = onScenarioPage();
        if (!P.selectPlace) throw new Error('This page has no map.');
        P.selectPlace(a.place || null);
        return { ok: true, selected_place: P.selected() };
      },
      {
        description:
          'Select a place on the Workspace map by name, to show its detail. Select the same place again, or give none, to clear it.',
        parameters: {
          type: 'object',
          properties: { place: { type: 'string' } },
        },
      },
    ],
    // ---- saving, grouping and deleting
    saveScenario: [
      function (a) {
        var fields = { name: a.name, note: a.note, shared: a.shared };
        if ('group_id' in a) fields.group = a.group_id;
        return panel()
          .save(fields)
          .then(function (j) {
            return j
              ? { ok: true, scenario_id: j.id, name: j.name }
              : {
                  ok: false,
                  note: 'Not saved: the name is taken and the person declined to replace it, or the server refused.',
                };
          });
      },
      {
        description:
          'Save the scenario on screen under a name (the settings as they are now), as the Save button does. A name the person already uses asks them, in their tab, whether to replace it. Give `group_id` (one of the person’s own groups; null for none) to put it in a group too, last in its order.',
        parameters: {
          type: 'object',
          properties: {
            name: { type: 'string' },
            note: { type: 'string' },
            shared: { type: 'boolean' },
            group_id: { type: ['integer', 'null'] },
          },
          required: ['name'],
        },
      },
    ],
    updateScenario: [
      function (a) {
        var patch = {};
        ['name', 'note', 'shared'].forEach(function (k) {
          if (k in a) patch[k] = a[k];
        });
        return panel()
          .update(patch)
          .then(function (j) {
            return { ok: true, name: j.name, shared: j.shared };
          });
      },
      {
        description:
          'Rename the open saved scenario, change its note, or share it with everyone on Labs (or make it private). Only the person’s own saved scenario.',
        parameters: {
          type: 'object',
          properties: {
            name: { type: 'string' },
            note: { type: 'string' },
            shared: { type: 'boolean' },
          },
        },
      },
    ],
    moveScenarioToGroup: [
      function (a) {
        var C = panel();
        if (a.new_group_name) return C.newGroup(a.new_group_name).then(ack);
        if (!('group_id' in a))
          throw new Error(
            'Give group_id (null for no group) or new_group_name.',
          );
        return C.moveToGroup(
          a.group_id === null ? null : id(a.group_id, 'group_id'),
        ).then(ack);
      },
      {
        description:
          'Put the open saved scenario in one of the person’s own groups (last in its order), in a new group, or take it out of its group (group_id null). The page reopens the scenario in its new place.',
        parameters: {
          type: 'object',
          properties: {
            group_id: { type: ['integer', 'null'] },
            new_group_name: { type: 'string' },
          },
        },
      },
    ],
    createGroup: [
      function (a) {
        return window.EffApi.createGroup({
          name: a.name,
          question: a.question,
          summary: a.summary,
          shared: a.shared,
        }).then(function (g) {
          return { ok: true, group_id: g.id, name: g.name };
        });
      },
      {
        description:
          'Make a scenario group: scenarios kept together about one question. `question` is what we are trying to think about; `summary` the living account of what they showed.',
        parameters: {
          type: 'object',
          properties: {
            name: { type: 'string' },
            question: { type: 'string' },
            summary: { type: 'string' },
            shared: { type: 'boolean' },
          },
          required: ['name'],
        },
      },
    ],
    deleteScenario: [
      function (a) {
        var sid = id(a.scenario_id, 'scenario_id');
        return nameOf(sid).then(function (name) {
          var run = G.test(here().pathname)
            ? onGroupPage().remove(sid, name)
            : panel().remove({ id: sid, name: name });
          return run.then(function (gone) {
            return gone
              ? { ok: true, deleted: name }
              : {
                  ok: false,
                  note: 'The person declined, so nothing was deleted.',
                };
          });
        });
      },
      {
        description:
          'Delete one of the person’s saved scenarios. The person is asked to confirm in their tab; it cannot be undone.',
        parameters: SCENARIO_ID,
      },
    ],
    // ---- a group page
    moveScenarioInGroup: [
      function (a) {
        return onGroupPage()
          .move(id(a.scenario_id, 'scenario_id'), a.position || a.direction)
          .then(ack);
      },
      {
        description:
          'On the owner’s group page, move a scenario within the group: to a 1-based `position`, or one place `earlier` or `later`.',
        parameters: {
          type: 'object',
          properties: {
            scenario_id: { type: 'integer' },
            position: { type: 'integer', minimum: 1 },
            direction: { type: 'string', enum: ['earlier', 'later'] },
          },
          required: ['scenario_id'],
        },
      },
    ],
    removeScenarioFromGroup: [
      function (a) {
        return onGroupPage()
          .takeOut(id(a.scenario_id, 'scenario_id'))
          .then(ack);
      },
      {
        description:
          'On the owner’s group page, take a scenario out of the group. The scenario is kept.',
        parameters: SCENARIO_ID,
      },
    ],
    editGroup: [
      function (a) {
        var patch = {};
        ['name', 'question', 'summary', 'shared'].forEach(function (k) {
          if (k in a) patch[k] = a[k];
        });
        return onGroupPage().saveText(patch).then(ack);
      },
      {
        description:
          'On the owner’s group page, change the group’s name, question, summary (lines starting `## ` open sections, `- ` makes a list) or sharing.',
        parameters: {
          type: 'object',
          properties: {
            name: { type: 'string' },
            question: { type: 'string' },
            summary: { type: 'string' },
            shared: { type: 'boolean' },
          },
        },
      },
    ],
    rebaselineGroup: [
      function () {
        return onGroupPage().rebaseline().then(ack);
      },
      {
        description:
          'On the owner’s group page, make what each scenario gives today the figure later runs are compared with (about 30 seconds).',
        parameters: NONE,
      },
    ],
    deleteGroup: [
      function () {
        return onGroupPage()
          .removeGroup()
          .then(function (gone) {
            return gone
              ? { ok: true }
              : {
                  ok: false,
                  note: 'The person declined, so nothing was deleted.',
                };
          });
      },
      {
        description:
          'Delete this group (the owner’s). Its scenarios are kept, ungrouped. The person is asked to confirm in their tab.',
        parameters: NONE,
      },
    ],
    // ---- moving around
    openScenario: [
      function (a) {
        return go(
          '/labs/cea/workspace/?scenario=' + id(a.scenario_id, 'scenario_id'),
        );
      },
      {
        description:
          'Open a saved scenario on the Workspace, in the person’s tab.',
        parameters: SCENARIO_ID,
      },
    ],
    openGroup: [
      function (a) {
        return go('/labs/cea/groups/' + id(a.group_id, 'group_id') + '/');
      },
      {
        description: 'Open a scenario group’s comparison page.',
        parameters: {
          type: 'object',
          properties: { group_id: { type: 'integer' } },
          required: ['group_id'],
        },
      },
    ],
    compareAgainst: [
      function (a) {
        onGroupPage();
        var u = here();
        u.searchParams.set('ref', id(a.scenario_id, 'scenario_id'));
        return go(u.toString());
      },
      {
        description:
          'On a group page, make this scenario the reference the others are compared against (the cards, bars and table all follow).',
        parameters: SCENARIO_ID,
      },
    ],
    checkAgainstToday: [
      function () {
        onGroupPage();
        var u = here();
        u.searchParams.set('live', '1');
        return go(u.toString());
      },
      {
        description:
          'On a group page, recompute every scenario with today’s data and models (takes about 30 seconds) and show what has moved since it was baselined.',
        parameters: NONE,
      },
    ],
    highlightScenario: [
      function (a) {
        var sid = id(a.scenario_id, 'scenario_id');
        var card = document.querySelector('[data-scenario="' + sid + '"]');
        if (!card) throw new Error('That scenario is not on this page.');
        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        card.style.outline = '3px solid #4338ca';
        setTimeout(function () {
          card.style.outline = '';
        }, 4000);
        return { ok: true };
      },
      {
        description:
          'On a group page, scroll to a scenario’s card and outline it, to point at it while explaining.',
        parameters: SCENARIO_ID,
      },
    ],
  };

  function ack() {
    return { ok: true };
  }

  // For tests and the page harness: call an action as canopy would.
  window.CEA_AGENT_ACTIONS = Object.fromEntries(
    Object.entries(ACTIONS).map(function (e) {
      return [e[0], e[1][0]];
    }),
  );
  window.CEA_PAGE_STATE = pageState;
  window.CEA_AGENT_SPECS = Object.fromEntries(
    Object.entries(ACTIONS).map(function (e) {
      return [e[0], e[1][1]];
    }),
  );

  function register(host) {
    if (!host || !host.widget || !host.widget.registerAction) return;
    Object.keys(ACTIONS).forEach(function (name) {
      host.widget.registerAction(name, ACTIONS[name][0], ACTIONS[name][1]);
    });
    var w = host.widget;
    if (w.provideContext) w.provideContext(pageState);
    if (w.setPageState) {
      var timer = null;
      var push = function () {
        clearTimeout(timer);
        timer = setTimeout(function () {
          w.setPageState(pageState());
        }, 400);
      };
      push();
      if (window.EffPage) window.EffPage.scn.onChange(push);
    }
  }
  if (window.canopyHost) register(window.canopyHost);
  else
    document.addEventListener(
      'canopy:ready',
      function (e) {
        register(e.detail);
      },
      { once: true },
    );
})();

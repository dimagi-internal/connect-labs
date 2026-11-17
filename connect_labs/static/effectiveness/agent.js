/* What the canopy agent can do on the scenario pages, beyond reading them: move the person around. Each action runs in the
 * person's tab, as them, checks its input, and returns an acknowledgement only. Nothing here saves or changes a scenario:
 * the agent proposes in chat and the person clicks. Registered once canopy's widget is ready (`canopy:ready`). */
(function () {
  'use strict';

  function go(url) {
    window.location.assign(url);
    return { ok: true };
  }
  function num(v, what) {
    var n = Number(v);
    if (!Number.isInteger(n) || n <= 0)
      throw new Error(what + ' must be a scenario id.');
    return n;
  }
  function here() {
    return new URL(window.location.href);
  }

  var ACTIONS = {
    openScenario: [
      function (a) {
        return go(
          '/labs/cea/workspace/?scenario=' + num(a.scenario_id, 'scenario_id'),
        );
      },
      {
        description:
          'Open a saved scenario on the Workspace, in the person’s tab.',
        input_schema: {
          type: 'object',
          properties: {
            scenario_id: {
              type: 'integer',
              description: 'The saved scenario’s id.',
            },
          },
          required: ['scenario_id'],
        },
      },
    ],
    openGroup: [
      function (a) {
        return go('/labs/cea/groups/' + num(a.group_id, 'group_id') + '/');
      },
      {
        description: 'Open a scenario group’s comparison page.',
        input_schema: {
          type: 'object',
          properties: { group_id: { type: 'integer' } },
          required: ['group_id'],
        },
      },
    ],
    compareAgainst: [
      function (a) {
        var u = here();
        if (!/\/groups\/\d+\/$/.test(u.pathname))
          throw new Error('This is not a group page.');
        u.searchParams.set('ref', num(a.scenario_id, 'scenario_id'));
        return go(u.toString());
      },
      {
        description:
          'On a group page, make this scenario the reference the others are compared against (the cards, bars and table all follow).',
        input_schema: {
          type: 'object',
          properties: { scenario_id: { type: 'integer' } },
          required: ['scenario_id'],
        },
      },
    ],
    checkAgainstToday: [
      function () {
        var u = here();
        if (!/\/groups\/\d+\/$/.test(u.pathname))
          throw new Error('This is not a group page.');
        u.searchParams.set('live', '1');
        return go(u.toString());
      },
      {
        description:
          'On a group page, recompute every scenario with today’s data and models (takes about 30 seconds) and show what has moved since it was baselined.',
        input_schema: { type: 'object', properties: {} },
      },
    ],
    highlightScenario: [
      function (a) {
        var id = num(a.scenario_id, 'scenario_id');
        var card = document.querySelector('[data-scenario="' + id + '"]');
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
        input_schema: {
          type: 'object',
          properties: { scenario_id: { type: 'integer' } },
          required: ['scenario_id'],
        },
      },
    ],
  };

  window.CEA_AGENT_ACTIONS = Object.fromEntries(
    Object.entries(ACTIONS).map(function (e) {
      return [e[0], e[1][0]];
    }),
  );

  function register(host) {
    if (!host || !host.widget || !host.widget.registerAction) return;
    Object.keys(ACTIONS).forEach(function (name) {
      host.widget.registerAction(name, ACTIONS[name][0], ACTIONS[name][1]);
    });
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

// What the canopy agent is told it can do (./agent.js), and that each action calls what the page's own buttons call.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { readFileSync } from 'node:fs';

let page;
afterEach(() => {
  vi.useRealTimers();
});
beforeEach(() => {
  page = { pathname: '/labs/cea/workspace/', search: '' };
  globalThis.window = globalThis;
  window.location = {
    get href() {
      return 'https://labs.example' + page.pathname + page.search;
    },
    get pathname() {
      return page.pathname;
    },
    assign: vi.fn(),
  };
  globalThis.document = {
    addEventListener: () => {},
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  delete window.EffGroup;
  delete window.EffPage;
  delete window.EffScenarios;
  // eslint-disable-next-line no-eval
  eval(readFileSync(new URL('./agent.js', import.meta.url), 'utf8'));
});
const act = (name, args) => window.CEA_AGENT_ACTIONS[name](args || {});

describe('what the agent is told', () => {
  it('has a description for every action, and parameters in the key canopy reads', () => {
    for (const [name, spec] of Object.entries(window.CEA_AGENT_SPECS)) {
      expect(spec.description, name).toBeTruthy();
      expect(spec.parameters.type, name).toBe('object');
      expect(spec, name).not.toHaveProperty('input_schema');
    }
  });

  it('names a required argument where the handler needs one', () => {
    const S = window.CEA_AGENT_SPECS;
    expect(S.deleteScenario.parameters.required).toEqual(['scenario_id']);
    expect(S.changeScenario.parameters.required).toEqual(['changes']);
    expect(S.saveScenario.parameters.required).toEqual(['name']);
  });

  it('covers what a person can do: see, change, save, group, reorder, delete, move around', () => {
    expect(Object.keys(window.CEA_AGENT_ACTIONS).sort()).toEqual(
      [
        'changeScenario',
        'checkAgainstToday',
        'compareAgainst',
        'createGroup',
        'deleteGroup',
        'deleteScenario',
        'editGroup',
        'highlightScenario',
        'moveScenarioInGroup',
        'moveScenarioToGroup',
        'openGroup',
        'openScenario',
        'rebaselineGroup',
        'removeScenarioFromGroup',
        'resetScenario',
        'saveScenario',
        'selectPlace',
        'setMapColouring',
        'updateScenario',
      ].sort(),
    );
  });
});

describe('what the agent sees', () => {
  it('is lent as page state: pulled on open and pushed when the scenario changes', () => {
    vi.useFakeTimers();
    let ready;
    document.addEventListener = (name, fn) => {
      if (name === 'canopy:ready') ready = fn;
    };
    let onChange;
    window.EffPage = {
      data: { loaded: null },
      scn: {
        params: () => new URLSearchParams('models=kc'),
        onChange: (fn) => (onChange = fn),
        onSettled: () => {},
      },
    };
    // eslint-disable-next-line no-eval
    eval(readFileSync(new URL('./agent.js', import.meta.url), 'utf8'));
    const host = {
      widget: {
        registerAction: vi.fn(),
        provideContext: vi.fn(),
        setPageState: vi.fn(),
      },
    };
    ready({ detail: host });
    expect(host.widget.registerAction).toHaveBeenCalledTimes(
      Object.keys(window.CEA_AGENT_ACTIONS).length,
    );
    const lend = host.widget.provideContext.mock.calls[0][0];
    expect(lend()).toMatchObject({
      page: 'workspace',
      settings: { models: 'kc' },
    });
    vi.advanceTimersByTime(500);
    expect(host.widget.setPageState).toHaveBeenCalledTimes(1);
    onChange();
    onChange();
    vi.advanceTimersByTime(500);
    expect(host.widget.setPageState).toHaveBeenCalledTimes(2); // one push for a burst of changes
    vi.useRealTimers();
  });
});

describe('on the wrong page', () => {
  it('a group action says this is not a group page', () => {
    expect(() => act('rebaselineGroup')).toThrow(/not a group page/);
    expect(() => act('moveScenarioInGroup', { scenario_id: 1 })).toThrow(
      /not a group page/,
    );
  });

  it('a scenario action says no scenario is open', () => {
    expect(() => act('changeScenario', { changes: {} })).toThrow(
      /no scenario open/,
    );
    expect(() => act('saveScenario', { name: 'x' })).toThrow(
      /no scenario panel/,
    );
  });

  it('refuses an id that is not a whole number above zero', () => {
    expect(() => act('openScenario', { scenario_id: 'x' })).toThrow(
      /must be an id/,
    );
    expect(() => act('openGroup', { group_id: 0 })).toThrow(/must be an id/);
  });
});

describe('calling the page’s own operations', () => {
  it('changes the scenario through scn.apply and reports the settings', async () => {
    const apply = vi.fn();
    const settled = vi.fn(() => Promise.resolve({ ok: true }));
    document.querySelector = (sel) =>
      sel === '#tiles' ? { hidden: false, innerText: '$184M a year' } : null;
    window.EffPage = {
      scn: {
        apply,
        settled,
        params: () => new URLSearchParams('models=kc&target=5'),
      },
    };
    const got = await act('changeScenario', { changes: { target: 5 } });
    expect(apply).toHaveBeenCalledWith({ target: 5 });
    expect(settled).toHaveBeenCalled(); // it waits for the figures
    expect(got.settings).toEqual({ models: 'kc', target: '5' });
    expect(got.figures).toBe('$184M a year');
  });

  it('moves a scenario in a group by the group page’s own move', () => {
    page.pathname = '/labs/cea/groups/3/';
    window.EffGroup = { move: vi.fn(() => Promise.resolve()) };
    return act('moveScenarioInGroup', {
      scenario_id: 12,
      direction: 'later',
    }).then(() =>
      expect(window.EffGroup.move).toHaveBeenCalledWith(12, 'later'),
    );
  });

  it('deletes through the same call as the button, naming the scenario, and says when the person declines', async () => {
    page.pathname = '/labs/cea/groups/3/';
    document.querySelector = (sel) =>
      sel.includes('data-scenario="12"') ? { textContent: ' Twelve ' } : null;
    window.EffGroup = { remove: vi.fn(() => Promise.resolve(null)) };
    const got = await act('deleteScenario', { scenario_id: 12 });
    expect(window.EffGroup.remove).toHaveBeenCalledWith(12, 'Twelve');
    expect(got.ok).toBe(false);
  });

  it('sets the map colouring through the page', () => {
    const setLens = vi.fn();
    window.EffPage = { scn: {}, setLens, lens: () => 'worth' };
    expect(act('setMapColouring', { lens: 'worth' })).toEqual({
      ok: true,
      map_colouring: 'worth',
    });
    expect(setLens).toHaveBeenCalledWith('worth');
  });
});

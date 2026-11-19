// The scenario in the browser: it writes the link the server reads (effectiveness/tests/scenario_contract.json is the
// one definition of both), and the changes a person makes leave the link only when they differ from the model.
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { readFileSync } from 'node:fs';

function load(path) {
  // These files attach to window rather than exporting, as the page loads them.
  globalThis.window = globalThis.window || globalThis;
  // eslint-disable-next-line no-eval
  eval(readFileSync(new URL(path, import.meta.url), 'utf8'));
}
load('./scenario.js');
const contract = JSON.parse(
  readFileSync(
    new URL(
      '../../effectiveness/tests/scenario_contract.json',
      import.meta.url,
    ),
    'utf8',
  ),
);

const DIALS = [
  { key: 'extra', default: 0 },
  { key: 'effect', default: 100 },
  { key: 'reach', default: 100 },
];
const INDICATORS = [
  { key: 'all', label: 'Everywhere', threshold: null },
  { key: 'u5mr', label: 'U5MR', threshold: 80 },
  { key: 'nmr', label: 'NMR', threshold: 20 },
];

// Kangaroo Care starts at reaching half of a place's cases; the others at all of them.
const DIAL_DEFAULTS = {
  chc: { extra: 0, effect: 100, reach: 100 },
  kc: { extra: 0, effect: 100, reach: 50 },
  mbw: { extra: 0, effect: 100, reach: 100 },
};
const PLACE_RISK = {
  kc: {
    input: 'post_ref',
    measure: 'nmr',
    reference: 28.4,
    label: 'newborn mortality',
  },
};

// scenario.CAPS as the page is given it (only the keys matter to the scenario)
const CAPS = [{ key: 'max_conflict' }, { key: 'max_remote' }];

function data(state) {
  return {
    state,
    caps: CAPS,
    dials: DIALS,
    dial_defaults: DIAL_DEFAULTS,
    place_risk: PLACE_RISK,
    indicators: INDICATORS,
    bar: contract.default_bar,
  };
}
function fresh() {
  return window.EffScenario.create(
    data(JSON.parse(JSON.stringify(contract.state))),
  );
}
function blank() {
  return window.EffScenario.create(
    data({
      models: ['chc'],
      indicator: 'all',
      threshold: null,
      iso: '',
      bar: contract.default_bar,
      target: null,
      place_risk: true,
      grain: 'country',
      dials: JSON.parse(JSON.stringify(DIAL_DEFAULTS)),
      overrides: {},
      extras: {},
    }),
  );
}
const asObject = (p) => Object.fromEntries(p.entries());

describe('the link', () => {
  it('is the one the server reads, written from the state the server sent', () => {
    expect(asObject(fresh().params())).toEqual(contract.link);
  });

  it('is the bare page for the default scenario', () => {
    expect(asObject(blank().params())).toEqual({ models: 'chc' });
  });

  it('leaves out a dial that is back at its default', () => {
    const s = blank();
    s.setDial('chc', 'reach', 60);
    expect(asObject(s.params()).chc_reach).toBe('60');
    s.setDial('chc', 'reach', 100);
    expect(asObject(s.params())).toEqual({ models: 'chc' });
  });

  it("starts each programme's dials at its own default, so Kangaroo Care's half is not a change", () => {
    const s = blank();
    s.toggleModel('kc', true);
    expect(asObject(s.params())).toEqual({ models: 'chc,kc' });
    s.setDial('kc', 'reach', 100);
    expect(asObject(s.params()).kc_reach).toBe('100');
    s.setDial('kc', 'reach', 50);
    expect(asObject(s.params())).toEqual({ models: 'chc,kc' });
    s.setDial('chc', 'reach', 50);
    expect(asObject(s.params()).chc_reach).toBe('50');
  });

  it('writes a target, the risk switch and the grain only when they are not the default', () => {
    const s = blank();
    s.setTarget(1000000);
    s.setPlaceRisk(false);
    s.setGrain('territory');
    expect(asObject(s.params())).toEqual({
      models: 'chc',
      target: '1000000',
      place_risk: '0',
      grain: 'territory',
    });
    s.setTarget(null);
    s.setPlaceRisk(true);
    s.setGrain('country');
    expect(asObject(s.params())).toEqual({ models: 'chc' });
  });

  it('writes the limits and the trust only when they are set', () => {
    const s = blank();
    s.setLimit('min_place', 20000);
    s.setLimit('max_countries', 5);
    s.setTrust(60);
    expect(asObject(s.params())).toEqual({
      models: 'chc',
      min_place: '20000',
      max_countries: '5',
      trust: '60',
    });
    s.setLimit('min_place', null);
    s.setLimit('max_countries', 0);
    s.setTrust(100);
    expect(asObject(s.params())).toEqual({ models: 'chc' });
  });

  it('keeps trust between none and all of it', () => {
    const s = blank();
    s.setTrust(250);
    expect(s.state.trust).toBe(100);
    s.setTrust(-4);
    expect(s.state.trust).toBe(0);
  });

  it('counts believing the places less as a change, and a limit as none', () => {
    const s = blank();
    s.setLimit('min_place', 1000);
    expect(s.changes()).toEqual({ dials: 0, assumptions: 0 });
    s.setTrust(50);
    expect(s.changes()).toEqual({ dials: 0, assumptions: 1 });
  });

  it('takes a blank or zero target as no target', () => {
    const s = blank();
    s.setTarget(0);
    expect(s.state.target).toBeNull();
    s.setTarget(NaN);
    expect(s.state.target).toBeNull();
  });

  it('lets the extra cost go below zero for delivery cheaper than the model', () => {
    const s = blank();
    s.setDial('chc', 'extra', -10);
    expect(asObject(s.params()).chc_extra).toBe('-10');
  });

  it('leaves out an assumption set back to the model value', () => {
    const s = blank();
    s.setInput('chc', 'cost', 3, 1.4);
    expect(asObject(s.params())['i.chc.cost']).toBe('3');
    s.setInput('chc', 'cost', 1.4, 1.4);
    expect(asObject(s.params())).toEqual({ models: 'chc' });
  });

  it("keeps a page's own settings in the link but out of the question asked of the model", () => {
    const s = blank();
    s.ui.lens = 'worth';
    expect(s.link().get('lens')).toBe('worth');
    expect(s.params().has('lens')).toBe(false);
  });
});

describe('changing it', () => {
  let s;
  beforeEach(() => {
    s = blank();
  });

  it("takes the indicator's usual threshold, and none for everywhere", () => {
    s.setIndicator('u5mr');
    expect(s.state.threshold).toBe(80);
    s.setIndicator('all');
    expect(s.state.threshold).toBeNull();
    expect(asObject(s.params())).toEqual({ models: 'chc' });
  });

  it('never switches off the last programme', () => {
    s.toggleModel('chc', false);
    expect(s.state.models).toEqual(['chc']);
    s.toggleModel('kc', true);
    s.toggleModel('chc', false);
    expect(s.state.models).toEqual(['kc']);
  });

  it('counts changes only on the programmes in play', () => {
    s.setDial('chc', 'reach', 50);
    s.setDial('kc', 'reach', 50);
    s.setInput('kc', 'cost', 99, 60);
    expect(s.changes()).toEqual({ dials: 1, assumptions: 0 });
  });

  it('counts switching off place risk as a change only when a programme in play has it', () => {
    const s = blank();
    s.setPlaceRisk(false);
    expect(s.changes()).toEqual({ dials: 0, assumptions: 0 });
    s.toggleModel('kc', true);
    expect(s.changes()).toEqual({ dials: 0, assumptions: 1 });
  });

  it('keeps the target and the places through a reset, and puts Kangaroo Care back to half', () => {
    const s = blank();
    s.toggleModel('kc', true);
    s.setTarget(500000);
    s.setGrain('territory');
    s.setDial('kc', 'reach', 90);
    s.setPlaceRisk(false);
    s.reset();
    expect(s.state.dials.kc.reach).toBe(50);
    expect(s.state.place_risk).toBe(true);
    expect(s.state.target).toBe(500000);
    expect(s.state.grain).toBe('territory');
  });

  it('puts trust back to all of it on a reset and keeps the plan limits', () => {
    const s = blank();
    s.setTrust(40);
    s.setLimit('max_countries', 3);
    s.reset();
    expect(s.state.trust).toBe(100);
    expect(s.state.max_countries).toBe(3);
  });

  it('resets to the models as published and keeps the places', () => {
    s.setIndicator('u5mr');
    s.setDial('chc', 'reach', 50);
    s.setInput('chc', 'cost', 3, 1.4);
    s.set({ bar: 9 }, 'bar');
    s.reset();
    expect(s.changes()).toEqual({ dials: 0, assumptions: 0 });
    expect(s.state.bar).toBe(contract.default_bar);
    expect(s.state.indicator).toBe('u5mr');
  });

  it('says what changed, so a control redraws only when it must', () => {
    const kinds = [];
    s.onChange((_, kind) => kinds.push(kind));
    s.setDial('chc', 'reach', 50);
    s.toggleModel('kc', true);
    s.setUi('lens', 'worth');
    s.reset();
    expect(kinds).toEqual(['dial', 'models', 'ui', 'reset']);
  });
});

describe("apply: changing a scenario in the link's own words", () => {
  it('reads every key of the contract link into the state that writes it back', () => {
    const s = blank();
    s.apply(contract.link);
    expect(asObject(s.params())).toEqual(contract.link);
  });

  it('puts a key back at the model\'s own with null, an empty value or "default"', () => {
    const s = fresh();
    s.apply({
      chc_reach: null,
      target: '',
      place_risk: 'default',
      grain: 'default',
      'i.chc.cost': 'default',
      max_remote: 0,
    });
    const link = asObject(s.params());
    for (const k of [
      'chc_reach',
      'target',
      'place_risk',
      'grain',
      'i.chc.cost',
      'max_remote',
    ])
      expect(link).not.toHaveProperty(k);
  });

  it('redraws once, as Reset does, and says why a value is refused', () => {
    const s = blank();
    const kinds = [];
    s.onChange((_, kind) => kinds.push(kind));
    s.apply({ target: 500000, kc_reach: 80, models: 'chc,kc' });
    expect(kinds).toEqual(['reset']);
    expect(asObject(s.params())).toMatchObject({
      target: '500000',
      kc_reach: '80',
      models: 'chc,kc',
    });
    expect(() => s.apply({ target: 'lots' })).toThrow(/not a number/);
    expect(() => s.apply({ models: 'nope' })).toThrow(/at least one/);
  });

  it('takes an indicator with its own threshold, and an explicit threshold over it', () => {
    const s = blank();
    s.apply({ indicator: 'u5mr' });
    expect(asObject(s.params())).toMatchObject({
      indicator: 'u5mr',
      threshold: '80',
    });
    s.apply({ indicator: 'nmr', threshold: 25 });
    expect(asObject(s.params())).toMatchObject({
      indicator: 'nmr',
      threshold: '25',
    });
  });
});

describe('settled: waiting for the figures after a change', () => {
  it('resolves when the next answer has been drawn, and says when it failed', async () => {
    vi.useFakeTimers();
    const answers = [
      { ok: true, j: { cost: 0 } }, // the first answer, from follow()
      { ok: true, j: { cost: 1 } },
      { ok: false, j: { error: 'No.' } },
    ];
    globalThis.fetch = vi.fn(() => {
      const a = answers.shift();
      return Promise.resolve({ ok: a.ok, json: () => Promise.resolve(a.j) });
    });
    const s = blank();
    const drawn = [];
    s.follow(
      '/api',
      (r) => drawn.push(r),
      () => drawn.push('error'),
    );
    await vi.advanceTimersByTimeAsync(300); // the first answer, from follow()
    const next = s.settled();
    s.apply({ target: 5 });
    await vi.advanceTimersByTimeAsync(300);
    expect(await next).toEqual({ ok: true });
    expect(drawn).toEqual([{ cost: 0 }, { cost: 1 }]);
    const failed = s.settled();
    s.apply({ target: 6 });
    await vi.advanceTimersByTimeAsync(300);
    expect(await failed).toEqual({ ok: false, error: 'No.' });
    vi.useRealTimers();
  });

  it('gives up with a reason when no answer comes', async () => {
    vi.useFakeTimers();
    const s = blank();
    const waited = s.settled(1000);
    await vi.advanceTimersByTimeAsync(1100);
    expect(await waited).toMatchObject({ ok: false });
    vi.useRealTimers();
  });
});

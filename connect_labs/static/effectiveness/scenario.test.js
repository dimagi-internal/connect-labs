// The scenario in the browser: it writes the link the server reads (effectiveness/tests/scenario_contract.json is the
// one definition of both), and the changes a person makes leave the link only when they differ from the model.
import { describe, it, expect, beforeEach } from 'vitest';
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

function data(state) {
  return {
    state,
    dials: DIALS,
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
      dials: Object.fromEntries(
        ['chc', 'kc', 'mbw'].map((m) => [
          m,
          { extra: 0, effect: 100, reach: 100 },
        ]),
      ),
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

// The comparison behind "changed since it was saved": two links are one scenario when every key agrees.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';

globalThis.window = globalThis.window || globalThis;
for (const f of ['./ui.js', './scenarios.js']) {
  // eslint-disable-next-line no-eval
  eval(readFileSync(new URL(f, import.meta.url), 'utf8'));
}
const { same } = window.EffScenarios;

describe('same', () => {
  it('ignores the order keys are written in', () => {
    expect(same({ models: 'chc', bar: '8' }, { bar: '8', models: 'chc' })).toBe(
      true,
    );
  });

  it('reads a link and the object the server stored as the same thing', () => {
    expect(
      same(new URLSearchParams('models=chc&chc_reach=50'), {
        models: 'chc',
        chc_reach: '50',
      }),
    ).toBe(true);
  });

  it('compares numbers as numbers, so how a float is written does not matter', () => {
    expect(same({ chc_extra: '1e-7' }, { chc_extra: '1e-07' })).toBe(true);
    expect(same({ bar: '8.0' }, { bar: '8' })).toBe(true);
  });

  it('sees a changed value, an added key and a removed key', () => {
    expect(same({ models: 'chc', bar: '8' }, { models: 'chc', bar: '9' })).toBe(
      false,
    );
    expect(same({ models: 'chc' }, { models: 'chc', chc_reach: '50' })).toBe(
      false,
    );
    expect(same({ models: 'chc', chc_reach: '50' }, { models: 'chc' })).toBe(
      false,
    );
  });

  it('does not read text that looks empty as the number zero', () => {
    expect(same({ iso: '' }, { iso: '0' })).toBe(false);
    expect(same({ models: 'chc,kc' }, { models: 'chc,kc' })).toBe(true);
  });
});

// The pure parts of the results view: how a place is named and its risk explained, and how rows become places.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';

globalThis.window = globalThis.window || globalThis;
for (const f of ['./ui.js', './marketview.js']) {
  // eslint-disable-next-line no-eval
  eval(readFileSync(new URL(f, import.meta.url), 'utf8'));
}
const MV = window.EffMarketView;

const row = (o) => ({
  key: 'k',
  name: 'Kano',
  country: 'Nigeria',
  market_usd: 100,
  planned: 0,
  multiple: 10,
  clears_bar: true,
  partial: false,
  risk_basis: 'place',
  measure: 40,
  measure_country: 30,
  measure_how: 'Kano, igme_subnational 2021',
  far_from_country: false,
  ...o,
});

describe('mult and pct', () => {
  it('give one decimal, so 10.3x and 15.5x are not both "10x"', () => {
    expect(MV.mult(10.3)).toBe('10.3x');
    expect(MV.mult(15.47)).toBe('15.5x');
    expect(MV.mult(null)).toBe('–');
  });
  it('write a share as a percent', () => {
    expect(MV.pct(0.1768)).toBe('17.7%');
    expect(MV.pct(0.5, 0)).toBe('50%');
    expect(MV.pct(null)).toBe('–');
  });
});

describe('placeLabel', () => {
  it('names a territory with its country and a country once', () => {
    expect(MV.placeLabel({ name: 'Kano', country: 'Nigeria' })).toBe(
      'Kano, Nigeria',
    );
    expect(MV.placeLabel({ name: 'Nigeria', country: 'Nigeria' })).toBe(
      'Nigeria',
    );
  });
});

describe('riskTitle', () => {
  it("says whose figure it is, its country's beside it and where it came from", () => {
    const t = MV.riskTitle(row());
    expect(t).toContain('40.0');
    expect(t).toContain('its country: 30.0');
    expect(t).toContain('igme_subnational 2021');
    expect(t).not.toContain('check the source');
  });
  it("tells a reader to check the source when the figure is far from its country's", () => {
    expect(
      MV.riskTitle(row({ far_from_country: true, measure: 70 })),
    ).toContain('check the source');
  });
  it("says the model's own figure was used when the place had none", () => {
    expect(MV.riskTitle(row({ risk_basis: 'model' }))).toContain(
      "the model's own figure",
    );
  });
});

describe('byPlace', () => {
  const result = {
    models: [
      {
        model: 'chc',
        noun: 'children',
        rows: [row({ key: 'a', market_usd: 100, multiple: 8, planned: 5 })],
      },
      {
        model: 'kc',
        noun: 'newborns',
        rows: [
          row({
            key: 'a',
            market_usd: 50,
            multiple: 12,
            clears_bar: false,
            planned: 7,
          }),
          row({ key: 'b', market_usd: null, multiple: null }),
        ],
      },
    ],
  };
  it('adds a place up across programmes: market and plan together, the best multiple and who gives it', () => {
    const a = MV.byPlace(result).a;
    expect(a.market_usd).toBe(150);
    expect(a.planned).toBe(12);
    expect(a.best).toEqual({ model: 'kc', multiple: 12 });
    expect(a.clears).toBe(true);
    expect(a.rows).toHaveLength(2);
  });
  it('leaves a place with no figures without a market or a best', () => {
    const b = MV.byPlace(result).b;
    expect(b.market_usd).toBeNull();
    expect(b.best).toBeNull();
  });
});

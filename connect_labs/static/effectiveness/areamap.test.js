// The pure part of the map: colours and class edges for a set of numbers. The map itself needs a browser and a token.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';

globalThis.window = globalThis.window || globalThis;
for (const f of ['./ui.js', './areamap.js']) {
  // eslint-disable-next-line no-eval
  eval(readFileSync(new URL(f, import.meta.url), 'utf8'));
}
const { ramp, SEQUENTIAL } = window.EffMap;

describe('ramp', () => {
  it('gives each colour about the same number of countries, so a few giants do not wash out the rest', () => {
    const values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 1_000_000];
    const r = ramp(values);
    const used = values.map((v) => r.color(v));
    expect(new Set(used).size).toBe(SEQUENTIAL.length);
    expect(used.filter((c) => c === SEQUENTIAL[0]).length).toBe(2);
  });

  it('paints the largest with the darkest colour and the smallest with the palest', () => {
    const r = ramp([5, 10, 20, 40, 80]);
    expect(r.color(80)).toBe(SEQUENTIAL[SEQUENTIAL.length - 1]);
    expect(r.color(5)).toBe(SEQUENTIAL[0]);
  });

  it('leaves a country with no figure unpainted rather than zero', () => {
    const r = ramp([null, undefined, 3, 9]);
    expect(r.color(null)).toBeNull();
    expect(r.color(undefined)).toBeNull();
    expect(r.color(NaN)).toBeNull();
  });

  it('copes with one value, and with none', () => {
    expect(ramp([7]).color(7)).toBeTruthy();
    expect(ramp([]).classes).toEqual([]);
    expect(ramp([null]).classes).toEqual([]);
  });

  it('describes its classes low to high, from the smallest to the largest value', () => {
    const r = ramp([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect(r.classes[0].from).toBe(1);
    expect(r.classes[r.classes.length - 1].to).toBe(10);
    r.classes.forEach((c, i) => {
      if (i) expect(c.from).toBeGreaterThanOrEqual(r.classes[i - 1].from);
    });
  });
});

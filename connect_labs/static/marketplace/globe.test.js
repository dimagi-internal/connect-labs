/**
 * The marketplace globe draws one dot per organisation, and organisations
 * share cities -- seven in Maiduguri. At one coordinate they stack into a
 * single dot and the map shows half its organisations. These check the spread
 * that keeps every one visible.
 *
 * No DOM here: the file is evaluated with a stub window and document, and the
 * pure `spread` it exposes is tested directly.
 */
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const SRC = fs.readFileSync(path.join(here, 'globe.js'), 'utf8');

function load() {
  const window = {};
  const document = { readyState: 'complete', getElementById: () => null };
  new Function('window', 'document', SRC)(window, document);
  return window.MarketplaceGlobe;
}

const at = (name, lat, lon) => ({ name, lat, lon, precision: 'city' });

describe('spread', () => {
  const { spread } = load();

  it('leaves a point alone when nothing shares its spot', () => {
    const p = at('Solo', 9.0, 7.0);
    expect(spread([p], 2)).toEqual([p]);
  });

  it('gives every organisation in a shared city its own spot', () => {
    const seven = Array.from({ length: 7 }, (_, i) =>
      at(`Org ${i}`, 11.85, 13.16),
    );
    const out = spread(seven, 2);
    expect(out).toHaveLength(7);
    const spots = new Set(
      out.map((p) => `${p.lat.toFixed(5)},${p.lon.toFixed(5)}`),
    );
    expect(spots.size).toBe(7);
    expect(out.map((p) => p.name).sort()).toEqual(
      seven.map((p) => p.name).sort(),
    );
  });

  it('stays a few pixels from the city at any zoom', () => {
    const pair = [at('A', 0, 30), at('B', 0, 30)];
    const far = Math.abs(spread(pair, 1)[0].lon - 30);
    const near = Math.abs(spread(pair, 6)[0].lon - 30);
    expect(far).toBeGreaterThan(near * 30); // 2^5 = 32x closer at zoom 6
    expect(near).toBeLessThan(0.1); // 7px at zoom 6 is ~8.5 km
  });

  it('keeps everything but the position', () => {
    const out = spread(
      [at('A', 1, 1), { ...at('B', 1, 1), delivering: true, place: 'Town' }],
      3,
    );
    const b = out.find((p) => p.name === 'B');
    expect(b.delivering).toBe(true);
    expect(b.place).toBe('Town');
  });
});

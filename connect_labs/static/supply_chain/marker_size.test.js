// How big a place is drawn in the portfolio map's Stock mode.
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// The file attaches to window rather than exporting, as the page loads it.
const here = path.dirname(fileURLToPath(import.meta.url));
new Function(fs.readFileSync(path.join(here, 'marker_size.js'), 'utf8'))();
const { markerRadius, biggestKey } = globalThis.SupplyMarkerSize;

describe('markerRadius', () => {
  it('draws a worker with nothing at the smallest size, never zero', () => {
    expect(markerRadius('user_held', 0, 150)).toBe(2.5);
    expect(markerRadius('user_held', null, 150)).toBe(2.5);
  });
  it('grows a worker by area with what they hold', () => {
    expect(markerRadius('user_held', 150, 150)).toBe(9);
    expect(markerRadius('user_held', 37.5, 150)).toBeCloseTo(2.5 + 6.5 * 0.5);
  });
  it('keeps stores on their own, larger scale', () => {
    expect(markerRadius('central_store', 400, 400)).toBe(20);
    expect(markerRadius('regional_store', 0, 400)).toBe(5);
  });
  it('never draws past the biggest, even on a stale maximum', () => {
    expect(markerRadius('user_held', 300, 150)).toBe(9);
  });
  it('draws a negative balance at the smallest size, not a negative radius', () => {
    expect(markerRadius('user_held', -20, 150)).toBe(2.5);
  });
});

describe('biggestKey', () => {
  it('compares workers only with workers, per unit', () => {
    expect(biggestKey('user_held', 'sachet')).not.toBe(
      biggestKey('central_store', 'sachet'),
    );
    expect(biggestKey('user_held', 'sachet')).not.toBe(
      biggestKey('user_held', 'carton'),
    );
    expect(biggestKey('central_store', 'sachet')).toBe(
      biggestKey('regional_store', 'sachet'),
    );
  });
});

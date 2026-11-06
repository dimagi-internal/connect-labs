/*
 * Tests for the geometry helpers in window.SolServiceAreaPicker (service_area_picker.js).
 * Run: node --test connect_labs/static/solicitations/service_area_picker.test.mjs
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

global.window = {};
const here = dirname(fileURLToPath(import.meta.url));
await import('file://' + join(here, 'service_area_picker.js'));
const P = window.SolServiceAreaPicker;

function square([x0, y0, x1, y1]) {
  return {
    type: 'Polygon',
    coordinates: [
      [
        [x0, y0],
        [x1, y0],
        [x1, y1],
        [x0, y1],
        [x0, y0],
      ],
    ],
  };
}
const state = { name: 'Kano', geometry: square([0, 0, 10, 10]) };

test('a ward inside a limit is inside', () => {
  assert.equal(P.insideAny(square([2, 2, 3, 3]), [state]), true);
});

test('an area across the country is outside', () => {
  assert.equal(P.insideAny(square([20, 20, 25, 25]), [state]), false);
});

test('inside any one of several limits is inside', () => {
  const lagos = { name: 'Lagos', geometry: square([20, 20, 30, 30]) };
  assert.equal(P.insideAny(square([21, 21, 22, 22]), [state, lagos]), true);
});

test('no limits means anywhere', () => {
  assert.equal(P.insideAny(square([20, 20, 25, 25]), []), true);
});

test('a shape whose centroid falls outside itself is left to the server', () => {
  // A U: its centroid sits in the gap between the arms.
  const u = {
    type: 'Polygon',
    coordinates: [
      [
        [0, 0],
        [6, 0],
        [6, 6],
        [5, 6],
        [5, 1],
        [1, 1],
        [1, 6],
        [0, 6],
        [0, 0],
      ],
    ],
  };
  assert.equal(P.insideAny(u, [state]), null);
});

test('a hole is not inside its polygon', () => {
  const donut = {
    type: 'Polygon',
    coordinates: [
      square([0, 0, 10, 10]).coordinates[0],
      square([4, 4, 6, 6]).coordinates[0],
    ],
  };
  assert.equal(P.inGeometry([5, 5], donut), false);
  assert.equal(P.inGeometry([1, 1], donut), true);
});

test('the centroid of a multipolygon is its largest part', () => {
  const g = {
    type: 'MultiPolygon',
    coordinates: [
      square([0, 0, 1, 1]).coordinates,
      square([10, 10, 14, 14]).coordinates,
    ],
  };
  assert.deepEqual(P.centroid(g), [12, 12]);
});

test('a country is an area at level 0 keyed by its code', () => {
  const d = P.countryDesc('NGA', 'NG', 'Nigeria');
  assert.equal(d.key, 'country:NGA');
  assert.equal(d.level, 0);
  assert.equal(d.source, 'overture');
  assert.equal(d.country, 'NGA');
  assert.deepEqual(d.ref, {
    boundary_id: 'country:NGA',
    alpha2: 'NG',
    subtype: 'country',
  });
});

test('country search puts names that start with the query first', () => {
  const countries = [
    { alpha3: 'NER', name: 'Niger' },
    { alpha3: 'NGA', name: 'Nigeria' },
    { alpha3: 'GNQ', name: 'Equatorial Guinea' },
    { alpha3: 'GIN', name: 'Guinea' },
  ];
  assert.deepEqual(
    P.matchCountries(countries, 'guin').map((c) => c.alpha3),
    ['GIN', 'GNQ'],
  );
  assert.deepEqual(
    P.matchCountries(countries, 'nga').map((c) => c.alpha3),
    ['NGA'],
  );
  assert.deepEqual(P.matchCountries(countries, '  '), []);
});

test('bounds covers every ring of a multipolygon', () => {
  const b = P.bounds([
    [
      [
        [0, 0],
        [2, 1],
      ],
    ],
    [
      [
        [-3, 5],
        [1, -4],
      ],
    ],
  ]);
  assert.deepEqual(b, [-3, -4, 2, 5]);
});

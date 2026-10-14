/*
 * Tests for window.SimpleMicroplanAdmin (simple_microplan_admin.js).
 * Run: node --test connect_labs/static/microplans/simple_microplan_admin.test.mjs
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

global.window = {};
const here = dirname(fileURLToPath(import.meta.url));
await import('file://' + join(here, 'simple_microplan_admin.js'));
const A = window.SimpleMicroplanAdmin;

// a square ward feature as the boundary-viewport endpoint returns it
function ward(name, [x0, y0, x1, y1], props = {}) {
  return {
    type: 'Feature',
    geometry: {
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
    },
    properties: {
      name,
      lga: 'Sabon Gari',
      state: 'Kaduna',
      provider: 'geopode',
      provider_rank: 0,
      area_km2: 4,
      ...props,
    },
  };
}

function row(lon, lat, fields = {}) {
  return {
    'Area Slug': 'SG' + lon + lat,
    Ward: '',
    Centroid: lon.toFixed(8) + ' ' + lat.toFixed(8),
    LGA: '',
    State: '',
    ...fields,
  };
}

const WARDS = [
  ward('Bomo', [7.6, 11.1, 7.7, 11.2]),
  ward('Zabi', [7.7, 11.1, 7.8, 11.2], { lga: 'Zaria', state: 'Kaduna' }),
];

test('a work area inside a ward takes its ward, LGA and state', () => {
  const rows = [row(7.65, 11.15), row(7.75, 11.15)];
  assert.equal(A.fillAdminFromWards(rows, WARDS), 2);
  assert.deepEqual(
    rows.map((r) => [r.Ward, r.LGA, r.State]),
    [
      ['Bomo', 'Sabon Gari', 'Kaduna'],
      ['Zabi', 'Zaria', 'Kaduna'],
    ],
  );
});

test('a work area outside every ward stays blank', () => {
  const rows = [row(9.0, 12.0)];
  assert.equal(A.fillAdminFromWards(rows, WARDS), 0);
  assert.deepEqual([rows[0].Ward, rows[0].LGA, rows[0].State], ['', '', '']);
  assert.equal(A.countMissingAdmin(rows), 1);
});

test('values already there (reverse geocode, ward lookup) are kept', () => {
  const rows = [
    row(7.65, 11.15, { LGA: 'Sabon-Gari LGA', State: 'Kaduna State' }),
    row(7.65, 11.16, { Ward: 'Hanwa', LGA: 'Zaria', State: 'Kaduna' }),
  ];
  assert.equal(A.fillAdminFromWards(rows, WARDS), 1);
  assert.deepEqual(
    [rows[0].Ward, rows[0].LGA, rows[0].State],
    ['Bomo', 'Sabon-Gari LGA', 'Kaduna State'],
  );
  assert.deepEqual(
    [rows[1].Ward, rows[1].LGA, rows[1].State],
    ['Hanwa', 'Zaria', 'Kaduna'],
  );
});

test('the centroid decides, not the work area', () => {
  // centroid just over the line into Zabi
  const rows = [row(7.7001, 11.15)];
  A.fillAdminFromWards(rows, WARDS);
  assert.equal(rows[0].Ward, 'Zabi');
});

test('the same ward from two providers: the preferred provider wins', () => {
  const twins = [
    ward('Sabongari', [7.6, 11.1, 7.7, 11.2], {
      provider: 'grid3',
      provider_rank: 1,
      lga: 'Sabongari',
      area_km2: 3,
    }),
    ward('Sabon Gari', [7.6, 11.1, 7.7, 11.2], {
      provider: 'geopode',
      provider_rank: 0,
      area_km2: 5,
    }),
  ];
  const rows = [row(7.65, 11.15)];
  A.fillAdminFromWards(rows, twins);
  assert.deepEqual([rows[0].Ward, rows[0].LGA], ['Sabon Gari', 'Sabon Gari']);
  // order in the response does not matter
  const again = [row(7.65, 11.15)];
  A.fillAdminFromWards(again, twins.slice().reverse());
  assert.equal(again[0].Ward, 'Sabon Gari');
});

test('a hole in a ward is not part of it; multipolygons are', () => {
  const holed = {
    type: 'Feature',
    geometry: {
      type: 'MultiPolygon',
      coordinates: [
        [
          [
            [0, 0],
            [10, 0],
            [10, 10],
            [0, 10],
            [0, 0],
          ],
          [
            [4, 4],
            [6, 4],
            [6, 6],
            [4, 6],
            [4, 4],
          ],
        ],
        [
          [
            [20, 0],
            [21, 0],
            [21, 1],
            [20, 1],
            [20, 0],
          ],
        ],
      ],
    },
    properties: { name: 'Ring', lga: 'L', state: 'S' },
  };
  const rows = [row(1, 1), row(5, 5), row(20.5, 0.5)];
  assert.equal(A.fillAdminFromWards(rows, [holed]), 2);
  assert.deepEqual(
    rows.map((r) => r.Ward),
    ['Ring', '', 'Ring'],
  );
});

test('bbox covers only the rows still missing a field', () => {
  const rows = [
    row(7.65, 11.15),
    row(7.75, 11.18),
    row(9.0, 9.0, { Ward: 'W', LGA: 'L', State: 'S' }),
  ];
  assert.deepEqual(A.bboxOfRowsNeedingAdmin(rows), [7.65, 11.15, 7.75, 11.18]);
  assert.equal(
    A.bboxOfRowsNeedingAdmin([row(1, 1, { Ward: 'W', LGA: 'L', State: 'S' })]),
    null,
  );
});

test('count of rows Connect would reject', () => {
  const rows = [
    row(1, 1, { Ward: 'W', LGA: 'L', State: 'S' }),
    row(1, 1, { Ward: 'W', LGA: '', State: 'S' }),
    row(1, 1, { Ward: ' ', LGA: 'L', State: 'S' }),
    row(1, 1),
  ];
  assert.equal(A.countMissingAdmin(rows), 3);
});

test('no wards back from the server changes nothing', () => {
  const rows = [row(7.65, 11.15)];
  assert.equal(A.fillAdminFromWards(rows, []), 0);
  assert.equal(A.fillAdminFromWards(rows, undefined), 0);
  assert.equal(rows[0].Ward, '');
});

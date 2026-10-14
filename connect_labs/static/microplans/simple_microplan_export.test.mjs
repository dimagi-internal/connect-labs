/*
 * Tests for window.SimpleMicroplanExport (simple_microplan_export.js).
 * Run: node --test connect_labs/static/microplans/simple_microplan_export.test.mjs
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

global.window = {};
const here = dirname(fileURLToPath(import.meta.url));
await import('file://' + join(here, 'simple_microplan_export.js'));
const E = window.SimpleMicroplanExport;

// a square boundary part as the editor stores it: {polys, ward, lga, state, name?}
function part([x0, y0, x1, y1], props = {}) {
  return {
    polys: [
      [
        [
          [x0, y0],
          [x1, y0],
          [x1, y1],
          [x0, y1],
          [x0, y0],
        ],
      ],
    ],
    ward: '',
    lga: '',
    state: '',
    ...props,
  };
}

// a work-area row as the editor stores it
function row(slug, lon, lat, extra = {}) {
  return {
    'Area Slug': slug,
    Ward: 'Doka',
    Centroid: lon + ' ' + lat,
    Boundary: `POLYGON((${lon} ${lat}, ${lon + 0.001} ${lat}, ${lon} ${lat})`,
    'Building Count': 4,
    'Expected Visit Count': 4,
    LGA: 'Tofa',
    State: 'Kano',
    'Target Population': 0,
    'Work Area Group Name': 'DO-G-001',
    ...extra,
  };
}

test('work-area CSV header matches the CHC creator / Connect import order', () => {
  const header = E.workAreaCsv(
    [row('AA00001', 8.3, 12.0)],
    [part([8, 11, 9, 13])],
  ).split('\n')[0];
  assert.equal(
    header,
    'Area Slug,Ward,Centroid,Boundary,Building Count,Expected Visit Count,' +
      'Target Population,LGA,State,Implementation Area,Work Area Group Name',
  );
});

test('extra columns (singletons) follow the standard ones', () => {
  const r = row('AA00001', 8.3, 12.0, { keepSingleton: 'N/A' });
  const header = E.workAreaCsv([r], [part([8, 11, 9, 13])]).split('\n')[0];
  assert.ok(header.endsWith('Work Area Group Name,keepSingleton'));
});

test('boundary uses the creator WKT spacing and stays quoted', () => {
  const csv = E.workAreaCsv(
    [row('AA00001', 8.3, 12.0)],
    [part([8, 11, 9, 13])],
  );
  assert.match(csv.split('\n')[1], /"POLYGON \(\(8\.3 12, /);
});

test('each work area takes the name of the boundary part it sits in', () => {
  const parts = [
    part([8, 11, 9, 12], { ward: 'Doka' }),
    part([9, 11, 10, 12], { ward: 'Durma', names: ['Durma North'] }),
  ];
  const out = E.workAreaRows([row('A', 8.5, 11.5), row('B', 9.5, 11.5)], parts);
  assert.equal(out[0]['Implementation Area'], 'Doka');
  assert.equal(out[1]['Implementation Area'], 'Durma North');
});

test('a work area just outside every part goes to the nearest part', () => {
  const parts = [
    part([8, 11, 9, 12], { ward: 'Doka' }),
    part([20, 11, 21, 12], { ward: 'Far' }),
  ];
  const out = E.workAreaRows([row('A', 9.0001, 11.5)], parts);
  assert.equal(out[0]['Implementation Area'], 'Doka');
});

test('the stored rows are not changed by exporting', () => {
  const r = row('A', 8.5, 11.5);
  E.workAreaRows([r], [part([8, 11, 9, 12])]);
  assert.equal(r['Implementation Area'], undefined);
  assert.match(r.Boundary, /^POLYGON\(\(/);
});

const names = (parts, rows) => E.areasOf(parts, rows).map((a) => a.name);
// one boundary part made of two separate squares (e.g. a ward in two pieces)
function twoPiece(props = {}) {
  const a = part([0, 0, 1, 1]);
  const b = part([2, 0, 3, 1]);
  return {
    polys: [...a.polys, ...b.polys],
    ward: '',
    lga: '',
    state: '',
    ...props,
  };
}

test('area name: typed name, else ward, else numbered default', () => {
  assert.deepEqual(
    names([part([0, 0, 1, 1], { names: ['  Kano 2 '], ward: 'Doka' })]),
    ['Kano 2'],
  );
  assert.deepEqual(names([part([0, 0, 1, 1], { ward: 'Doka' })]), ['Doka']);
  assert.deepEqual(names([part([0, 0, 1, 1]), part([1, 0, 2, 1])]), [
    'Implementation Area 1',
    'Implementation Area 2',
  ]);
});

test('a name saved on a single-polygon boundary still applies', () => {
  assert.deepEqual(
    names([part([0, 0, 1, 1], { name: 'Old name', ward: 'Doka' })]),
    ['Old name'],
  );
});

test('with no boundary ward, the ward of the work areas inside is used', () => {
  const rows = [
    row('A', 0.5, 0.5, { Ward: 'Langel' }),
    row('B', 0.6, 0.5, { Ward: 'Langel' }),
  ];
  assert.deepEqual(names([part([0, 0, 1, 1])], rows), ['Langel']);
});

test('implementation area CSV matches the sample shape', () => {
  const csv = E.implementationAreaCsv([part([8, 11, 9, 12], { ward: 'Doka' })]);
  const [header, line] = csv.split('\n');
  assert.equal(header, 'Implementation Area Name,Centroid,Boundary');
  assert.equal(line, 'Doka,8.5 11.5,"POLYGON((8 11,9 11,9 12,8 12,8 11))"');
});

test('implementation area coordinates are rounded to 6 decimals', () => {
  const p = part([8.1234567891, 11, 9, 12]);
  const r = E.implementationAreaRows([p])[0];
  assert.match(r.Boundary, /^POLYGON\(\(8\.123457 11,/);
});

test('a boundary in several pieces becomes one single-POLYGON row per piece', () => {
  const rows = E.implementationAreaRows([twoPiece({ ward: 'Doka' })]);
  assert.equal(rows.length, 2);
  assert.deepEqual(
    rows.map((r) => r['Implementation Area Name']),
    ['Doka 1', 'Doka 2'],
  );
  rows.forEach((r) => assert.match(r.Boundary, /^POLYGON\(\(/));
  assert.ok(!E.implementationAreaCsv([twoPiece()]).includes('MULTIPOLYGON'));
});

test('each piece is named on its own, and its work areas follow it', () => {
  const p = twoPiece({ ward: 'Doka', names: ['Doka West', 'Doka East'] });
  const out = E.workAreaRows([row('A', 0.5, 0.5), row('B', 2.5, 0.5)], [p]);
  assert.deepEqual(
    out.map((r) => r['Implementation Area']),
    ['Doka West', 'Doka East'],
  );
});

test('names used twice are reported, case-insensitively', () => {
  const parts = [
    part([0, 0, 1, 1], { names: ['Doka'] }),
    part([1, 0, 2, 1], { names: ['doka'] }),
    part([2, 0, 3, 1], { names: ['Durma'] }),
  ];
  assert.deepEqual(E.duplicateNames(E.areasOf(parts)), ['Doka']);
});

test('one name feeds both files, so Connect can link them', () => {
  const parts = [part([8, 11, 9, 12], { ward: 'Doka', names: ['Doka East'] })];
  const wa = E.workAreaRows([row('A', 8.5, 11.5)], parts)[0][
    'Implementation Area'
  ];
  const ia = E.implementationAreaRows(parts)[0]['Implementation Area Name'];
  assert.equal(wa, ia);
});

/* ---- Work Area Group names ---- */

test('groups are named <WARD>-G-001, numbered per ward', () => {
  const nameFor = E.groupNamer([]);
  assert.equal(nameFor('Doka'), 'Doka-G-001');
  assert.equal(nameFor('Doka'), 'Doka-G-002');
  assert.equal(nameFor('Durma'), 'Durma-G-001');
});

test('numbering carries on after the groups that stay', () => {
  const nameFor = E.groupNamer([
    'Doka-G-001',
    'Doka-G-007',
    'Kauran_Mata-G-002',
  ]);
  assert.equal(nameFor('Doka'), 'Doka-G-008');
  assert.equal(nameFor('Kauran_Mata'), 'Kauran_Mata-G-003');
});

test('a ward with spaces gives a group key with none', () => {
  assert.equal(E.groupWardKey({ Ward: ' Kauran  Mata ' }), 'Kauran_Mata');
  assert.equal(E.groupWardKey({ Ward: '' }), '');
});

test('a group is named after the ward most of it is in', () => {
  const members = [{ Ward: 'Doka' }, { Ward: 'Durma' }, { Ward: 'Durma' }];
  assert.equal(E.groupWardKeyOf(members), 'Durma');
  assert.equal(E.groupWardKeyOf([{ Ward: '' }]), '');
});

test('exported group names never contain spaces', () => {
  const out = E.workAreaRows(
    [row('A', 8.5, 11.5, { 'Work Area Group Name': 'Kauran Mata  G 1' })],
    [part([8, 11, 9, 12])],
  );
  assert.equal(out[0]['Work Area Group Name'], 'Kauran_Mata_G_1');
});

/* ---- several boundaries ---- */

test('a boundary in pieces is split into one boundary per polygon', () => {
  const out = E.splitBoundary([twoPiece({ ward: 'Doka' })], 1);
  assert.equal(out.length, 2);
  assert.deepEqual(
    out.map((p) => p.name),
    ['Doka 1', 'Doka 2'],
  );
  assert.deepEqual(
    out.map((p) => p.id),
    [1, 2],
  );
  out.forEach((p) => assert.equal(p.polys.length, 1));
  // and the names then come through unchanged, with no duplicates
  assert.deepEqual(names(out), ['Doka 1', 'Doka 2']);
});

test('single-polygon boundaries keep their id, name and settings', () => {
  const p = part([0, 0, 1, 1], {
    id: 7,
    name: 'Langel',
    params: { cellSize: '20' },
  });
  const [out] = E.splitBoundary([p], 99);
  assert.equal(out.id, 7);
  assert.equal(out.name, 'Langel');
  assert.deepEqual(out.params, { cellSize: '20' });
});

test('boundaries added later get ids after the ones already there', () => {
  const have = E.splitBoundary([part([0, 0, 1, 1]), part([1, 0, 2, 1])], 1);
  const next = E.nextBoundaryId(have);
  const added = E.splitBoundary([part([2, 0, 3, 1])], next);
  assert.equal(added[0].id, 3);
});

test('each boundary gets its own Implementation Area row in the combined file', () => {
  const parts = E.splitBoundary(
    [
      part([8, 11, 9, 12], { ward: 'Doka' }),
      part([9, 11, 10, 12], { ward: 'Durma' }),
    ],
    1,
  );
  const rows = [row('A', 8.5, 11.5), row('B', 9.5, 11.5, { Ward: 'Durma' })];
  assert.deepEqual(
    E.implementationAreaRows(parts, rows).map(
      (r) => r['Implementation Area Name'],
    ),
    ['Doka', 'Durma'],
  );
  assert.deepEqual(
    E.workAreaRows(rows, parts).map((r) => r['Implementation Area']),
    ['Doka', 'Durma'],
  );
});

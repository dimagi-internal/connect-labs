/*
 * Tests for window.SolQuestionGrids (question_grids.js): a grid question in the
 * call editor, collapsed from its stored rows and expanded back into them.
 * Run: node --test connect_labs/static/solicitations/question_grids.test.mjs
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

global.window = {};
const here = dirname(fileURLToPath(import.meta.url));
await import('file://' + join(here, 'question_grids.js'));
const G = window.SolQuestionGrids;

const COLUMNS = ['1st choice', '2nd choice'];
function row(id, label, extra = {}) {
  return {
    id,
    text: `Rank these [${label}]`,
    type: 'multiple_choice',
    required: false,
    options: COLUMNS,
    display: 'radio',
    grid: {
      id: 'g1',
      title: 'Rank these',
      columns: COLUMNS,
      row: label,
      ...extra,
    },
  };
}
const plain = {
  id: 'q1',
  text: 'Name?',
  type: 'text',
  required: true,
  options: [],
};

test("a grid's consecutive rows collapse into one editor item", () => {
  const items = G.collapse([
    plain,
    row('g1_r1', 'Cost', { one_per_column: true }),
    row('g1_r2', 'Distance', { one_per_column: true }),
  ]);
  assert.equal(items.length, 2);
  assert.equal(items[0], plain);
  const grid = items[1];
  assert.equal(grid.type, 'grid');
  assert.equal(grid.id, 'g1');
  assert.equal(grid.text, 'Rank these');
  assert.equal(grid.rows_text, 'Cost\nDistance');
  assert.equal(grid.columns_text, '1st choice\n2nd choice');
  assert.equal(grid.one_per_column, true);
});

test('a grid is required when any of its rows is', () => {
  const [grid] = G.collapse([
    row('g1_r1', 'Cost'),
    { ...row('g1_r2', 'Distance'), required: true },
  ]);
  assert.equal(grid.required, true);
});

test('collapse then expand gives the stored rows back unchanged', () => {
  const stored = [
    plain,
    row('g1_r1', 'Cost', { one_per_column: true }),
    row('g1_r2', 'Distance', { one_per_column: true }),
  ];
  const back = G.expand(G.collapse(stored));
  assert.deepEqual(back, stored);
});

test('an edited grid keeps the ids of the rows it kept and gives new rows new ids', () => {
  const [grid] = G.collapse([row('g1_r1', 'Cost'), row('g1_r2', 'Distance')]);
  grid.rows_text = 'Distance\nSafety\n\n  Cost  ';
  grid.columns_text = '1st\n2nd\n3rd';
  grid.one_per_column = true;
  const out = G.expand([grid]);
  assert.deepEqual(
    out.map((q) => [q.id, q.grid.row]),
    [
      ['g1_r2', 'Distance'],
      ['g1_r3', 'Safety'],
      ['g1_r1', 'Cost'],
    ],
  );
  for (const q of out) {
    assert.deepEqual(q.options, ['1st', '2nd', '3rd']);
    assert.deepEqual(q.grid.columns, ['1st', '2nd', '3rd']);
    assert.equal(q.grid.one_per_column, true);
    assert.equal(q.display, 'radio');
  }
});

test('a new grid item (switched from a plain question) expands to fresh row ids', () => {
  const item = {
    id: 'q_3',
    type: 'grid',
    text: 'Rank',
    required: true,
    rows_text: 'A\nB',
    columns_text: 'X\nY',
    one_per_column: false,
  };
  const out = G.expand([plain, item]);
  assert.deepEqual(
    out.map((q) => q.id),
    ['q1', 'q_3_r1', 'q_3_r2'],
  );
  assert.equal(out[1].text, 'Rank [A]');
  assert.equal(out[1].required, true);
  assert.equal('one_per_column' in out[1].grid, false);
});

test('lines drops blanks and trims', () => {
  assert.deepEqual(G.lines(' a \n\n b\n'), ['a', 'b']);
});

test('a required ranking stores grid.required and leaves its rows optional', () => {
  const item = {
    id: 'g1',
    type: 'grid',
    text: 'Rank these',
    required: true,
    rows_text: 'Cost\nDistance\nSafety',
    columns_text: '1st\n2nd',
    one_per_column: true,
  };
  const out = G.expand([item]);
  assert.ok(out.every((q) => q.required === false && q.grid.required === true));
  const [back] = G.collapse(out);
  assert.equal(back.required, true);
  assert.deepEqual(G.expand([back]), out);
});

test('a collapsed grid keeps its choices for a switch to Multiple Choice', () => {
  const [grid] = G.collapse([row('g1_r1', 'Cost')]);
  assert.equal(grid.options_text, '1st choice, 2nd choice');
});

test('problems names the grids that cannot be saved', () => {
  const ok = {
    type: 'grid',
    text: 'Rank',
    rows_text: 'A\nB',
    columns_text: '1st\n2nd',
  };
  assert.deepEqual(G.problems([plain, ok]), []);
  assert.deepEqual(G.problems([plain, { ...ok, rows_text: ' \n' }]), [
    'Q2: a grid needs at least one row.',
  ]);
  assert.equal(G.problems([{ ...ok, text: '', columns_text: '' }]).length, 2);
  const tooFewRows = {
    ...ok,
    rows_text: 'A',
    one_per_column: true,
    required: true,
  };
  assert.match(G.problems([tooFewRows])[0], /at least as many rows as columns/);
});

test('newQuestionId skips ids already taken by questions and grid rows', () => {
  const items = [
    { id: 'q_3' },
    { id: 'g', type: 'grid', rows: [{ id: 'q_4' }] },
    { id: 'q_1' },
  ];
  assert.equal(G.newQuestionId(items), 'q_5');
});

test('limitOnePerColumn clears the column in the other rows of a ranking only', () => {
  const radios = [];
  const grid = { querySelectorAll: () => radios };
  for (const name of ['r1', 'r2']) {
    for (const value of ['1st', '2nd']) {
      radios.push({
        type: 'radio',
        name,
        value,
        checked: false,
        closest: () => grid,
      });
    }
  }
  radios[0].checked = true; // r1: 1st
  radios[2].checked = true; // r2: 1st
  G.limitOnePerColumn(radios[2]);
  assert.deepEqual(
    radios.map((r) => r.checked),
    [false, false, true, false],
  );
  const loose = {
    type: 'radio',
    name: 'x',
    value: '1st',
    checked: true,
    closest: () => null,
  };
  G.limitOnePerColumn(loose);
  assert.equal(loose.checked, true);
});

test('a tick-any grid and a row framing survive an edit unchanged', () => {
  const tickAny = (id, label) => ({
    id,
    text: `Barriers [${label}]`,
    type: 'checkboxes',
    required: false,
    options: COLUMNS,
    grid: { id: 'g2', title: 'Barriers', columns: COLUMNS, row: label },
  });
  const stored = [
    tickAny('g2_r1', 'Cost'),
    tickAny('g2_r2', 'Distance'),
    { ...row('g1_r1', 'Cost'), framing: 'Why we ask.' },
    row('g1_r2', 'Distance'),
  ];
  const items = G.collapse(stored);
  assert.equal(items[0].row_type, 'checkboxes');
  assert.deepEqual(G.expand(items), stored);
  // Ticking "one per column" on a tick-any grid cannot make it a ranking.
  items[0].one_per_column = true;
  assert.ok(
    G.expand(items)
      .slice(0, 2)
      .every((q) => q.type === 'checkboxes' && !('one_per_column' in q.grid)),
  );
});

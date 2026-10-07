// @vitest-environment happy-dom
// Sorting a supply sheet by a click on a column's header (sheet_sort.js).
import { describe, it, expect, beforeAll, beforeEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const SOURCE = fs.readFileSync(path.join(here, 'sheet_sort.js'), 'utf8');

const SHEET = `
<div data-live id="region">
<table class="sheet" data-testid="quotes">
  <thead><tr>
    <th scope="col" data-sort>Supplier</th>
    <th scope="col" data-sort>Landed</th>
    <th scope="col">Open</th>
  </tr></thead>
  <tbody>
    <tr data-q="zulu"><th scope="row">Zulu</th><td data-sort-value="4.10">USD 4.10</td><td>Open</td></tr>
    <tr data-q="mid"><th scope="row">Mid</th><td data-sort-value="">needs freight</td><td>Open</td></tr>
    <tr data-q="mid-v1" data-sort-follows><td colspan="3">v1 · replaced</td></tr>
    <tr data-q="alpha"><th scope="row">Alpha</th><td data-sort-value="12.5">USD 12.50</td><td>Open</td></tr>
  </tbody>
</table>
</div>`;

function order() {
  return Array.from(document.querySelectorAll('tbody tr')).map((r) =>
    r.getAttribute('data-q'),
  );
}

function header(name) {
  return Array.from(document.querySelectorAll('thead th')).find((th) =>
    th.textContent.includes(name),
  );
}

function click(name) {
  header(name).querySelector('button.sheet-sort').click();
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeAll(() => {
  document.body.innerHTML = SHEET;
  new Function(SOURCE)();
});

beforeEach(() => {
  window.SheetSort.forget();
  document.body.innerHTML = SHEET;
  window.SheetSort.arm(document);
});

describe('sheet_sort', () => {
  it('makes a data-sort header a button and leaves the others alone', () => {
    expect(
      header('Supplier').querySelector('button.sheet-sort'),
    ).not.toBeNull();
    expect(header('Supplier').getAttribute('aria-sort')).toBe('none');
    expect(header('Open').querySelector('button')).toBeNull();
  });

  it('sorts figures as numbers, ascending then descending, a missing one last both ways', () => {
    click('Landed');
    // 4.10 before 12.5 as numbers (as text "12.5" < "4.10"); Mid has no figure: last.
    expect(order()).toEqual(['zulu', 'alpha', 'mid', 'mid-v1']);
    expect(header('Landed').getAttribute('aria-sort')).toBe('ascending');
    expect(header('Supplier').getAttribute('aria-sort')).toBe('none');
    click('Landed');
    expect(order()).toEqual(['alpha', 'zulu', 'mid', 'mid-v1']);
    expect(header('Landed').getAttribute('aria-sort')).toBe('descending');
  });

  it('sorts by text where a cell gives no sort value, keeping a following row with its lead', () => {
    click('Supplier');
    expect(order()).toEqual(['alpha', 'mid', 'mid-v1', 'zulu']);
  });

  it('puts a swapped-in copy of a sorted table back in the same order', async () => {
    click('Landed');
    click('Landed');
    // cell_edit.js swaps the [data-live] region for a fresh copy in the server's order.
    document.getElementById('region').outerHTML = SHEET;
    await tick();
    expect(order()).toEqual(['alpha', 'zulu', 'mid', 'mid-v1']);
    expect(header('Landed').getAttribute('aria-sort')).toBe('descending');
    // And its headers still sort: the click is handled on the document.
    click('Supplier');
    expect(order()).toEqual(['alpha', 'mid', 'mid-v1', 'zulu']);
  });
});

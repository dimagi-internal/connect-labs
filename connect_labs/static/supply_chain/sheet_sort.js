// Sort a supply sheet by a column, the way a spreadsheet does.
//
// A header cell marked `data-sort` becomes a button: click it and the table's rows sort by that
// column, ascending; click again for descending. A cell sorts by its `data-sort-value` when it
// gives one (a figure or an ISO date, so "USD 9.10" sorts below "USD 42.50" and 2 Oct before
// 10 Oct), else by its text. Numbers compare as numbers, words as words. A missing value -- an
// empty sort value, or a cell reading "—" -- sorts last whichever way round. The header carries
// aria-sort, so a screen reader says which column the rows are in order by.
//
// A row marked `data-sort-follows` (an earlier version under its quote) travels with the row
// above it. The page may swap a table for a fresh copy (cell_edit.js re-reads the page after a
// save): clicks are handled on the document, headers are re-armed as they arrive, and the sort a
// table was last in is put back on the fresh copy.
(function () {
  'use strict';

  var MISSING = ['', '—', '-', '–'];
  // The last sort per table, by the table's data-testid (or id): {key: {column, direction}}.
  var remembered = {};

  function tableKey(table) {
    return table.getAttribute('data-testid') || table.id || '';
  }

  function valueOf(cell) {
    if (!cell) return '';
    var given = cell.getAttribute('data-sort-value');
    var text = given !== null ? given : cell.textContent;
    return text.replace(/\s+/g, ' ').trim();
  }

  function asNumber(value) {
    if (!/^[-+]?\d*\.?\d+(e[-+]?\d+)?$/i.test(value)) return null;
    var n = Number(value);
    return isFinite(n) ? n : null;
  }

  function compare(a, b) {
    var x = asNumber(a);
    var y = asNumber(b);
    if (x !== null && y !== null) return x - y;
    return a.localeCompare(b, undefined, {
      numeric: true,
      sensitivity: 'base',
    });
  }

  function columnIndex(th) {
    // Count the columns before this header, colspans included.
    var index = 0;
    var cell = th.previousElementSibling;
    while (cell) {
      index += cell.colSpan || 1;
      cell = cell.previousElementSibling;
    }
    return index;
  }

  // Children by tag rather than .rows/.cells, which not every DOM a test runs in provides.
  function childrenOf(element, tags) {
    return Array.prototype.filter.call(element.children, function (child) {
      return tags.indexOf(child.tagName) !== -1;
    });
  }

  function cellAt(row, index) {
    var cells = childrenOf(row, ['TD', 'TH']);
    var at = 0;
    for (var i = 0; i < cells.length; i++) {
      if (at === index) return cells[i];
      at += cells[i].colSpan || 1;
      if (at > index) return null;
    }
    return null;
  }

  function sortTable(table, index, direction) {
    var body = childrenOf(table, ['TBODY'])[0];
    if (!body) return;
    // Rows in groups: a lead row and the rows that follow it.
    var groups = [];
    childrenOf(body, ['TR']).forEach(function (row) {
      if (row.hasAttribute('data-sort-follows') && groups.length) {
        groups[groups.length - 1].rows.push(row);
      } else {
        groups.push({ rows: [row], value: valueOf(cellAt(row, index)) });
      }
    });
    // A lone "nothing here yet" row spanning the table is not data to sort.
    if (groups.length < 2) return;
    groups.forEach(function (group, position) {
      group.position = position;
    });
    groups.sort(function (a, b) {
      var aMissing = MISSING.indexOf(a.value) !== -1;
      var bMissing = MISSING.indexOf(b.value) !== -1;
      if (aMissing || bMissing) {
        if (aMissing && bMissing) return a.position - b.position;
        return aMissing ? 1 : -1;
      }
      var order =
        compare(a.value, b.value) * (direction === 'descending' ? -1 : 1);
      return order || a.position - b.position;
    });
    groups.forEach(function (group) {
      group.rows.forEach(function (row) {
        body.appendChild(row);
      });
    });
  }

  function headers(table) {
    return Array.prototype.slice.call(
      table.querySelectorAll('thead th[data-sort]'),
    );
  }

  function mark(table, th, direction) {
    headers(table).forEach(function (other) {
      other.setAttribute('aria-sort', other === th ? direction : 'none');
    });
  }

  function arm(th) {
    if (th.querySelector(':scope > button.sheet-sort')) return;
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'sheet-sort';
    while (th.firstChild) button.appendChild(th.firstChild);
    th.appendChild(button);
    if (!th.hasAttribute('aria-sort')) th.setAttribute('aria-sort', 'none');
  }

  function armAll(root) {
    var scope = root && root.querySelectorAll ? root : document;
    Array.prototype.forEach.call(
      scope.querySelectorAll('table thead th[data-sort]'),
      function (th) {
        arm(th);
      },
    );
    // A fresh copy of a table that was sorted is put back in that order.
    Array.prototype.forEach.call(
      scope.querySelectorAll('table'),
      function (table) {
        var last = remembered[tableKey(table)];
        if (!last) return;
        var th = headers(table).filter(function (h) {
          return columnIndex(h) === last.column;
        })[0];
        if (!th || th.getAttribute('aria-sort') === last.direction) return;
        sortTable(table, last.column, last.direction);
        mark(table, th, last.direction);
      },
    );
  }

  document.addEventListener('click', function (event) {
    var button =
      event.target.closest &&
      event.target.closest('th[data-sort] > button.sheet-sort');
    if (!button) return;
    var th = button.parentElement;
    var table = th.closest('table');
    if (!table) return;
    var direction =
      th.getAttribute('aria-sort') === 'ascending' ? 'descending' : 'ascending';
    var index = columnIndex(th);
    sortTable(table, index, direction);
    mark(table, th, direction);
    var key = tableKey(table);
    if (key) remembered[key] = { column: index, direction: direction };
  });

  function start() {
    armAll(document);
    // Tables swapped in later (cell_edit.js's [data-live] regions) are armed as they arrive.
    if (window.MutationObserver) {
      new MutationObserver(function (records) {
        for (var i = 0; i < records.length; i++) {
          if (records[i].addedNodes.length) {
            armAll(document);
            return;
          }
        }
      }).observe(document.body, { childList: true, subtree: true });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }

  // For tests and for a page that builds a table itself.
  window.SheetSort = {
    arm: armAll,
    sort: sortTable,
    forget: function () {
      remembered = {};
    },
  };
})();

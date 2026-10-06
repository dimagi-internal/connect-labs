// Edit a supply table's cell in place, the way a spreadsheet does.
//
// A cell marked by the `edit_cell` template tag carries data-edit="<kind>:<id>:<field>",
// its stored value, its type and (for a choice) its options. Click it, press Enter or
// just start typing, and it becomes an input; Enter or Tab saves, Escape puts it back.
// The save posts to /supply/cells/ (supply_chain/cells.py), which runs the same operation
// the record's form runs. The page is then re-read and every [data-live] region on it
// swapped for the fresh one, so whatever the value changes -- a count, a chip, a
// comparison's landed total -- changes with it, and the saved cell is marked.
(function () {
  'use strict';

  var ENDPOINT = '/supply/cells/';
  var editing = null;

  function csrf() {
    var input = document.querySelector('input[name=csrfmiddlewaretoken]');
    if (input) return input.value;
    try {
      return (
        JSON.parse(document.body.getAttribute('hx-headers') || '{}')[
          'X-CSRFToken'
        ] || ''
      );
    } catch (e) {
      return '';
    }
  }

  function cells() {
    return Array.prototype.slice.call(document.querySelectorAll('[data-edit]'));
  }

  function inputFor(cell, typed) {
    var type = cell.getAttribute('data-edit-type');
    var value =
      typed !== undefined ? typed : cell.getAttribute('data-edit-value') || '';
    var input;
    if (type === 'choice') {
      input = document.createElement('select');
      var choices = JSON.parse(cell.getAttribute('data-edit-choices') || '[]');
      // Options are positions and words: the server maps a position back to its stored value.
      choices.forEach(function (words, index) {
        var option = document.createElement('option');
        option.value = String(index);
        option.textContent = words;
        if (String(index) === value) option.selected = true;
        input.appendChild(option);
      });
    } else {
      input = document.createElement('input');
      input.type = type === 'date' ? 'date' : 'text';
      if (type === 'money' || type === 'number' || type === 'int')
        input.inputMode = 'decimal';
      input.value = value;
    }
    input.className = 'cell-input';
    input.setAttribute(
      'aria-label',
      cell.getAttribute('data-edit-label') || 'value',
    );
    return input;
  }

  function start(cell, typed) {
    if (editing) finish(false);
    var input = inputFor(cell, typed);
    editing = {
      cell: cell,
      input: input,
      html: cell.innerHTML,
      was: cell.getAttribute('data-edit-value') || '',
    };
    cell.classList.add('is-editing');
    cell.innerHTML = '';
    cell.appendChild(input);
    input.focus();
    if (input.select && typed === undefined) input.select();
    input.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') {
        event.preventDefault();
        finish(true);
      } else if (event.key === 'Escape') {
        event.preventDefault();
        finish(false);
      } else if (event.key === 'Tab') {
        event.preventDefault();
        finish(true, event.shiftKey ? -1 : 1);
      }
    });
    input.addEventListener('blur', function () {
      // A blur that is not the save itself (clicking elsewhere) saves too, as a sheet does.
      setTimeout(function () {
        if (editing && editing.input === input) finish(true);
      }, 0);
    });
    if (input.tagName === 'SELECT')
      input.addEventListener('change', function () {
        finish(true);
      });
  }

  function restore(state) {
    clearRefusals();
    state.cell.classList.remove('is-editing');
    state.cell.innerHTML = state.html;
  }

  function finish(save, step) {
    var state = editing;
    if (!state) return;
    editing = null;
    var value = state.input.value;
    var key = state.cell.getAttribute('data-edit');
    if (!save || value === state.was) {
      restore(state);
      if (step) move(key, step);
      else state.cell.focus();
      return;
    }
    state.cell.classList.add('is-saving');
    state.input.disabled = true;
    var url = ENDPOINT + window.location.search;
    fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
      body: JSON.stringify({ cell: key, value: value, was: state.was }),
    })
      .then(function (response) {
        return response.json().then(function (body) {
          return { ok: response.ok, body: body };
        });
      })
      .then(function (result) {
        if (!result.ok) {
          restore(state);
          state.cell.classList.remove('is-saving');
          refuse(state.cell, result.body.error || 'Could not save.', value);
          return;
        }
        return refresh().then(function () {
          var saved = document.querySelector(
            '[data-edit="' + result.body.key + '"]',
          );
          if (saved) {
            saved.classList.add('is-saved');
            setTimeout(function () {
              saved.classList.remove('is-saved');
            }, 2400);
          }
          if (step) move(result.body.key, step);
          else if (saved) saved.focus();
        });
      })
      .catch(function () {
        restore(state);
        state.cell.classList.remove('is-saving');
        refuse(
          state.cell,
          'Could not reach the server; nothing was saved.',
          value,
        );
      });
  }

  // The refusal sits under the cell it is about, and the cell reopens on what was typed.
  function refuse(cell, message, typed) {
    clearRefusals();
    var note = document.createElement('div');
    note.className = 'cell-refusal';
    note.setAttribute('role', 'alert');
    note.textContent = message;
    start(cell, typed);
    cell.classList.add('is-refused');
    cell.appendChild(note);
  }

  function clearRefusals() {
    document.querySelectorAll('.cell-refusal').forEach(function (n) {
      n.remove();
    });
    document.querySelectorAll('.is-refused').forEach(function (c) {
      c.classList.remove('is-refused');
    });
  }

  // Tab moves along the row, then down: the next editable cell in the page's order.
  function move(fromKey, step) {
    var all = cells();
    var index = all.findIndex(function (c) {
      return c.getAttribute('data-edit') === fromKey;
    });
    if (index < 0) return;
    var next = all[index + step];
    if (next) next.focus();
  }

  // Re-read the page and swap each live region, keeping open folds open and the scroll where it was.
  function refresh() {
    return fetch(window.location.href, { credentials: 'same-origin' })
      .then(function (r) {
        return r.text();
      })
      .then(function (html) {
        var fresh = new DOMParser().parseFromString(html, 'text/html');
        document.querySelectorAll('[data-live]').forEach(function (region) {
          var id = region.id;
          var replacement = id && fresh.getElementById(id);
          if (!replacement) return;
          var open = Array.prototype.map.call(
            region.querySelectorAll('details[open][id]'),
            function (d) {
              return d.id;
            },
          );
          if (region.tagName === 'DETAILS' && region.open) open.push(region.id);
          region.replaceWith(replacement);
          open.forEach(function (detailsId) {
            var d = document.getElementById(detailsId);
            if (d) d.open = true;
          });
        });
      });
  }

  document.addEventListener('click', function (event) {
    var cell = event.target.closest('[data-edit]');
    if (!cell || cell.classList.contains('is-editing')) return;
    // A link inside a cell still goes where it points.
    if (event.target.closest('a, button, summary')) return;
    start(cell);
  });

  document.addEventListener('keydown', function (event) {
    var cell = event.target;
    if (
      !cell ||
      !cell.hasAttribute ||
      !cell.hasAttribute('data-edit') ||
      cell.classList.contains('is-editing')
    )
      return;
    if (event.key === 'Enter' || event.key === 'F2') {
      event.preventDefault();
      start(cell);
    } else if (
      event.key.length === 1 &&
      !event.metaKey &&
      !event.ctrlKey &&
      !event.altKey
    ) {
      var type = cell.getAttribute('data-edit-type');
      if (type === 'choice' || type === 'date') return;
      // Typing over a cell replaces its value, as in a sheet.
      event.preventDefault();
      start(cell, event.key);
    }
  });
})();

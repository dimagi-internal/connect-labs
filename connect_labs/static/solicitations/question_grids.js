/*
 * Grid questions: the call editor (solicitation_form.html's questionBuilder)
 * and the respond form (_respond_questions.html).
 *
 * A grid ("rank these programs 1st, 2nd, 3rd") is stored the way a Google
 * Form's arrives: one multiple_choice question per row, each carrying
 * ``grid: {id, title, help?, columns, row, one_per_column?, required?}``
 * (connect_labs/solicitations/validation.py). The editor shows it as ONE item
 * of type "grid", with rows and columns typed one per line.
 *
 *   collapse(questions)   -> editor items  (a grid's rows become one item)
 *   expand(items)         -> questions     (a grid item becomes its rows)
 *   problems(items)       -> what stops the items being saved, in words
 *   newQuestionId(items)  -> an id no question or grid row uses
 *   limitOnePerColumn(input) -> clears the input's column in a ranking's other rows
 *
 * A row keeps its question id across edits, so answers already given stay
 * attached to their row; a new row gets an id no other question has.
 *
 * "Required" on a ranking (one_per_column) cannot mean every row: with more
 * rows than columns no one could submit. It means every column must be used,
 * and is stored as ``grid.required`` with the rows themselves optional.
 */
(function (global) {
  'use strict';

  function lines(text) {
    return String(text || '')
      .split('\n')
      .map(function (l) {
        return l.trim();
      })
      .filter(function (l) {
        return l;
      });
  }

  /** Stored questions -> editor items, a grid's consecutive rows as one item. */
  function collapse(questions) {
    var items = [];
    (questions || []).forEach(function (q) {
      var grid = q.grid;
      if (!grid) {
        items.push(q);
        return;
      }
      var last = items[items.length - 1];
      if (!(last && last.type === 'grid' && last.id === grid.id)) {
        var columns = grid.columns || q.options || [];
        last = {
          id: grid.id,
          type: 'grid',
          text: grid.title || '',
          help: grid.help || '',
          required: !!grid.required,
          columns_text: columns.join('\n'),
          // So switching the item to Multiple Choice keeps its choices.
          options_text: columns.join(', '),
          one_per_column: !!grid.one_per_column,
          rows: [],
        };
        items.push(last);
      }
      // The editor sets "required" for the whole grid: required if any row is.
      last.required = last.required || !!q.required;
      last.rows.push({ id: q.id, label: grid.row || '' });
    });
    items.forEach(function (item) {
      if (item.type === 'grid') {
        item.rows_text = item.rows
          .map(function (r) {
            return r.label;
          })
          .join('\n');
      }
    });
    return items;
  }

  /** Editor items -> stored questions, each grid item as one question per row. */
  function expand(items) {
    var used = {};
    (items || []).forEach(function (item) {
      if (item.type !== 'grid') used[item.id] = true;
      (item.rows || []).forEach(function (r) {
        if (r.id) used[r.id] = true;
      });
    });
    function freshId(base) {
      var n = 1;
      while (used[base + '_r' + n]) n++;
      used[base + '_r' + n] = true;
      return base + '_r' + n;
    }
    var out = [];
    (items || []).forEach(function (item) {
      if (item.type !== 'grid') {
        out.push(item);
        return;
      }
      var columns = lines(item.columns_text);
      var ranking = !!item.one_per_column;
      // An edited row keeps the id it had at the same label; else a new one.
      var byLabel = {};
      (item.rows || []).forEach(function (r) {
        if (r.id && !(r.label in byLabel)) byLabel[r.label] = r.id;
      });
      lines(item.rows_text).forEach(function (label) {
        var id = byLabel[label] || freshId(item.id);
        delete byLabel[label];
        var grid = {
          id: item.id,
          title: item.text,
          columns: columns,
          row: label,
        };
        if (item.help) grid.help = item.help;
        if (ranking) {
          grid.one_per_column = true;
          if (item.required) grid.required = true;
        }
        out.push({
          id: id,
          text: item.text + ' [' + label + ']',
          type: 'multiple_choice',
          required: !ranking && !!item.required,
          options: columns.slice(),
          display: 'radio',
          grid: grid,
        });
      });
    });
    return out;
  }

  /** Why the editor items cannot be saved, one message per problem (Q numbers as shown). */
  function problems(items) {
    var found = [];
    (items || []).forEach(function (item, i) {
      if (item.type !== 'grid') return;
      var name = 'Q' + (i + 1);
      var rows = lines(item.rows_text);
      var columns = lines(item.columns_text);
      if (!String(item.text || '').trim())
        found.push(name + ': a grid needs its question text.');
      if (!rows.length) found.push(name + ': a grid needs at least one row.');
      if (!columns.length)
        found.push(name + ': a grid needs at least one column.');
      if (
        item.one_per_column &&
        item.required &&
        rows.length &&
        columns.length > rows.length
      ) {
        found.push(
          name +
            ': with one choice per column and Required, every column must go to a row, so it needs at least as many rows as columns.',
        );
      }
    });
    return found;
  }

  /** "q_N" for the smallest N no question or grid row in ``items`` uses. */
  function newQuestionId(items) {
    var used = {};
    (items || []).forEach(function (item) {
      used[item.id] = true;
      (item.rows || []).forEach(function (r) {
        used[r.id] = true;
      });
    });
    var n = (items || []).length + 1;
    while (used['q_' + n]) n++;
    return 'q_' + n;
  }

  /**
   * A ranking (``[data-one-per-column]``) lets each column go to one row: the
   * radio just picked clears the same column in the grid's other rows, as a
   * Google Form's "limit to one response per column" does. The server checks
   * it too (SolicitationResponseForm.clean).
   */
  function limitOnePerColumn(input) {
    if (!input || input.type !== 'radio' || !input.checked || !input.closest)
      return;
    var grid = input.closest('[data-one-per-column]');
    if (!grid) return;
    grid.querySelectorAll('input[type="radio"]').forEach(function (other) {
      if (
        other !== input &&
        other.name !== input.name &&
        other.value === input.value
      ) {
        other.checked = false;
      }
    });
  }

  global.SolQuestionGrids = {
    collapse: collapse,
    expand: expand,
    problems: problems,
    newQuestionId: newQuestionId,
    limitOnePerColumn: limitOnePerColumn,
    lines: lines,
  };

  if (
    global.document &&
    global.document.addEventListener &&
    !global.__solQuestionGridsListening
  ) {
    global.__solQuestionGridsListening = true;
    global.document.addEventListener('change', function (event) {
      limitOnePerColumn(event.target);
    });
  }
})(typeof window !== 'undefined' ? window : globalThis);

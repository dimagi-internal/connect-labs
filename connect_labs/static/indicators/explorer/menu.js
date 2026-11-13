/* The indicator menu: twelve registry groups, balanced columns, filterable. */
(function () {
  'use strict';
  window.Targeting = window.Targeting || {};
  var T = window.Targeting;
  var util = T.util;
  var state = T.state;

  var menuOpen = false;

  // The old <select> populated indicatorMeta as a side effect of building its
  // <option>s, and the threshold scale reads it. Made explicit, because the
  // menu renders lazily on open and the scale is needed before anyone opens it.
  function index() {
    var S = state.get();
    ((S.methodInfo && S.methodInfo.indicators) || []).forEach(function (i) {
      S.indicatorMeta[i.code] = i;
    });
  }

  function byGroup() {
    var S = state.get();
    var groups = (S.methodInfo && S.methodInfo.groups) || [];
    var bucket = {};
    ((S.methodInfo && S.methodInfo.indicators) || []).forEach(function (i) {
      var g = i.group || 'Other';
      (bucket[g] = bucket[g] || []).push(i);
    });
    var ordered = groups.filter(function (g) {
      return bucket[g];
    });
    // Anything the registry did not place — which should be nothing, and is
    // visible rather than silently dropped if it is not.
    Object.keys(bucket).forEach(function (g) {
      if (ordered.indexOf(g) === -1) ordered.push(g);
    });
    return ordered.map(function (g) {
      return { name: g, items: bucket[g] };
    });
  }

  function render(filter) {
    var S = state.get();
    var cols = document.getElementById('tg-indicator-cols');
    var empty = document.getElementById('tg-indicator-empty');
    if (!cols) return;
    var needle = (filter || '').trim().toLowerCase();
    cols.innerHTML = '';
    var shown = 0;

    // Balanced across explicit columns rather than left to CSS multicol, which
    // fragments its overflow into a column outside the box and drops groups
    // silently. Greedy shortest-column packing: near-optimal for a dozen
    // groups, and never loses one.
    var n =
      parseInt(
        getComputedStyle(cols).getPropertyValue('--tg-cols') || '4',
        10,
      ) || 1;
    var columns = [];
    var weights = [];
    for (var c = 0; c < n; c++) {
      var col = document.createElement('div');
      col.className = 'tg-menu-col';
      // Layout only. Without this the columns sit between the listbox and its
      // options, which breaks the relationship assistive tech reads.
      col.setAttribute('role', 'presentation');
      cols.appendChild(col);
      columns.push(col);
      weights.push(0);
    }
    function shortest() {
      var best = 0;
      for (var k = 1; k < weights.length; k++) {
        if (weights[k] < weights[best]) best = k;
      }
      return best;
    }

    byGroup().forEach(function (group) {
      var items = group.items.filter(function (i) {
        if (!needle) return true;
        return (
          i.label.toLowerCase().indexOf(needle) !== -1 ||
          i.code.toLowerCase().indexOf(needle) !== -1 ||
          group.name.toLowerCase().indexOf(needle) !== -1
        );
      });
      if (!items.length) return;

      var wrap = document.createElement('div');
      wrap.className = 'tg-group';
      var h = document.createElement('div');
      h.className = 'tg-group-name';
      h.textContent = group.name;
      wrap.appendChild(h);

      items.forEach(function (i) {
        shown += 1;
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'tg-opt';
        b.setAttribute('role', 'option');
        b.setAttribute(
          'aria-selected',
          i.code === S.indicator ? 'true' : 'false',
        );
        b.title = i.description || i.label;

        var mark = document.createElement('span');
        // Which way the threshold points. A burden selects above it, a coverage
        // measure below — opposite questions, and the one thing a reader must
        // know before touching the slider.
        mark.className =
          'tg-opt-mark ' +
          (i.lower_is_worse ? 'tg-dot-coverage' : 'tg-dot-burden');
        b.appendChild(mark);

        var name = document.createElement('span');
        name.className = 'tg-opt-name';
        name.textContent = i.label;
        b.appendChild(name);

        b.onclick = function () {
          close();
          T.main.selectIndicator(i.code);
        };
        wrap.appendChild(b);
      });

      var into = shortest();
      columns[into].appendChild(wrap);
      // A header costs about a line and a half; a long label wraps to two.
      // Rough is enough — this decides tidiness, not correctness.
      weights[into] += 1.6;
      items.forEach(function (i) {
        weights[into] += i.label.length > 26 ? 2 : 1;
      });
    });

    if (empty) empty.classList.toggle('hidden', shown > 0);
  }

  function options() {
    var cols = document.getElementById('tg-indicator-cols');
    return cols ? [].slice.call(cols.querySelectorAll('.tg-opt')) : [];
  }

  function renderTrigger() {
    var S = state.get();
    var meta = S.indicatorMeta[S.indicator] || {};
    var label = document.getElementById('tg-indicator-label');
    var group = document.getElementById('tg-indicator-group');
    if (label) label.textContent = meta.label || S.indicator;
    if (group) group.textContent = meta.group ? meta.group + ' ·' : '';
  }

  function open() {
    var menu = document.getElementById('tg-indicator-menu');
    var search = document.getElementById('tg-indicator-search');
    if (!menu) return;
    T.popovers.close();
    menu.classList.remove('hidden');
    document
      .getElementById('tg-indicator')
      .setAttribute('aria-expanded', 'true');
    menuOpen = true;
    if (search) {
      search.value = '';
      render('');
      search.focus();
    }
  }

  function close() {
    var menu = document.getElementById('tg-indicator-menu');
    if (!menu) return;
    var trigger = document.getElementById('tg-indicator');
    // Hiding the element that holds focus strands the caret on <body>, so a
    // keyboard reader who picks an indicator loses their place on the page.
    // Hand focus back to the control they opened.
    var strand = menu.contains(document.activeElement);
    menu.classList.add('hidden');
    if (trigger) {
      trigger.setAttribute('aria-expanded', 'false');
      if (strand) trigger.focus();
    }
    menuOpen = false;
  }

  function init() {
    var trigger = document.getElementById('tg-indicator');
    var search = document.getElementById('tg-indicator-search');
    var menu = document.getElementById('tg-indicator-menu');
    if (!trigger || !menu) return;

    trigger.addEventListener('click', function (ev) {
      ev.stopPropagation();
      if (menuOpen) close();
      else open();
    });
    if (search) {
      search.addEventListener('input', function () {
        render(search.value);
      });
      search.addEventListener('keydown', function (ev) {
        // Enter takes the first match, which is what a filter box is for.
        if (ev.key === 'Enter') {
          var first = menu.querySelector('.tg-opt');
          if (first) first.click();
          return;
        }
        // Down from the box steps into the list. The container claims
        // role=listbox, and a listbox that ignores the arrow keys is a promise
        // to assistive technology that the page does not keep.
        if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
          var opts = options();
          if (!opts.length) return;
          ev.preventDefault();
          (ev.key === 'ArrowDown' ? opts[0] : opts[opts.length - 1]).focus();
        }
      });
    }

    // Arrow keys within the list, in the order the eye reads it: down a column,
    // then to the top of the next.
    menu.addEventListener('keydown', function (ev) {
      if (!ev.target.classList || !ev.target.classList.contains('tg-opt'))
        return;
      var opts = options();
      var i = opts.indexOf(ev.target);
      if (i === -1) return;
      if (ev.key === 'ArrowDown') {
        ev.preventDefault();
        (opts[i + 1] || opts[0]).focus();
      } else if (ev.key === 'ArrowUp') {
        ev.preventDefault();
        // Up from the first option returns to the filter, which is where the
        // reader came from.
        if (i === 0) search.focus();
        else opts[i - 1].focus();
      } else if (ev.key === 'Home') {
        ev.preventDefault();
        opts[0].focus();
      } else if (ev.key === 'End') {
        ev.preventDefault();
        opts[opts.length - 1].focus();
      } else if (ev.key.length === 1 && /\S/.test(ev.key)) {
        // Typing anywhere in the list goes on filtering rather than being
        // swallowed by whichever option happens to hold focus.
        search.focus();
      }
    });
    menu.addEventListener('click', function (ev) {
      ev.stopPropagation();
    });
    document.addEventListener('click', function () {
      if (menuOpen) close();
    });
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && menuOpen) {
        close();
        trigger.focus();
      }
      // A menu open behind a Tab is a menu the reader has already left.
      if (ev.key === 'Tab' && menuOpen && !menu.contains(ev.target)) close();
    });
  }

  window.Targeting.menu = {
    index: index,
    render: render,
    renderTrigger: renderTrigger,
    open: open,
    close: close,
    init: init,
    isOpen: function () {
      return menuOpen;
    },
  };
})();

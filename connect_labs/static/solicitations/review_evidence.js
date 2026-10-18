/*
 * A review read beside the response it judges.
 *
 * The answers are plain text in [data-answer] elements; every highlight is
 * built here from the review's claims (connect_labs/solicitations/evidence.py),
 * whose quote cites carry [start, end) offsets into that text. Nothing from a
 * response is ever inserted as HTML: segments are text nodes.
 *
 * data-mode="view"  the response page: switch reviews, click a statement to
 *                   see its passages, evidence off / selected / all.
 * data-mode="edit"  the review form: the same, plus editing the review's own
 *                   statements -- remove a cited passage, or select text in an
 *                   answer and attach it. The claims travel in the form's
 *                   `claims_json` field.
 */
(function () {
  'use strict';

  var root = document.querySelector('[data-evidence-root]');
  var dataEl = document.getElementById('evidence-data');
  if (!root || !dataEl) return;

  var data = JSON.parse(dataEl.textContent || '{}');
  var editing = root.getAttribute('data-mode') === 'edit';
  var legend = data.legend || [];
  var colours = {};
  var labels = {};
  legend.forEach(function (row) {
    colours[row.about] = row.colour;
    labels[row.about] = row.label;
  });

  var answersPane = root.querySelector('[data-pane="answers"]');
  var reviewPane = root.querySelector('[data-pane="review"]');
  var answers = Array.prototype.map.call(
    root.querySelectorAll('[data-question]'),
    function (el) {
      var p = el.querySelector('[data-answer]');
      return {
        el: el,
        id: el.getAttribute('data-question'),
        p: p,
        text: p ? p.textContent : '',
        absent: el.querySelector('[data-absent-note]'),
      };
    },
  );

  var claimsInput = editing ? root.querySelector('[name="claims_json"]') : null;
  var claims = [];
  var selected = {};
  var evidenceMode = 'all';
  var pending = null; // edit mode: the text selected in an answer, waiting to be attached

  if (editing) {
    try {
      claims = JSON.parse((claimsInput && claimsInput.value) || '[]') || [];
    } catch (e) {
      claims = [];
    }
  } else if ((data.reviews || []).length) {
    claims = data.reviews[0].claims || [];
  }

  function colourOf(about) {
    return colours[about] || '#64748b';
  }
  function tint(hex, alpha) {
    var n = parseInt(hex.slice(1), 16);
    return (
      'rgba(' +
      ((n >> 16) & 255) +
      ',' +
      ((n >> 8) & 255) +
      ',' +
      (n & 255) +
      ',' +
      alpha +
      ')'
    );
  }
  function anySelected() {
    return Object.keys(selected).length > 0;
  }
  function passageCites(claim) {
    return (claim.cites || []).filter(function (c) {
      return c.question;
    });
  }

  // ---- painting the answers --------------------------------------------------

  function shown() {
    if (evidenceMode === 'off') return [];
    if (evidenceMode === 'selected')
      return claims.filter(function (c) {
        return selected[c.id];
      });
    return claims;
  }

  function paint() {
    var visible = shown();
    answers.forEach(function (a) {
      var spans = [];
      var bar = null;
      var missing = [];
      visible.forEach(function (claim) {
        var own = passageCites(claim);
        own.forEach(function (cite) {
          if (cite.question !== a.id) return;
          if (cite.whole) bar = bar || colourOf(claim.about);
          else if (cite.absent) missing.push(claim.text);
          else if (typeof cite.start === 'number')
            spans.push({ s: cite.start, e: cite.end, claim: claim });
        });
        // A judgement drawn from the whole response, selected on its own:
        // every answer carries its bar.
        if (
          !own.length &&
          claim.basis === 'synthesized' &&
          evidenceMode === 'selected'
        )
          bar = bar || colourOf(claim.about);
      });
      a.el.style.borderLeftColor = bar || 'transparent';
      a.el.style.outline = missing.length ? '1.5px dashed #94a3b8' : '';
      a.el.style.outlineOffset = missing.length ? '4px' : '';
      if (a.absent) {
        a.absent.textContent = missing.length
          ? 'Nothing here about: ' + missing.join('; ')
          : '';
        a.absent.classList.toggle('hidden', !missing.length);
      }
      var cited = spans.length || bar || missing.length;
      a.el.style.opacity =
        evidenceMode === 'all' && claims.length && !cited ? '0.55' : '';
      if (a.p) renderSegments(a, spans);
    });
    drawMap();
    var status = root.querySelector('[data-evidence-status]');
    if (status) {
      var n = visible.reduce(function (t, c) {
        return (
          t +
          passageCites(c).filter(function (x) {
            return x.quote;
          }).length
        );
      }, 0);
      status.textContent =
        evidenceMode === 'off'
          ? ''
          : evidenceMode === 'selected' && !anySelected()
            ? 'click a statement'
            : n + ' passage' + (n === 1 ? '' : 's');
    }
    root.querySelectorAll('[data-evidence-mode]').forEach(function (b) {
      var on = b.getAttribute('data-evidence-mode') === evidenceMode;
      b.style.background = on ? '#4f46e5' : '';
      b.style.color = on ? '#fff' : '';
    });
  }

  function renderSegments(a, spans) {
    var text = a.text;
    a.p.textContent = '';
    if (!spans.length) {
      a.p.textContent = text;
      return;
    }
    var cuts = [0, text.length];
    spans.forEach(function (s) {
      cuts.push(s.s, s.e);
    });
    cuts = cuts
      .filter(function (v, i, arr) {
        return arr.indexOf(v) === i;
      })
      .sort(function (x, y) {
        return x - y;
      });
    for (var i = 0; i < cuts.length - 1; i++) {
      var from = cuts[i],
        to = cuts[i + 1];
      if (to <= from) continue;
      var over = spans.filter(function (s) {
        return s.s <= from && s.e >= to;
      });
      var piece = document.createTextNode(text.slice(from, to));
      if (!over.length) {
        a.p.appendChild(piece);
        continue;
      }
      var mark = document.createElement('mark');
      var lead = over[0].claim;
      var strong = over.some(function (s) {
        return selected[s.claim.id];
      });
      mark.style.background = tint(colourOf(lead.about), strong ? 0.38 : 0.18);
      mark.style.borderBottom = '2px solid ' + colourOf(lead.about);
      mark.style.color = 'inherit';
      mark.style.cursor = 'pointer';
      mark.style.borderRadius = '2px';
      mark.setAttribute(
        'data-claims',
        over
          .map(function (s) {
            return s.claim.id;
          })
          .join(' '),
      );
      mark.title = over
        .map(function (s) {
          return (labels[s.claim.about] || s.claim.about) + ': ' + s.claim.text;
        })
        .join('\n');
      mark.appendChild(piece);
      a.p.appendChild(mark);
    }
  }

  function drawMap() {
    var map = root.querySelector('[data-margin-map]');
    if (!map || !answersPane) return;
    map.textContent = '';
    var total = answersPane.scrollHeight || 1;
    var base = answersPane.getBoundingClientRect().top - answersPane.scrollTop;
    answersPane.querySelectorAll('mark[data-claims]').forEach(function (mark) {
      var tick = document.createElement('button');
      tick.type = 'button';
      tick.title = mark.title;
      tick.style.cssText =
        'position:absolute;left:0;width:100%;min-height:3px;border:0;padding:0;cursor:pointer;';
      tick.style.top =
        ((mark.getBoundingClientRect().top - base) / total) * 100 + '%';
      tick.style.height =
        Math.max(3, (mark.offsetHeight / total) * map.clientHeight) + 'px';
      tick.style.background = mark.style.borderBottomColor;
      tick.addEventListener('click', function () {
        mark.scrollIntoView({ block: 'center', behavior: 'smooth' });
      });
      map.appendChild(tick);
    });
  }

  // ---- choosing what to look at ------------------------------------------------

  function select(ids, scroll) {
    selected = {};
    ids.forEach(function (id) {
      selected[id] = true;
    });
    if (evidenceMode === 'off') evidenceMode = 'selected';
    paint();
    root.querySelectorAll('[data-claim]').forEach(function (b) {
      var on = !!selected[b.getAttribute('data-claim')] && isCurrent(b);
      b.style.borderColor = on ? '#4f46e5' : '';
      b.style.background = on ? '#eef2ff' : '';
    });
    if (scroll) {
      showPane('answers');
      var first =
        answersPane &&
        Array.prototype.find.call(
          answersPane.querySelectorAll('mark[data-claims]'),
          function (m) {
            return m
              .getAttribute('data-claims')
              .split(' ')
              .some(function (id) {
                return selected[id];
              });
          },
        );
      var target =
        first ||
        (answersPane &&
          Array.prototype.find.call(
            answersPane.querySelectorAll('[data-question]'),
            function (q) {
              return (
                (q.style.borderLeftColor &&
                  q.style.borderLeftColor !== 'transparent') ||
                q.style.outline
              );
            },
          ));
      if (target)
        target.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }

  var currentReview =
    !editing && (data.reviews || []).length ? String(data.reviews[0].id) : null;
  function isCurrent(button) {
    return editing || button.getAttribute('data-review') === currentReview;
  }

  root.addEventListener('click', function (ev) {
    var claimBtn = ev.target.closest('[data-claim]');
    if (
      claimBtn &&
      !ev.target.closest(
        '[data-cite-remove],[data-attach],select,textarea,input',
      )
    ) {
      select([claimBtn.getAttribute('data-claim')], true);
      return;
    }
    var mark = ev.target.closest('mark[data-claims]');
    if (mark) {
      select(mark.getAttribute('data-claims').split(' '), false);
      var btn = root.querySelector(
        '[data-claim="' +
          mark.getAttribute('data-claims').split(' ')[0] +
          '"]' +
          (currentReview ? '[data-review="' + currentReview + '"]' : ''),
      );
      if (btn) {
        showPane('review');
        btn.scrollIntoView({ block: 'center', behavior: 'smooth' });
      }
      return;
    }
    var modeBtn = ev.target.closest('[data-evidence-mode]');
    if (modeBtn) {
      evidenceMode = modeBtn.getAttribute('data-evidence-mode');
      paint();
      return;
    }
    var tab = ev.target.closest('[data-review-tab]');
    if (tab) {
      switchReview(tab.getAttribute('data-review-tab'));
      return;
    }
    var paneTab = ev.target.closest('[data-pane-tab]');
    if (paneTab) {
      showPane(paneTab.getAttribute('data-pane-tab'));
      return;
    }
    if (ev.target.closest('[data-back-to-review]')) {
      showPane('review');
    }
  });

  function switchReview(id) {
    currentReview = String(id);
    var review = (data.reviews || []).find(function (r) {
      return String(r.id) === currentReview;
    });
    claims = review ? review.claims || [] : [];
    selected = {};
    root.querySelectorAll('[data-review-panel]').forEach(function (p) {
      p.classList.toggle(
        'hidden',
        p.getAttribute('data-review-panel') !== currentReview,
      );
    });
    root.querySelectorAll('[data-review-tab]').forEach(function (t) {
      var on = t.getAttribute('data-review-tab') === currentReview;
      t.style.background = on ? '#4f46e5' : '';
      t.style.color = on ? '#fff' : '';
      t.style.borderColor = on ? '#4f46e5' : '';
    });
    paint();
  }

  // Below lg the two panes are tabs.
  var wide = window.matchMedia('(min-width: 1024px)');
  function showPane(name) {
    if (wide.matches || !reviewPane || !answersPane) return;
    answersPane.classList.toggle('hidden', name !== 'answers');
    reviewPane.classList.toggle('hidden', name !== 'review');
    root.querySelectorAll('[data-pane-tab]').forEach(function (t) {
      var on = t.getAttribute('data-pane-tab') === name;
      t.classList.toggle('bg-white', on);
      t.classList.toggle('shadow-sm', on);
    });
    var back = root.querySelector('[data-back-to-review]');
    if (back)
      back.classList.toggle('hidden', !(name === 'answers' && anySelected()));
  }
  function layout() {
    if (!reviewPane || !answersPane) return;
    if (wide.matches) {
      answersPane.classList.remove('hidden');
      reviewPane.classList.remove('hidden');
    } else {
      showPane(reviewPane.classList.contains('hidden') ? 'answers' : 'review');
    }
    drawMap();
  }
  wide.addEventListener('change', layout);
  window.addEventListener('resize', drawMap);

  // ---- editing a review's statements ------------------------------------------

  function offsetIn(p, node, offset) {
    var walker = document.createTreeWalker(p, NodeFilter.SHOW_TEXT);
    var total = 0;
    while (walker.nextNode()) {
      if (walker.currentNode === node) return total + offset;
      total += walker.currentNode.textContent.length;
    }
    return node === p ? total : null;
  }

  function captureSelection() {
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return;
    var range = sel.getRangeAt(0);
    var a = answers.find(function (x) {
      return (
        x.p &&
        x.p.contains(range.startContainer) &&
        x.p.contains(range.endContainer)
      );
    });
    if (!a) return;
    var start = offsetIn(a.p, range.startContainer, range.startOffset);
    var end = offsetIn(a.p, range.endContainer, range.endOffset);
    if (start === null || end === null || end <= start) return;
    while (start < end && /\s/.test(a.text[start])) start++;
    while (end > start && /\s/.test(a.text[end - 1])) end--;
    if (end <= start) return;
    pending = {
      question: a.id,
      quote: a.text.slice(start, end),
      start: start,
      end: end,
    };
    var hint = root.querySelector('[data-pending]');
    if (hint) {
      hint.textContent =
        'Selected: “' +
        (pending.quote.length > 80
          ? pending.quote.slice(0, 80) + '…'
          : pending.quote) +
        '” — choose “Attach selected text” on a statement.';
      hint.classList.remove('hidden');
    }
    renderEditor();
  }

  function save() {
    if (claimsInput) claimsInput.value = JSON.stringify(claims);
  }

  function nextId() {
    var n = claims.length + 1;
    while (
      claims.some(function (c) {
        return c.id === 'c' + n;
      })
    )
      n++;
    return 'c' + n;
  }

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      node.setAttribute(k, attrs[k]);
    });
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderEditor() {
    var box = root.querySelector('[data-claims-editor]');
    if (!box) return;
    box.textContent = '';
    claims.forEach(function (claim, index) {
      var card = el('div', {
        'data-claim': claim.id,
        class: 'border border-gray-200 rounded-lg p-2.5 mb-2 cursor-pointer',
      });
      card.style.borderLeft = '4px solid ' + colourOf(claim.about);
      var top = el('div', { class: 'flex gap-2 mb-1.5' });
      var about = el('select', { class: 'base-dropdown !py-1 text-xs flex-1' });
      legend.forEach(function (row) {
        var opt = el('option', { value: row.about }, row.label);
        if (row.about === claim.about) opt.selected = true;
        about.appendChild(opt);
      });
      about.addEventListener('change', function () {
        claim.about = about.value;
        card.style.borderLeft = '4px solid ' + colourOf(claim.about);
        save();
        paint();
      });
      var drop = el(
        'button',
        {
          type: 'button',
          class: 'text-xs text-gray-400 hover:text-red-600 px-1',
          title: 'Remove this statement',
          'data-cite-remove': '',
        },
        'Remove',
      );
      drop.addEventListener('click', function () {
        claims.splice(index, 1);
        save();
        renderEditor();
        paint();
      });
      top.appendChild(about);
      top.appendChild(drop);
      card.appendChild(top);
      var text = el('textarea', {
        rows: '2',
        class: 'base-input !h-auto py-1.5 text-sm w-full',
      });
      text.value = claim.text || '';
      text.addEventListener('input', function () {
        claim.text = text.value;
        save();
      });
      card.appendChild(text);
      var chips = el('div', { class: 'flex flex-wrap gap-1 mt-1.5' });
      (claim.cites || []).forEach(function (cite, ci) {
        var label = cite.source
          ? (cite.source === 'web' ? 'web: ' : 'labs: ') +
            (cite.detail || cite.url || '')
          : cite.absent
            ? 'nothing in an answer'
            : cite.whole
              ? 'a whole answer'
              : '“' +
                (cite.quote || '').slice(0, 48) +
                ((cite.quote || '').length > 48 ? '…' : '') +
                '”';
        var chip = el(
          'span',
          {
            class:
              'inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-gray-100 text-[11px] text-gray-700',
          },
          label,
        );
        var x = el(
          'button',
          {
            type: 'button',
            class: 'text-gray-400 hover:text-red-600',
            title: 'Remove this evidence',
            'data-cite-remove': '',
          },
          '×',
        );
        x.addEventListener('click', function () {
          claim.cites.splice(ci, 1);
          claim.basis = passageCites(claim).some(function (c) {
            return c.quote;
          })
            ? 'quoted'
            : 'synthesized';
          save();
          renderEditor();
          paint();
        });
        chip.appendChild(x);
        chips.appendChild(chip);
      });
      var attach = el(
        'button',
        {
          type: 'button',
          class:
            'text-[11px] text-brand-indigo hover:underline disabled:text-gray-300 disabled:no-underline',
          'data-attach': '',
        },
        '+ Attach selected text',
      );
      attach.disabled = !pending;
      attach.addEventListener('click', function () {
        if (!pending) return;
        claim.cites = claim.cites || [];
        claim.cites.push({
          question: pending.question,
          quote: pending.quote,
          start: pending.start,
          end: pending.end,
        });
        claim.basis = 'quoted';
        pending = null;
        var hint = root.querySelector('[data-pending]');
        if (hint) hint.classList.add('hidden');
        window.getSelection().removeAllRanges();
        save();
        renderEditor();
        select([claim.id], false);
      });
      chips.appendChild(attach);
      card.appendChild(chips);
      box.appendChild(card);
    });
  }

  if (editing) {
    var add = root.querySelector('[data-add-claim]');
    if (add) {
      add.addEventListener('click', function () {
        var kind = root.querySelector('[data-add-claim-about]');
        claims.push({
          id: nextId(),
          about: kind ? kind.value : 'concern',
          text: '',
          basis: 'synthesized',
          cites: [],
        });
        save();
        renderEditor();
        paint();
      });
    }
    document.addEventListener('mouseup', function () {
      setTimeout(captureSelection, 0);
    });
    document.addEventListener('keyup', function (ev) {
      if (ev.shiftKey) captureSelection();
    });
    var form = root.querySelector('form');
    if (form) form.addEventListener('submit', save);
    save();
    renderEditor();
  }

  layout();
  paint();
})();

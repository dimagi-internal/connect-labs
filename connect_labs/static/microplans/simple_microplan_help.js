/* Simple Microplan in-page help.
   The articles are user_docs/simple-microplan.md, split per "## " section by the
   editor view (simple_views.help_sections) -- the same file the Labs help site
   builds from and the per-merge docs updater keeps current. Nothing here is
   hand-written help text. */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const sections = JSON.parse(($('mp-help') || {}).textContent || '[]');
  const fab = $('mp-help-btn');
  const panel = $('mp-help-panel');
  const body = $('mp-help-body');
  const q = $('mp-help-q');
  if (!fab || !panel) return;

  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  const words = (s) =>
    String(s || '')
      .toLowerCase()
      .split(/\s+/)
      .filter(Boolean);

  // Every word must appear in the article; title hits rank first.
  function search(query) {
    const ws = words(query);
    if (!ws.length) return sections;
    return sections
      .map((s) => {
        const title = s.title.toLowerCase();
        const text = s.text.toLowerCase();
        if (!ws.every((w) => title.includes(w) || text.includes(w)))
          return null;
        const score = ws.reduce(
          (n, w) => n + (title.includes(w) ? 10 : 0) + text.split(w).length - 1,
          0,
        );
        return { s, score };
      })
      .filter(Boolean)
      .sort((a, b) => b.score - a.score)
      .map((x) => x.s);
  }

  function snippet(text, ws) {
    const lower = text.toLowerCase();
    let at = ws.length ? lower.indexOf(ws[0]) : 0;
    if (at < 0) at = 0;
    const start = Math.max(0, at - 50);
    let out =
      (start ? '…' : '') +
      text.slice(start, start + 150) +
      (start + 150 < text.length ? '…' : '');
    out = esc(out);
    ws.forEach((w) => {
      const re = new RegExp(
        '(' + w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')',
        'gi',
      );
      out = out.replace(re, '<mark>$1</mark>');
    });
    return out;
  }

  function showList(query) {
    const ws = words(query);
    const hits = search(query);
    let html = ws.length
      ? '<div class="help-bubble you">' + esc(query) + '</div>'
      : '<div class="help-bubble">Hi! Search below, or pick a topic to see how each part of this page works.</div>';
    if (!hits.length) {
      html +=
        '<div class="help-bubble">Nothing matches “' +
        esc(query) +
        '”. Try other words, or open the full guide below.</div>';
    } else {
      if (ws.length) {
        html +=
          '<div class="help-bubble">' +
          hits.length +
          ' topic' +
          (hits.length === 1 ? '' : 's') +
          ' found:</div>';
      }
      html += hits
        .map(
          (s) =>
            '<button type="button" class="help-topic" data-id="' +
            esc(s.id) +
            '"><b>' +
            esc(s.title) +
            '</b>' +
            '<span>' +
            snippet(s.text, ws) +
            '</span></button>',
        )
        .join('');
    }
    body.innerHTML = html;
    body.scrollTop = 0;
    body.querySelectorAll('.help-topic').forEach((b) => {
      b.onclick = () => showArticle(b.dataset.id);
    });
  }

  function showArticle(id) {
    const s = sections.find((x) => x.id === id);
    if (!s) {
      showList(q.value);
      return;
    }
    const tpl = document.getElementById('mp-help-art-' + s.id);
    body.textContent = '';
    const back = document.createElement('button');
    back.type = 'button';
    back.className = 'help-back';
    back.textContent = '← All topics';
    const article = document.createElement('article');
    article.className = 'help-article';
    const h = document.createElement('h3');
    h.textContent = s.title;
    article.appendChild(h);
    // the article itself is a server-rendered, sanitised <template>: clone it
    if (tpl) article.appendChild(tpl.content.cloneNode(true));
    body.append(back, article);
    body.scrollTop = 0;
    body.querySelector('.help-back').onclick = () => showList(q.value);
  }

  function open(id) {
    panel.hidden = false;
    fab.classList.add('open');
    if (id) showArticle(id);
    else showList(q.value);
    if (!id) q.focus();
  }
  function close() {
    panel.hidden = true;
    fab.classList.remove('open');
  }

  fab.addEventListener('click', () => (panel.hidden ? open() : close()));
  $('mp-help-close').addEventListener('click', close);
  q.addEventListener('input', () => showList(q.value));
  panel.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      close();
      fab.focus();
    }
  });
  document.querySelectorAll('[data-help-open]').forEach((b) => {
    b.addEventListener('click', (e) => {
      e.preventDefault();
      open(b.dataset.helpOpen);
    });
  });
})();

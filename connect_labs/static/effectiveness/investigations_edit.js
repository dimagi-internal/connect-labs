/* The Investigations Tracker's editor (signed-in, Dimagi staff). A form over the content JSON: every input writes
   into one object, and Save sends that object for the server to check (investigations.content.clean). Nothing here
   publishes. Built with textContent throughout. */
(function () {
  'use strict';

  const E = JSON.parse(
    document.getElementById('investigations-editor').textContent,
  );
  const C = E.content;
  /* A draft the server refused comes back as it was sent, so make sure the parts the form walks exist. */
  C.stats = C.stats || {};
  C.groups = Array.isArray(C.groups) ? C.groups : [];
  C.footer = Array.isArray(C.footer) ? C.footer : [];
  C.programs = Array.isArray(C.programs) ? C.programs : [];
  const host = document.getElementById('ited-fields');
  const form = document.getElementById('ited-form');
  let dirty = false;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function touch() {
    dirty = true;
  }

  /* One labelled input bound to obj[key]. kind: text | area | number | select | list (comma-separated names). */
  function input(obj, key, label, kind, opts) {
    opts = opts || {};
    const wrap = el('label', 'f' + (opts.wide ? ' wide' : ''));
    wrap.append(el('span', 'fl', label));
    let node;
    if (kind === 'area') {
      node = el('textarea');
      node.rows = opts.rows || 3;
    } else if (kind === 'select') {
      node = el('select');
      opts.options.forEach((o) =>
        node.append(new Option(o === '' ? '(none)' : o, o)),
      );
    } else {
      node = el('input');
      node.type = kind === 'number' ? 'number' : 'text';
      if (kind === 'number') node.min = '0';
    }
    const current = obj[key];
    node.value =
      kind === 'list'
        ? (current || []).join(', ')
        : current == null
          ? ''
          : current;
    if (opts.placeholder) node.placeholder = opts.placeholder;
    node.addEventListener('input', () => {
      if (kind === 'number')
        obj[key] = node.value === '' ? 0 : Number(node.value);
      else if (kind === 'list')
        obj[key] = node.value
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean);
      else obj[key] = node.value;
      touch();
      if (opts.onchange) opts.onchange();
    });
    wrap.append(node);
    if (opts.hint) wrap.append(el('span', 'hint', opts.hint));
    return wrap;
  }
  function button(text, onclick, cls) {
    const b = el(
      'button',
      'button button-sm outline-style' + (cls ? ' ' + cls : ''),
      text,
    );
    b.type = 'button';
    b.addEventListener('click', onclick);
    return b;
  }
  function section(title, note) {
    const s = el('section', 'card');
    s.append(el('h2', null, title));
    if (note) s.append(el('p', 'sub', note));
    const grid = el('div', 'grid');
    s.append(grid);
    host.append(s);
    return grid;
  }

  function pageFields() {
    const g = section(
      'The page',
      'The heading, the introduction and the figures across the top.',
    );
    g.append(
      input(C, 'title', 'Title', 'text', { wide: true }),
      input(C, 'lede', 'Introduction', 'area', { wide: true }),
      input(C.stats, 'visits', 'Verified visits to date', 'number', {
        hint: "Shown until Connect's own count is available; after that the page shows Connect's.",
      }),
      input(C.stats, 'countries', 'Countries', 'text'),
      input(C.stats, 'organizations', 'Local organizations', 'text'),
      input(C, 'snapshot_date', 'Date of the hand-entered figures', 'text', {
        placeholder: '2026-11-18',
      }),
      input(C, 'models_note', 'Note on the models', 'text', { wide: true }),
      input(C, 'bar_note', "Date of GiveWell's funding bar", 'text', {
        placeholder: 'May 2026',
      }),
      input(C, 'model_link', "A model's link opens", 'select', {
        options: E.choices.model_links,
        hint: 'sheet: its Google Sheet. explorer: the Labs model explorer, which asks visitors to sign in today.',
      }),
    );
    const groups = section(
      'The three groups',
      'The heading and one-line description of each group.',
    );
    C.groups.forEach((gr) => {
      groups.append(
        input(gr, 'title', 'Group: ' + gr.key, 'text'),
        input(gr, 'def', 'Description', 'text', { wide: true }),
      );
    });
    const foot = section('Footer', 'The paragraphs under the table.');
    C.footer.forEach((para, i) => {
      foot.append(
        input(
          para,
          'heading',
          'Paragraph ' + (i + 1) + ' heading (optional)',
          'text',
        ),
        input(para, 'text', 'Text', 'area', { wide: true, rows: 4 }),
      );
    });
    const row = el('div', 'f wide');
    row.append(
      button('Add a footer paragraph', () => {
        C.footer.push({ heading: '', text: '' });
        touch();
        render();
      }),
    );
    foot.append(row);
  }

  function liveVisits(p) {
    const wanted = (p.connect && p.connect.services) || [];
    const got = wanted.filter((s) => s in E.delivery);
    if (!wanted.length)
      return 'No delivery types named, so the page shows the number you type here.';
    if (!got.length)
      return 'Connect has not counted these delivery types yet; the page shows the number you type here.';
    const n = got.reduce((sum, s) => sum + E.delivery[s], 0);
    return (
      "The page shows Connect's count instead: " +
      n.toLocaleString('en-US') +
      ' verified visits.'
    );
  }

  function programCard(p, i) {
    const d = el('details', 'prog');
    d.open = !!p._open;
    d.addEventListener('toggle', () => {
      p._open = d.open;
    });
    const sum = el('summary');
    sum.append(
      el('b', null, p.name || 'New program'),
      el('span', 'sub', ' ' + p.stage + ' · ' + p.group),
    );
    d.append(sum);
    const g = el('div', 'grid');
    p.connect = p.connect || { services: [] };
    p.ref = p.ref || { short: '', text: '', links: [] };
    const fig = p.model_key && E.figures[p.model_key];
    const known = Object.keys(E.delivery).sort().join(', ');
    g.append(
      input(p, 'name', 'Name', 'text', {
        onchange: () => {
          sum.firstChild.textContent = p.name || 'New program';
        },
      }),
      input(p, 'group', 'Group', 'select', { options: E.choices.groups }),
      input(p, 'stage', 'Stage', 'select', { options: E.choices.stages }),
      input(p, 'what', 'What it is', 'area', { wide: true }),
      input(p, 'now', 'Countries now', 'list', {
        placeholder: 'Nigeria, Kenya',
      }),
      input(p, 'place', 'Place within the country (optional)', 'text'),
      input(p, 'earlier', 'Earlier countries', 'text'),
      input(p, 'planned', 'Planned countries', 'text'),
      input(p, 'since', 'Started (year-month)', 'text', {
        placeholder: '2026-09',
      }),
      input(p, 'ended', 'Ended (year-month)', 'text', {
        placeholder: '2026-09',
      }),
      input(p, 'visits', 'Verified visits', 'number', { hint: liveVisits(p) }),
      input(p.connect, 'services', 'Connect delivery types', 'list', {
        placeholder: 'chc',
        hint:
          'Naming them makes the visit count live.' +
          (known ? ' Connect has: ' + known + '.' : ''),
      }),
      input(p, 'done', 'Delivery so far', 'text', { wide: true }),
      input(p, 'model', 'Model status', 'select', {
        options: E.choices.models,
      }),
      input(p, 'model_text', 'Model label', 'text'),
      input(p, 'model_key', 'Connect model', 'select', {
        options: [''].concat(E.choices.model_keys),
        hint: fig
          ? 'Live from the explorer: base ' +
            fig.base +
            'x, scenarios ' +
            fig.lo +
            'x to ' +
            fig.hi +
            'x.'
          : 'Only for a public Connect model. Its figures then come from the explorer.',
      }),
      input(p, 'sheet', 'Model sheet (https)', 'text', { wide: true }),
      input(p, 'note', 'Cost-effectiveness note', 'area', { wide: true }),
      input(p, 'next', 'Next step', 'text', { wide: true }),
      input(
        p.ref,
        'short',
        'Reference: short figure (shown in the table as text)',
        'text',
        { placeholder: 'about 9x' },
      ),
      input(p.ref, 'text', 'Reference: text', 'area', { wide: true }),
    );
    p.ref.links.forEach((l, n) => {
      const pair = { label: l[0], url: l[1] };
      const sync = () => {
        p.ref.links[n] = [pair.label, pair.url];
      };
      g.append(
        input(pair, 'label', 'Reference link ' + (n + 1) + ' label', 'text', {
          onchange: sync,
        }),
        input(pair, 'url', 'Address (https)', 'text', { onchange: sync }),
      );
    });
    const tools = el('div', 'f wide tools');
    tools.append(
      button('Add a reference link', () => {
        p.ref.links.push(['', '']);
        p._open = true;
        touch();
        render();
      }),
      button('Move up', () => {
        if (i > 0) {
          C.programs.splice(i - 1, 0, C.programs.splice(i, 1)[0]);
          touch();
          render();
        }
      }),
      button('Move down', () => {
        if (i < C.programs.length - 1) {
          C.programs.splice(i + 1, 0, C.programs.splice(i, 1)[0]);
          touch();
          render();
        }
      }),
      button(
        'Remove this program',
        () => {
          if (
            window.confirm(
              'Remove ' + (p.name || 'this program') + ' from the draft?',
            )
          ) {
            C.programs.splice(i, 1);
            touch();
            render();
          }
        },
        'danger',
      ),
    );
    g.append(tools);
    d.append(g);
    return d;
  }

  function programs() {
    const s = el('section', 'card');
    s.append(
      el('h2', null, 'Programs (' + C.programs.length + ')'),
      el(
        'p',
        'sub',
        'Open a program to change it. Within a group the page orders programs by stage, then by visits.',
      ),
    );
    C.programs.forEach((p, i) => s.append(programCard(p, i)));
    const add = button('Add a program', () => {
      C.programs.push({
        name: '',
        group: 'invest',
        stage: 'Idea',
        now: [],
        earlier: '',
        planned: '',
        place: '',
        visits: 0,
        since: '',
        ended: '',
        model: 'none',
        model_text: 'Not started',
        model_key: '',
        sheet: '',
        what: '',
        done: '',
        note: '',
        next: '',
        _open: true,
      });
      touch();
      render();
    });
    s.append(add);
    host.append(s);
  }

  function render() {
    host.textContent = '';
    pageFields();
    programs();
  }

  /* What goes to the server: the content without the editor's own marks or the empty shells it added. */
  function serialise() {
    const out = JSON.parse(JSON.stringify(C));
    out.programs.forEach((p) => {
      delete p._open;
      if (p.ref) {
        p.ref.links = p.ref.links.filter((l) => l[0] || l[1]);
        if (!p.ref.short && !p.ref.text && !p.ref.links.length) delete p.ref;
      }
      if (p.connect && !(p.connect.services || []).length) delete p.connect;
    });
    out.footer = out.footer.filter((f) => f.text);
    return out;
  }

  form.addEventListener('submit', () => {
    document.getElementById('ited-data').value = JSON.stringify(serialise());
    dirty = false;
  });
  document.querySelectorAll('form[data-confirm]').forEach((f) => {
    f.addEventListener('submit', (e) => {
      if (!window.confirm(f.dataset.confirm)) e.preventDefault();
    });
  });
  window.addEventListener('beforeunload', (e) => {
    if (dirty) {
      e.preventDefault();
      e.returnValue = '';
    }
  });
  render();
})();

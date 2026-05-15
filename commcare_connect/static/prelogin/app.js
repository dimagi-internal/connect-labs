// Hash router
function route() {
  const hash = window.location.hash.replace(/^#/, '') || '/';
  const path = hash.split('?')[0].split('#')[0] || '/';
  const pages = document.querySelectorAll('.page');
  let matched = false;
  pages.forEach((p) => {
    const isMatch = p.dataset.page === path;
    p.classList.toggle('active', isMatch);
    if (isMatch) matched = true;
  });
  if (!matched) {
    const home = document.querySelector('.page[data-page="/"]');
    if (home) home.classList.add('active');
  }
  document.querySelectorAll('#primary-nav a[data-route]').forEach((a) => {
    a.classList.toggle('active', a.dataset.route === path);
  });
  window.scrollTo({
    top: 0,
    behavior: 'instant' in window ? 'instant' : 'auto',
  });
}
window.addEventListener('hashchange', route);
window.addEventListener('DOMContentLoaded', route);

// Insights, two-axis filter (Program Type × Frontline Activity)
let activeProgram = 'all';
let activeLDVP = 'all';

function applyInsightFilters() {
  document
    .querySelectorAll('[data-page="/insights"] .insight-row')
    .forEach((row) => {
      const programs = (row.dataset.programs || '')
        .split(/\s+/)
        .filter(Boolean);
      const ldvps = (row.dataset.ldvp || '').split(/\s+/).filter(Boolean);
      const programMatch =
        activeProgram === 'all' || programs.includes(activeProgram);
      const ldvpMatch = activeLDVP === 'all' || ldvps.includes(activeLDVP);
      row.classList.toggle('is-hidden', !(programMatch && ldvpMatch));
    });
}

document.addEventListener('click', (e) => {
  const pill = e.target.closest('.filter-pills .pill');
  if (!pill) return;
  const type = pill.dataset.filterType;
  const value = pill.dataset.filterValue;
  if (!type || !value) return;
  // Only handle Insights filter types, Programs page filters use type="prog"
  if (type !== 'program' && type !== 'ldvp') return;
  if (type === 'program') activeProgram = value;
  if (type === 'ldvp') activeLDVP = value;
  pill.parentElement
    .querySelectorAll('.pill')
    .forEach((p) => p.classList.remove('active'));
  pill.classList.add('active');
  applyInsightFilters();
});

// Programs page — filter program cards by category
document.addEventListener('click', (e) => {
  const pill = e.target.closest(
    '#prog-filters .filter-pills .pill[data-filter-type="prog"]',
  );
  if (!pill) return;
  document
    .querySelectorAll('#prog-filters .filter-pills .pill')
    .forEach((p) => p.classList.remove('active'));
  pill.classList.add('active');
  const value = pill.dataset.filterValue;
  document.querySelectorAll('.prog-card[data-program]').forEach((card) => {
    const match = value === 'all' || card.dataset.category === value;
    card.classList.toggle('is-hidden', !match);
  });
  const divider = document.querySelector('.programs-section-divider');
  if (divider) {
    const hasVisibleSoon = !!document.querySelector(
      '.prog-card.is-soon:not(.is-hidden)',
    );
    divider.classList.toggle('is-hidden', !hasVisibleSoon);
  }
});

// Picker lists: click selects, auto-cycles every 2.5s
document.addEventListener('click', (e) => {
  const item = e.target.closest('.picker-scroll .picker-item');
  if (!item) return;
  const scroll = item.parentElement;
  scroll
    .querySelectorAll('.picker-item')
    .forEach((i) => i.classList.remove('is-active'));
  item.classList.add('is-active');
  scrollPickerToActive(scroll);
  // Pause auto-cycle for a moment after manual click
  scroll.dataset.paused = String(Date.now() + 6000);
});

function scrollPickerToActive(scroll) {
  const active = scroll.querySelector('.picker-item.is-active');
  if (!active) return;
  const targetTop =
    active.offsetTop - scroll.clientHeight / 2 + active.offsetHeight / 2;
  scroll.scrollTo({ top: targetTop, behavior: 'smooth' });
}

function cyclePickers() {
  document
    .querySelectorAll('.picker-scroll[data-cycle="1"]')
    .forEach((scroll) => {
      // Skip if recently clicked
      const paused = parseInt(scroll.dataset.paused || '0', 10);
      if (paused && Date.now() < paused) return;
      const items = Array.from(scroll.querySelectorAll('.picker-item'));
      if (items.length <= 1) return;
      const activeIdx = items.findIndex((i) =>
        i.classList.contains('is-active'),
      );
      const nextIdx = (activeIdx + 1) % items.length;
      items.forEach((item, idx) =>
        item.classList.toggle('is-active', idx === nextIdx),
      );
      scrollPickerToActive(scroll);
    });
}
setInterval(cyclePickers, 2500);

// How It Works, interactive step swap
const stepColors = {
  learn: '#16006D',
  deliver: '#5D70D2',
  verify: '#FC5F36',
  pay: '#1B998B',
};

function showStep(step) {
  const tabs = document.querySelectorAll('#step-tabs .step-tab');
  const details = document.querySelectorAll('.step-detail');
  tabs.forEach((t) => t.classList.toggle('active', t.dataset.step === step));
  details.forEach((d) => d.classList.toggle('active', d.dataset.step === step));

  // Update SVG cycle nodes, fill the active circle, white-out its icon
  document.querySelectorAll('.cycle-node-group').forEach((g) => {
    const stepKey = g.dataset.step;
    const color = stepColors[stepKey] || '#3843D0';
    const isActive = stepKey === step;
    const c = g.querySelector('circle');
    const icon = g.querySelector('.cycle-icon');
    if (c) {
      c.setAttribute('fill', isActive ? color : '#FFFFFF');
      c.setAttribute('stroke', color);
    }
    if (icon) {
      if (icon.tagName === 'text') {
        icon.setAttribute('fill', isActive ? '#FFFFFF' : color);
      } else {
        icon.setAttribute('stroke', isActive ? '#FFFFFF' : color);
      }
    }
  });
}

document.addEventListener('click', (e) => {
  const tab = e.target.closest('#step-tabs .step-tab');
  if (tab) {
    showStep(tab.dataset.step);
    return;
  }
  const node = e.target.closest('.cycle-node-group');
  if (node && node.dataset.step) {
    showStep(node.dataset.step);
  }
});

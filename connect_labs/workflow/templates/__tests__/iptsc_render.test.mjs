// The IPTsc dashboard render (iptsc_dashboard_render.js) is a JSX string that
// Babel transpiles IN THE BROWSER, so nothing in the Python suite executes it.
// This file does: it compiles the render exactly as the runner does, runs the
// pure calculation functions (ipt*) on synthetic fixtures, and renders every tab
// through a minimal fake React so a crash in any tab fails here, not on the page.
//
// Fixtures are synthetic: no real child, school or phone number.

import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';
import { transformSync } from '@babel/core';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(HERE, '..', 'iptsc_dashboard_render.js'), 'utf8');

// ---------------------------------------------------------------- harness

// A fake React good enough to walk a tree: createElement records, a "render"
// calls function components with their props, hooks keep per-call state.
function makeReact() {
  const React = {
    createElement(type, props, ...children) {
      return { type, props: Object.assign({}, props, { children }) };
    },
    useState(init) {
      return [typeof init === 'function' ? init() : init, () => {}];
    },
    useMemo(fn) {
      return fn();
    },
    useEffect() {},
    useRef(v) {
      return { current: v };
    },
    useCallback(fn) {
      return fn;
    },
    Fragment: 'Fragment',
  };
  return React;
}

function renderTree(node, depth = 0) {
  if (depth > 200) throw new Error('render too deep');
  if (node === null || node === undefined || typeof node === 'boolean')
    return 0;
  if (Array.isArray(node))
    return node.reduce((n, c) => n + renderTree(c, depth + 1), 0);
  if (typeof node !== 'object') return 1;
  if (typeof node.type === 'function')
    return renderTree(node.type(node.props), depth + 1);
  return 1 + renderTree(node.props && node.props.children, depth + 1);
}

function stubLabsReport() {
  const passthrough = (props) => ({
    type: 'div',
    props: {
      children: [
        props.children,
        props.right,
        props.sub,
        props.title,
        props.subtitle,
        props.badges,
        props.actions,
      ],
    },
  });
  return {
    VERSION: 7,
    Card: passthrough,
    SectionTitle: passthrough,
    Notice: passthrough,
    Pill: passthrough,
    Button: passthrough,
    ReportHeader: passthrough,
    Tabs: (props) => ({
      type: 'div',
      props: { children: props.tabs.map((t) => t.label) },
    }),
    StatTiles: (props) => {
      for (const t of props.tiles) {
        assert.ok(
          ['good', 'watch', 'bad', 'neutral', undefined, null].includes(t.tone),
          'StatTiles tone ' + t.tone,
        );
      }
      return {
        type: 'div',
        props: { children: props.tiles.map((t) => t.value) },
      };
    },
  };
}

function load(search = '') {
  const code = transformSync(SRC, {
    // The classic runtime (React.createElement), as the browser's Babel 7
    // standalone uses. Babel 8's default, "automatic", injects an import of
    // react/jsx-runtime, which neither the runner nor this sandbox has.
    presets: [['@babel/preset-react', { runtime: 'classic' }]],
    babelrc: false,
    configFile: false,
  }).code;
  const React = makeReact();
  const sandbox = {
    React,
    console,
    setTimeout,
    URL,
    URLSearchParams,
    Blob: class {},
    document: {
      createElement: () => ({}),
      body: { appendChild() {}, removeChild() {} },
    },
    window: {
      LabsReport: stubLabsReport(),
      location: {
        search,
        href: 'https://labs.example/labs/workflow/1/run/' + search,
      },
      history: { replaceState() {} },
      scrollTo() {},
      addEventListener() {},
      removeEventListener() {},
      alert() {},
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox;
}

const M = load();
const OPTS = {
  utcOffsetHours: 1,
  minChildren: 10,
  minDoses: 20,
  revisitDays: 3,
  schoolRadiusM: 250,
};

// ---------------------------------------------------------------- fixtures
// Monday 5 Oct 2026 is registration day. Times are UTC (as CommCare sends them);
// 08:00 UTC is 09:00 WAT.

const SCHOOL_GPS = '10.2000 11.3000 300 8';
const NEAR_GPS = '10.2005 11.3004 300 6'; // ~70 m away: same school
const FAR_GPS = '10.2500 11.3500 300 6'; // ~7.6 km away: another school

let seq = 0;
function day1(child, date, extra = {}) {
  seq += 1;
  return Object.assign(
    {
      child_id: child,
      form_name: 'Day 1 - Registration & Dose 1',
      username: 'flw_a',
      time_start: date + 'T07:50:00',
      time_end: date + 'T08:' + String(seq % 60).padStart(2, '0') + ':00',
      gps_raw: SCHOOL_GPS,
      eligible_to_proceed: 'yes',
      consent_3day: 'yes',
      consent_photo: 'consent.jpg',
      child_name: 'Child ' + child,
      school: 'Deba cps',
      sex: seq % 2 ? 'female' : 'male',
      age_years: '9',
      weight_kg: '27.4',
      dose_band: 'band_20_40',
      dot: 'yes',
      swallowed: 'yes',
      dose_prepared: 'yes',
      dose_photo: 'dose.jpg',
      child_said_name: 'yes',
      child_name_audio: 'name.amr',
      obs_result: 'no',
      obs_responsible: 'school',
      dose_completed: 'yes',
      course_status: 'on_track',
      return_confirm: 'yes',
      availability_next2: 'yes',
      holiday_intervening: 'no',
    },
    extra,
  );
}

function followUp(child, date, dose, extra = {}) {
  return Object.assign(
    {
      child_id: child,
      form_name: 'Follow-up Dose',
      username: 'flw_a',
      time_start: date + 'T08:00:00',
      time_end: date + 'T08:06:00',
      gps_raw: NEAR_GPS,
      expected_dose: String(dose),
      prior_doses: String(dose - 1),
      due_status: 'due',
      dot: 'yes',
      swallowed: 'yes',
      dose_prepared: 'yes',
      dose_photo: 'dose.jpg',
      child_said_name: 'yes',
      child_name_audio: 'name.amr',
      obs_result: 'no',
      obs_responsible: 'school',
      dose_completed: 'yes',
      course_status: dose >= 3 ? 'completed' : 'on_track',
    },
    extra,
  );
}

const MON = '2026-10-05';
const TUE = '2026-10-06';
const WED = '2026-10-07';
const THU = '2026-10-08';

const ROWS = [
  // A: textbook course, Mon/Tue/Wed
  day1('A', MON),
  followUp('A', TUE, 2),
  followUp('A', WED, 3),
  // B: doses 1 and 2, nothing since
  day1('B', MON),
  followUp('B', TUE, 2),
  // C: dose 1 only
  day1('C', MON),
  // D: dose 1 attempted but vomited, no re-dose
  day1('D', MON, {
    swallowed: 'vomited',
    dose_completed: 'no',
    redose_given: 'no',
  }),
  // E: dose 2 a day late (Wed), at another school's coordinates
  day1('E', MON),
  followUp('E', WED, 2, { gps_raw: FAR_GPS }),
  // F: registered on a Thursday with a supervisor override, spelled differently
  day1('F', THU, {
    school: 'debacps',
    supervisor_override: 'yes',
    gps_raw: NEAR_GPS,
  }),
  // G: a different school
  day1('G', MON, { school: 'Other Primary', gps_raw: FAR_GPS }),
  // A screening that ended before registration (no consent): not a child
  day1('', MON, {
    consent_3day: 'no',
    eligible_to_proceed: 'yes',
    dose_completed: '',
  }),
];

const CASES = [
  { case_id: 'B', course_status: 'stopped', doses_given_count: '2' }, // an AE stopped B's course
  {
    case_id: 'H',
    course_status: 'on_track',
    registration_date: TUE,
    school: 'Deba CPS',
    doses_given_count: '0',
  }, // case-only
];

function build(today) {
  const b = M.iptBuildChildren(ROWS, CASES, OPTS);
  const byId = Object.fromEntries(b.children.map((c) => [c.id, c]));
  return { b, byId, status: (id) => M.iptChildStatus(byId[id], today, OPTS) };
}

// ---------------------------------------------------------------- tests

test('the render compiles and declares WorkflowUI', () => {
  assert.equal(typeof M.WorkflowUI, 'function');
});

test('dates are West Africa Time calendar days', () => {
  // 23:30 UTC on the 5th is 00:30 WAT on the 6th
  assert.equal(
    M.iptLocalDate(M.iptParseTs('2026-10-05T23:30:00'), 1),
    '2026-10-06',
  );
  assert.equal(
    M.iptLocalDate(M.iptParseTs('2026-10-05T22:30:00Z'), 1),
    '2026-10-05',
  );
  assert.equal(M.iptDayDiff(MON, WED), 2);
  assert.equal(M.iptWeekStart(THU), MON);
  assert.equal(M.iptWeekday(MON), 1);
});

test('children are built from dosing forms and cases; screenings are not children', () => {
  const { b, byId } = build(THU);
  assert.deepEqual(Object.keys(byId).sort(), [
    'A',
    'B',
    'C',
    'D',
    'E',
    'F',
    'G',
    'H',
  ]);
  assert.equal(b.screenings.length, 1);
  assert.deepEqual(byId.A.doseDates, { 1: MON, 2: TUE, 3: WED });
  assert.equal(byId.A.successes, 3);
  assert.equal(
    byId.D.doseDates[1],
    undefined,
    'a vomited dose with no re-dose is not given',
  );
  assert.equal(byId.H.caseOnly, true);
  assert.equal(
    byId.B.courseStatus,
    'stopped',
    'the case outranks the last form',
  );
});

test('every child lands in one bucket as of a given day', () => {
  const { status } = build(THU);
  assert.equal(status('A').bucket, 'completed');
  assert.equal(status('B').bucket, 'stopped');
  assert.equal(status('C').bucket, 'overdue'); // dose 2 was due Tue: 2 days late on Thu
  assert.equal(status('C').overdueDays, 2);
  assert.equal(status('D').bucket, 'no_dose1');
  assert.equal(status('E').bucket, 'on_track'); // dose 3 due Thu
  assert.equal(status('F').bucket, 'on_track'); // registered today, dose 2 due Fri
  // Five days on, C is past the 3-day revisit window
  const later = build('2026-10-10');
  assert.equal(later.status('C').bucket, 'missed');
  assert.equal(later.status('C').overdueDays, 4);
});

test('dose cells say given, late, failed, missed or pending', () => {
  const { byId, status } = build(THU);
  const cell = (id, n) => M.iptDoseCell(byId[id], n, status(id), THU);
  assert.equal(cell('A', 3).state, 'given');
  assert.equal(cell('E', 2).state, 'late');
  assert.equal(cell('E', 2).late, 1);
  assert.equal(cell('C', 2).state, 'missed');
  assert.equal(cell('D', 1).state, 'failed');
  assert.equal(cell('F', 2).state, 'pending');
  assert.equal(cell('B', 3).state, 'ended');
});

test('the funnel only counts children who could already have had a dose', () => {
  const { b } = build(THU);
  const cas = M.iptCascade(b.children, THU, OPTS);
  assert.equal(cas.registered, 8);
  assert.equal(cas.d1.got, 6); // A B C E F G (D failed, H has no visit)
  // dose 2: A B E got it; C and G were due Tue; F registered today is not due
  assert.equal(cas.d2.got, 3);
  assert.equal(cas.d2.due, 5);
  // dose 3: A got it; B was due Wed; E's dose 3 is due today, so not yet counted
  assert.equal(cas.d3.got, 1);
  assert.equal(cas.d3.due, 2);
  // full course is graded once registration + 2 + 3 revisit days has passed
  assert.equal(
    cas.complete.due,
    1,
    'on Thursday only the finished child is gradeable',
  );
  const late = M.iptCascade(b.children, '2026-10-12', OPTS);
  assert.equal(late.complete.due, 7);
  assert.equal(cas.consecutive.got, 1);
});

test('schools merge spellings and nearby GPS, and split distant ones', () => {
  const { b } = build(THU);
  const s = M.iptClusterSchools(b.children, 250);
  assert.equal(
    s.byChild.A,
    s.byChild.F,
    '"Deba cps" and "debacps" are one school',
  );
  assert.equal(s.byChild.A, s.byChild.H, 'the case-only child joins by name');
  assert.notEqual(s.byChild.A, s.byChild.G);
  assert.equal(s.schools[s.byChild.A].label, 'Deba CPS');
  assert.ok(s.schools[s.byChild.A].variants.length >= 2);
});

test('intervals, drop reasons and attempts', () => {
  const { b } = build('2026-10-12');
  const iv = M.iptIntervals(b.children);
  assert.equal(iv.d1d2['1'], 2); // A, B
  assert.equal(iv.d1d2['2'], 1); // E
  const reasons = M.iptDropReasons(b.children, '2026-10-12', OPTS);
  const r = Object.fromEntries(reasons.map((x) => [x.reason, x.n]));
  assert.equal(r['Vomited, no successful re-dose'], 1);
  assert.equal(r['Stopped for safety (screen or adverse event)'], 1);
  const att = M.iptAttemptStats(b.children);
  assert.equal(att[1].forms, 7);
  assert.equal(att[1].vomited, 1);
  assert.equal(att[1].failed, 1);
});

test('authenticity checks', () => {
  const { b } = build(THU);
  const s = M.iptClusterSchools(b.children, 250);
  const a = M.iptAuthenticity(b.children, OPTS, s.byChild);
  assert.equal(a.weight_round5.value, 0);
  assert.equal(a.drift.n, 4, 'four follow-up doses with GPS');
  assert.ok(
    a.drift.value > 60 && a.drift.value < 100,
    'median drift is the near point, not the far one',
  );
  assert.equal(a.consent_photo.value, 0);
  // Every weight a multiple of 5 turns the check red
  const heaped = ROWS.map((r) =>
    r.form_name.startsWith('Day 1')
      ? Object.assign({}, r, { weight_kg: '30' })
      : r,
  );
  const hb = M.iptBuildChildren(heaped, [], OPTS);
  const ha = M.iptAuthenticity(hb.children, OPTS, null);
  assert.equal(ha.weight_round5.value, 100);
  assert.equal(ha.weight_round5.band, 'red');
  assert.equal(M.iptAuthFlag(ha).band, 'red');
  // Below five measurements a check is shown but never coloured
  const few = M.iptAuthenticity(hb.children.slice(0, 2), OPTS, null);
  assert.equal(few.weight_round5.band, null);
});

test('busiest hour is per worker, not the team added together', () => {
  // flw_a registers 7 children on Monday; adding 7 for flw_b in the same hour
  // must not make anyone's busiest hour 14.
  const extra = ['P', 'Q', 'R', 'S', 'T', 'U', 'V'].map((id) =>
    day1(id, MON, { username: 'flw_b' }),
  );
  const b = M.iptBuildChildren(ROWS.concat(extra), [], OPTS);
  const a = M.iptAuthenticity(b.children, OPTS, null);
  assert.ok(a.throughput.value <= 8, 'busiest hour ' + a.throughput.value);
  assert.ok(['flw_a', 'flw_b'].includes(a.throughput.extra.username));
});

test('school label: the most common spelling, unless a fuller form of it is nearly as common', () => {
  const kids = [
    ...Array.from({ length: 34 }, (_, i) => ({
      id: 'k' + i,
      school: 'Kwali Primary',
      gps: { lat: 10, lon: 11 },
    })),
    ...Array.from({ length: 30 }, (_, i) => ({
      id: 's' + i,
      school: 'kwali pry sch',
      gps: { lat: 10, lon: 11 },
    })),
    ...Array.from({ length: 5 }, (_, i) => ({
      id: 'd' + i,
      school: 'Deba',
      gps: { lat: 10.5, lon: 11 },
    })),
    ...Array.from({ length: 4 }, (_, i) => ({
      id: 'e' + i,
      school: 'Deba CPS',
      gps: { lat: 10.5, lon: 11 },
    })),
  ];
  const s = M.iptClusterSchools(kids, 250);
  assert.equal(
    s.schools[s.byChild.k0].label,
    'Kwali Primary',
    'a different abbreviation does not win',
  );
  assert.equal(
    s.schools[s.byChild.d0].label,
    'Deba CPS',
    'the fuller name of a bare place name wins',
  );
  // Case/spacing variants are one name, shown in their best-formatted form
  const spaced = M.iptClusterSchools(
    [
      ...Array.from({ length: 29 }, (_, i) => ({
        id: 'a' + i,
        school: 'debacps',
        gps: { lat: 10, lon: 11 },
      })),
      ...Array.from({ length: 26 }, (_, i) => ({
        id: 'b' + i,
        school: 'Deba CPS',
        gps: { lat: 10, lon: 11 },
      })),
      ...Array.from({ length: 21 }, (_, i) => ({
        id: 'c' + i,
        school: 'Deba',
        gps: { lat: 10, lon: 11 },
      })),
    ],
    250,
  );
  assert.equal(spaced.schools[spaced.byChild.a0].label, 'Deba CPS');
});

test('daily activity fills every calendar day, so a quiet day is a visible gap', () => {
  const { b } = build(THU);
  const days = M.iptDailyActivity(b.children).map((d) => d.date);
  assert.deepEqual(days, [MON, TUE, WED, THU]);
  const quiet = M.iptDailyActivity(
    M.iptBuildChildren([day1('X', MON), followUp('X', THU, 2)], [], OPTS)
      .children,
  );
  assert.deepEqual(
    quiet.map((d) => d.d1 + d.d2 + d.d3),
    [1, 0, 0, 1],
  );
});

test('a worker flag names each check with its value', () => {
  const heaped = ROWS.map((r) =>
    r.form_name.startsWith('Day 1')
      ? Object.assign({}, r, { weight_kg: '30' })
      : r,
  );
  const b = M.iptBuildChildren(heaped, [], OPTS);
  const flag = M.iptAuthFlag(M.iptAuthenticity(b.children, OPTS, null));
  assert.ok(
    flag.reds.includes('Weights that are a multiple of 5 kg: 100% of 7'),
    flag.reds.join(' | '),
  );
});

test('three duplicate checks: name; name + age; name + age + phone, all at one school', () => {
  const run = (extra) => {
    const b = M.iptBuildChildren(ROWS.concat(extra), [], OPTS);
    const s = M.iptClusterSchools(b.children, 250);
    return M.iptAuthenticity(b.children, OPTS, s.byChild);
  };
  const ids = (a, id) => a[id].extra.childIds.slice().sort();
  // A2: same name (other case) and age as A, same phone -> all three checks
  // A3: same name as A, different age, same phone       -> name only
  const a = run([
    day1('A2', MON, { child_name: 'child a', caregiver_phone: '08031234567' }),
    day1('A3', MON, {
      child_name: 'Child A',
      age_years: '11',
      caregiver_phone: '08031234567',
    }),
    day1('A4', MON, {
      child_name: 'CHILD A',
      caregiver_phone: '+234 803 123 4567',
    }),
  ]);
  assert.deepEqual(ids(a, 'dup_name'), ['A', 'A2', 'A3', 'A4']);
  assert.deepEqual(ids(a, 'dup_name_age'), ['A', 'A2', 'A4']);
  // A has no phone, so only A2 and A4 (+234 normalised to 0...) match on phone
  assert.deepEqual(ids(a, 'dup_name_age_phone'), ['A2', 'A4']);
  // total flagged, and in brackets the unique suspected children (groups)
  assert.equal(a.dup_name.value, 4);
  assert.equal(a.dup_name.extra.groups, 1);
  const spec = M.IPT_AUTH_CHECKS.find((c) => c.id === 'dup_name');
  assert.equal(M.iptCheckValueText(spec, a.dup_name), '4 (1 unique)');
  // another school never matches; no recorded age is not compared on age
  const other = run([
    day1('A2', MON, {
      child_name: 'Child A',
      school: 'Other Primary',
      gps_raw: '10.2500 11.3500 300 6',
    }),
    day1('A5', MON, { child_name: 'Child A', age_years: '' }),
  ]);
  assert.deepEqual(ids(other, 'dup_name'), ['A', 'A5']);
  assert.deepEqual(ids(other, 'dup_name_age'), []);
  // two separate pairs are two unique children
  const two = run([
    day1('A2', MON, { child_name: 'Child A' }),
    day1('C2', MON, { child_name: 'Child C' }),
  ]);
  assert.equal(two.dup_name_age.value, 4);
  assert.equal(two.dup_name_age.extra.groups, 2);
  // name-only never raises a worker's flag; name+age does
  assert.equal(
    M.IPT_AUTH_CHECKS.find((c) => c.id === 'dup_name').contributes,
    false,
  );
  assert.ok(
    M.iptAuthFlag(two).reasons.some((r) =>
      r.startsWith(
        'Possible duplicates: same name, age and school: 4 (2 unique)',
      ),
    ),
  );
});

test('the consent photo is found by filename, else by question id', () => {
  const images = [
    { blob_id: 'b-dose', name: 'dose.jpg', question_id: 'dosing/dose_photo' },
    {
      blob_id: 'b-consent',
      name: 'consent.jpg',
      question_id: 'consent/consent_photo',
    },
  ];
  assert.equal(M.iptConsentBlob(images, 'consent.jpg'), 'b-consent');
  assert.equal(M.iptConsentBlob(images, 'renamed.jpg'), 'b-consent');
  assert.equal(M.iptConsentBlob([images[0]], 'consent.jpg'), null);
  assert.equal(M.iptConsentBlob(undefined, 'x'), null);
});

test('the Connect visit link needs every piece', () => {
  assert.equal(
    M.iptConnectVisitUrl('org-slug', 2307, 'u1', 'v9'),
    'https://connect.dimagi.com/a/org-slug/opportunity/2307/user_visits/?user=u1&visit_id=v9',
  );
  assert.equal(M.iptConnectVisitUrl('', 2307, 'u1', 'v9'), null);
  assert.equal(M.iptConnectVisitUrl('org-slug', 2307, '', 'v9'), null);
});

test('compliance rows', () => {
  const { b } = build(THU);
  const rows = Object.fromEntries(
    M.iptCompliance(b.children, b.screenings).map((r) => [r.id, r]),
  );
  assert.equal(rows.reg_window.num, 1); // F on a Thursday
  assert.equal(rows.reg_window_override.num, 1);
  assert.equal(rows.screened_out.num, 1);
  assert.equal(rows.dot.num, rows.dot.den);
});

test('safety: adverse events and reactions with no log', () => {
  const rows = ROWS.concat([
    followUp('C', TUE, 2, { obs_result: 'yes', dose_completed: 'yes' }),
  ]);
  const b = M.iptBuildChildren(rows, [], OPTS);
  const ae = [
    {
      child_id: 'A',
      severity: 'moderate',
      which_dose: 'dose_2',
      symptoms: 'vomiting nausea',
      action: 'called_caregiver',
      current_status: 'worsening',
      followup_required: 'next_day',
      event_datetime: TUE + 'T10:00:00',
    },
    {
      child_id: 'E',
      severity: 'mild',
      which_dose: 'dose_1',
      symptoms: 'rash',
      action: 'observed_only',
      current_status: 'resolved',
      followup_required: 'no',
    },
  ];
  const s = M.iptSafety(b.children, b.screenings, ae, OPTS);
  assert.equal(s.aes.length, 2);
  assert.equal(s.moderatePlus, 1);
  assert.equal(s.bySymptom.vomiting, 1);
  assert.equal(s.lineList.length, 1, 'only moderate+ or referrals are listed');
  assert.equal(s.unlogged.length, 1, "C's reaction has no Adverse Event Log");
});

test('CSV cells cannot start a formula', () => {
  const csv = M.iptCsv(['a', 'b'], [['=HYPERLINK("x")', 'plain, with comma']]);
  assert.match(csv, /^a,b\n"'=HYPERLINK\(""x""\)","plain, with comma"$/);
});

test('every tab renders, with data and with none', () => {
  const tabs = [
    'overview',
    'completion',
    'reach',
    'safety',
    'protocol',
    'authenticity',
    'duplicates',
    'workers',
    'definitions',
  ];
  for (const tab of tabs) {
    for (const rows of [ROWS, []]) {
      const m = load('?tab=' + tab);
      const props = {
        definition: { name: 'IPTsc', config: {} },
        instance: { id: 1, state: {} },
        workers: [{ username: 'flw_a', name: 'Worker A' }],
        pipelines: {
          doses: { rows, metadata: {} },
          child_cases: { rows: rows.length ? CASES : [], metadata: {} },
          ae_log: {
            rows: [],
            metadata: {
              per_opp: { 2307: { error: 'CommCare HQ token expired' } },
            },
          },
        },
        links: { auditUrl: () => '/audit/', taskUrl: () => '/tasks/' },
        actions: {},
        onUpdateState: () => {},
      };
      const n = renderTree(m.WorkflowUI(props));
      assert.ok(n > 10, tab + ' rendered ' + n + ' nodes');
    }
  }
});

test('the child timeline renders for every child', () => {
  const { b, status } = build(THU);
  for (const c of b.children) {
    const el = M.React.createElement(M.ChildDrawer, {
      child: c,
      status: status(c.id),
      today: THU,
      opts: OPTS,
      nameOf: (u) => u,
      schoolLabel: 'Deba CPS',
      aes: [],
      onClose() {},
    });
    assert.ok(renderTree(el) > 10);
  }
});

test('the duplicate review renders rows of photo cards in every photo state', () => {
  const m = load('?tab=duplicates&dup=dup_name');
  const rows = ROWS.concat([
    day1('A2', MON, { child_name: 'child a', id: 'v-a2' }),
  ]);
  const props = {
    definition: { name: 'IPTsc', config: {} },
    instance: { id: 1, opportunity_id: 2307, state: {} },
    workers: [{ username: 'flw_a', name: 'Worker A' }],
    pipelines: {
      doses: { rows, metadata: {} },
      child_cases: { rows: [], metadata: {} },
      ae_log: { rows: [], metadata: {} },
    },
    links: {},
    actions: {},
    onUpdateState: () => {},
  };
  const tree = m.WorkflowUI(props);
  const text = [];
  (function walk(n) {
    if (n === null || n === undefined || typeof n === 'boolean') return;
    if (Array.isArray(n)) return n.forEach(walk);
    if (typeof n !== 'object') return text.push(String(n));
    if (typeof n.type === 'function') return walk(n.type(n.props));
    if (n.type === 'img') text.push('[img]');
    walk(n.props && n.props.children);
  })(tree);
  const flat = text.join('');
  assert.ok(flat.includes('2 registrations'), 'one group of two registrations');
  assert.ok(
    flat.includes('Open visit in Connect'),
    'each card links to its visit',
  );
  assert.ok(
    flat.includes('Loading photo'),
    'photos show as loading until fetched',
  );
  const b = m.iptBuildChildren(rows, [], OPTS);
  const child = b.children.find((c) => c.id === 'A2');
  assert.equal(child.visitId, 'v-a2');
  for (const photo of [
    undefined,
    { status: 'loading' },
    { status: 'ok', blob: 'b1' },
    { status: 'none' },
    { status: 'error' },
  ]) {
    const el = m.React.createElement(m.DupPhotoCard, {
      child,
      photo,
      oppId: 2307,
      schoolLabel: 'Deba CPS',
      workerName: 'Worker A',
      visitUrl: '/audit/visits/v-a2/',
      onOpenChild() {},
    });
    assert.ok(renderTree(el) > 5);
  }
});

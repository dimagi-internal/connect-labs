// IPTsc School Delivery dashboard -- render code (transpiled by Babel in the browser).
//
// Layout of this file:
//   1. Pure calculation functions (ipt*) -- no React, no DOM. Every number on the
//      page comes from these, and __tests__/iptsc_render.test.mjs runs them on
//      fixtures. Change a rule here and the test says what moved.
//   2. Small presentational components, declared at the TOP LEVEL. A component
//      declared inside WorkflowUI gets a new identity on every render, so React
//      remounts it and it loses its state (an open panel snaps shut) -- the bug
//      the CHC pages kept hitting.
//   3. WorkflowUI: reads props, applies the filter bar, renders the tabs.
//
// Data (see iptsc_dashboard.py): pipelines.doses (one row per dosing form,
// Connect), pipelines.child_cases (CommCare child cases), pipelines.ae_log
// (CommCare Adverse Event Log forms).

// ===========================================================================
// 1. Calculation
// ===========================================================================

var IPT_DEFAULTS = {
  utcOffsetHours: 1, // West Africa Time, no daylight saving
  minChildren: 10, // a rate over fewer children reads "too few to say"
  minDoses: 20,
  revisitDays: 3, // the follow-up form lets a missed dose be given within 3 days
  schoolRadiusM: 250, // Day 1 points closer than this belong to the same school
};

var DAY_MS = 86400000;

function iptStr(v) {
  if (v === null || v === undefined) return '';
  var s = String(v).trim();
  return s === 'None' || s === 'null' || s === 'undefined' ? '' : s;
}

function iptNum(v) {
  var s = iptStr(v);
  if (!s) return null;
  var n = Number(s);
  return isFinite(n) ? n : null;
}

function iptYes(v) {
  return iptStr(v).toLowerCase() === 'yes';
}

function iptPresent(v) {
  return iptStr(v) !== '';
}

// CommCare's timeStart/timeEnd arrive as UTC without a zone suffix.
function iptParseTs(s) {
  var t = iptStr(s);
  if (!t) return null;
  if (!/[zZ]$|[+-]\d\d:?\d\d$/.test(t)) t = t + 'Z';
  var ms = Date.parse(t);
  return isNaN(ms) ? null : ms;
}

function pad2(n) {
  return n < 10 ? '0' + n : '' + n;
}

// The calendar date of an instant in WAT (or any fixed offset).
function iptLocalDate(ms, offsetHours) {
  if (ms === null || ms === undefined) return null;
  var d = new Date(ms + (offsetHours || 0) * 3600000);
  return (
    d.getUTCFullYear() +
    '-' +
    pad2(d.getUTCMonth() + 1) +
    '-' +
    pad2(d.getUTCDate())
  );
}

function iptLocalHour(ms, offsetHours) {
  if (ms === null || ms === undefined) return null;
  return new Date(ms + (offsetHours || 0) * 3600000).getUTCHours();
}

function iptDateMs(d) {
  return Date.parse(d + 'T00:00:00Z');
}

function iptAddDays(d, n) {
  if (!d) return null;
  var x = new Date(iptDateMs(d) + n * DAY_MS);
  return (
    x.getUTCFullYear() +
    '-' +
    pad2(x.getUTCMonth() + 1) +
    '-' +
    pad2(x.getUTCDate())
  );
}

// b - a, in whole days.
function iptDayDiff(a, b) {
  if (!a || !b) return null;
  return Math.round((iptDateMs(b) - iptDateMs(a)) / DAY_MS);
}

// 0 = Sunday ... 6 = Saturday
function iptWeekday(d) {
  return new Date(iptDateMs(d)).getUTCDay();
}

// The Monday on or before d.
function iptWeekStart(d) {
  var wd = iptWeekday(d);
  return iptAddDays(d, wd === 0 ? -6 : 1 - wd);
}

var IPT_WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

// "lat lon alt accuracy"
function iptParseGps(raw) {
  var parts = iptStr(raw).split(/\s+/);
  if (parts.length < 2) return null;
  var lat = Number(parts[0]);
  var lon = Number(parts[1]);
  if (!isFinite(lat) || !isFinite(lon) || (lat === 0 && lon === 0)) return null;
  var acc = parts.length >= 4 ? Number(parts[3]) : null;
  return { lat: lat, lon: lon, acc: isFinite(acc) ? acc : null };
}

function iptHaversine(a, b) {
  var R = 6371000;
  var toRad = Math.PI / 180;
  var dLat = (b.lat - a.lat) * toRad;
  var dLon = (b.lon - a.lon) * toRad;
  var h =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(a.lat * toRad) *
      Math.cos(b.lat * toRad) *
      Math.sin(dLon / 2) *
      Math.sin(dLon / 2);
  return 2 * R * Math.asin(Math.sqrt(h));
}

function iptMedian(values) {
  var v = values.filter(function (x) {
    return x !== null && x !== undefined && isFinite(x);
  });
  if (!v.length) return null;
  v.sort(function (a, b) {
    return a - b;
  });
  var m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}

// A share as a percentage, or null when there is nothing to divide by.
function iptPct(n, d) {
  return d ? (100 * n) / d : null;
}

function iptIsDay1(row) {
  return /^day\s*1/i.test(iptStr(row.form_name));
}

// Which dose a form was trying to give: Day 1 is always dose 1; a follow-up is
// its expected dose (the form computes it from the case's dose count).
function iptDoseNumber(row) {
  if (iptIsDay1(row)) return 1;
  var e = iptNum(row.expected_dose);
  if (e) return e;
  var p = iptNum(row.prior_doses);
  return p !== null ? p + 1 : null;
}

function iptNormName(s) {
  return iptStr(s)
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '');
}

function iptTitle(s) {
  return iptStr(s)
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .replace(/(^|[\s(/-])([a-z])/g, function (m, p, c) {
      return p + c.toUpperCase();
    })
    .replace(/\b(Cps|Lgea|Ups|Pps|Jss|Sss|Gss|Gjss|Lea)\b/g, function (w) {
      return w.toUpperCase();
    });
}

// A visit row is a "screening" when the Day 1 form ended before registration:
// not eligible to proceed today, or no consent. No child case is created then.
function iptIsRegistration(row) {
  return (
    iptIsDay1(row) &&
    iptYes(row.eligible_to_proceed) &&
    iptYes(row.consent_3day)
  );
}

// Group children into schools. School is typed free-hand ("Deba cps", "Deba CPS",
// "debacps"), so a name alone splits one school many ways. Start from the
// normalised name, then join any two groups whose Day 1 points sit within
// `radiusM` of each other. Returns {byChild: {childId: schoolId}, schools: {...}}.
function iptClusterSchools(children, radiusM) {
  var groups = {};
  var order = [];
  children.forEach(function (c) {
    var key = iptNormName(c.school) || '__unknown__';
    if (!groups[key]) {
      groups[key] = { key: key, children: [], names: {}, lats: [], lons: [] };
      order.push(key);
    }
    var g = groups[key];
    g.children.push(c.id);
    var raw = iptStr(c.school);
    if (raw) g.names[raw] = (g.names[raw] || 0) + 1;
    if (c.gps) {
      g.lats.push(c.gps.lat);
      g.lons.push(c.gps.lon);
    }
  });
  var parent = {};
  order.forEach(function (k) {
    parent[k] = k;
  });
  function find(k) {
    while (parent[k] !== k) {
      parent[k] = parent[parent[k]];
      k = parent[k];
    }
    return k;
  }
  var centres = order.map(function (k) {
    var g = groups[k];
    return g.lats.length
      ? { key: k, lat: iptMedian(g.lats), lon: iptMedian(g.lons) }
      : null;
  });
  for (var i = 0; i < centres.length; i++) {
    for (var j = i + 1; j < centres.length; j++) {
      if (
        centres[i] &&
        centres[j] &&
        iptHaversine(centres[i], centres[j]) <= radiusM
      ) {
        parent[find(centres[j].key)] = find(centres[i].key);
      }
    }
  }
  var schools = {};
  var byChild = {};
  order.forEach(function (k) {
    var root = find(k);
    if (!schools[root])
      schools[root] = { id: root, childIds: [], names: {}, lats: [], lons: [] };
    var s = schools[root];
    var g = groups[k];
    s.childIds = s.childIds.concat(g.children);
    Object.keys(g.names).forEach(function (n) {
      s.names[n] = (s.names[n] || 0) + g.names[n];
    });
    s.lats = s.lats.concat(g.lats);
    s.lons = s.lons.concat(g.lons);
    g.children.forEach(function (id) {
      byChild[id] = root;
    });
  });
  Object.keys(schools).forEach(function (id) {
    var s = schools[id];
    var variants = Object.keys(s.names).sort(function (a, b) {
      return s.names[b] - s.names[a];
    });
    // Spellings that differ only in case or spacing ("Deba CPS", "debacps")
    // are one name: count them together, and show the best-formatted one
    // (with spaces, then the most used).
    var byNorm = {};
    variants.forEach(function (n) {
      var k = iptNormName(n);
      var e = (byNorm[k] = byNorm[k] || { n: 0, shown: null });
      e.n += s.names[n];
      var spaced = /\s/.test(n.trim());
      if (e.shown === null || (spaced && !/\s/.test(e.shown.trim())))
        e.shown = n;
    });
    var keys = Object.keys(byNorm).sort(function (a, b) {
      return byNorm[b].n - byNorm[a].n;
    });
    // Prefer the most common name, but not a bare place name ("Deba") over a
    // fuller one that starts with it ("Deba CPS") that nearly as many used.
    var bestKey = null;
    keys.forEach(function (k) {
      if (bestKey === null) bestKey = k;
      else if (
        k.indexOf(bestKey) === 0 &&
        k.length > bestKey.length &&
        byNorm[k].n * 2 >= byNorm[bestKey].n
      )
        bestKey = k;
    });
    var best = bestKey === null ? null : byNorm[bestKey].shown;
    s.label =
      id === '__unknown__' ? 'School not recorded' : iptTitle(best || id);
    s.variants = variants.map(function (n) {
      return { name: n, n: s.names[n] };
    });
    s.lat = iptMedian(s.lats);
    s.lon = iptMedian(s.lons);
    delete s.lats;
    delete s.lons;
    delete s.names;
  });
  return { byChild: byChild, schools: schools };
}

// One record per child, from the dosing forms and (when available) the CommCare
// child case. `today` is a WAT date string.
function iptBuildChildren(doseRows, caseRows, opts) {
  var off = opts.utcOffsetHours;
  var byId = {};
  var screenings = [];
  var sorted = doseRows.slice().map(function (r) {
    var ms = iptParseTs(r.time_end) || iptParseTs(r.time_start);
    return {
      row: r,
      ms: ms,
      date:
        ms !== null
          ? iptLocalDate(ms, off)
          : iptStr(r.visit_date).slice(0, 10) || null,
    };
  });
  sorted.sort(function (a, b) {
    return (a.ms || 0) - (b.ms || 0);
  });
  function ensure(id) {
    if (!byId[id]) {
      byId[id] = {
        id: id,
        forms: [],
        attempts: { 1: [], 2: [], 3: [] },
        extraDoses: 0,
        doseDates: {},
        flws: {},
        registered: false,
      };
    }
    return byId[id];
  }
  sorted.forEach(function (x) {
    var r = x.row;
    var id = iptStr(r.child_id);
    if (iptIsDay1(r) && !iptIsRegistration(r)) {
      screenings.push({ row: r, ms: x.ms, date: x.date });
      return;
    }
    if (!id) return;
    var c = ensure(id);
    var n = iptDoseNumber(r);
    var success = iptYes(r.dose_completed);
    var form = {
      row: r,
      ms: x.ms,
      date: x.date,
      dose: n,
      success: success,
      username: iptStr(r.username),
      gps: iptParseGps(r.gps_raw),
    };
    c.forms.push(form);
    if (form.username) c.flws[form.username] = true;
    if (iptIsDay1(r)) {
      c.registered = true;
      c.day1 = form;
      c.username = form.username;
      c.name = iptStr(r.child_name);
      c.school = iptStr(r.school);
      c.ward = iptStr(r.ward_village);
      c.district = iptStr(r.district);
      c.state = iptStr(r.state);
      c.sex = iptStr(r.sex);
      c.age = iptNum(r.age_years);
      c.weight = iptNum(r.weight_kg);
      c.band = iptStr(r.dose_band);
      c.phone = iptStr(r.caregiver_phone);
      c.regDate = x.date;
      c.gps = form.gps;
      c.visitId = iptStr(r.id);
      c.connectUserId = iptStr(r.connect_user_id);
      c.userVisitId = iptStr(r.user_visit_id);
      c.consentPhoto = iptStr(r.consent_photo);
    }
    if (n && n <= 3) {
      c.attempts[n].push(form);
      if (success && !c.doseDates[n]) c.doseDates[n] = x.date;
    } else if (n && n > 3 && success) {
      c.extraDoses += 1;
    }
    var cs = iptStr(r.course_status);
    if (cs) c.formCourseStatus = cs;
  });
  (caseRows || []).forEach(function (cr) {
    var id = iptStr(cr.case_id) || iptStr(cr.entity_id);
    if (!id) return;
    var known = !!byId[id];
    var c = ensure(id);
    c.caseRow = cr;
    if (!known) {
      // On CommCare but no Connect visit yet (rejected, unsynced, or pending).
      c.caseOnly = true;
      c.registered = true;
      c.regDate =
        iptStr(cr.registration_date) ||
        iptStr(cr.date_opened).slice(0, 10) ||
        null;
      c.school = iptStr(cr.school);
      c.sex = iptStr(cr.sex);
      c.age = iptNum(cr.age_years);
      c.band = iptStr(cr.dose_band);
    }
  });
  var children = Object.keys(byId)
    .map(function (k) {
      var c = byId[k];
      c.flwList = Object.keys(c.flws);
      if (!c.username) c.username = c.flwList[0] || '';
      c.successes = [1, 2, 3].filter(function (n) {
        return !!c.doseDates[n];
      }).length;
      var caseStatus = c.caseRow ? iptStr(c.caseRow.course_status) : '';
      // The case is the newer word (an AE log can stop a course without a dose form).
      c.courseStatus = caseStatus || c.formCourseStatus || '';
      return c;
    })
    .filter(function (c) {
      return c.registered;
    });
  return { children: children, screenings: screenings };
}

// Where a child's course stands as of `today` (WAT date). Every child lands in
// exactly one bucket.
var IPT_BUCKETS = [
  {
    id: 'completed',
    label: 'Completed',
    color: '#15803d',
    hint: 'All 3 doses given and swallowed',
  },
  {
    id: 'on_track',
    label: 'On track',
    color: '#4f46e5',
    hint: 'Next dose due today or later',
  },
  {
    id: 'overdue',
    label: 'Overdue',
    color: '#d97706',
    hint: 'Dose due date passed, still within the 3-day revisit window',
  },
  {
    id: 'missed',
    label: 'Missed',
    color: '#dc2626',
    hint: 'More than 3 days overdue, or the course was closed incomplete',
  },
  {
    id: 'no_dose1',
    label: 'No dose 1',
    color: '#9f1239',
    hint: 'Registered, but dose 1 was not successfully given',
  },
  {
    id: 'stopped',
    label: 'Stopped (safety)',
    color: '#475569',
    hint: 'Stopped by the safety screen or an adverse event',
  },
  {
    id: 'referred',
    label: 'Referred',
    color: '#0e7490',
    hint: 'Referred to a health facility',
  },
];

function iptBucketMeta(id) {
  for (var i = 0; i < IPT_BUCKETS.length; i++)
    if (IPT_BUCKETS[i].id === id) return IPT_BUCKETS[i];
  return { id: id, label: id, color: '#6b7280', hint: '' };
}

function iptChildStatus(c, today, opts) {
  var revisit = opts.revisitDays;
  var cs = (c.courseStatus || '').toLowerCase();
  var lastDose = c.doseDates[3] || c.doseDates[2] || c.doseDates[1] || null;
  var nextDose = c.successes >= 3 ? null : c.successes + 1;
  var nextDue = null;
  if (nextDose && lastDose) nextDue = iptAddDays(lastDose, 1);
  if (nextDose === 1 && c.regDate) nextDue = c.regDate;
  var res = {
    nextDose: nextDose,
    nextDue: nextDue,
    overdueDays: 0,
    bucket: null,
  };
  if (c.successes >= 3 || cs === 'completed') {
    res.bucket = 'completed';
    res.nextDose = null;
    res.nextDue = null;
  } else if (cs === 'stopped') res.bucket = 'stopped';
  else if (cs === 'referred') res.bucket = 'referred';
  else if (!c.doseDates[1]) res.bucket = 'no_dose1';
  else if (cs === 'incomplete') res.bucket = 'missed';
  else {
    var late = iptDayDiff(nextDue, today);
    res.overdueDays = late > 0 ? late : 0;
    if (late <= 0) res.bucket = 'on_track';
    else if (late <= revisit) res.bucket = 'overdue';
    else res.bucket = 'missed';
  }
  return res;
}

// Per dose: given on the day it was due, given late (catch-up), attempted but
// not swallowed, missed (due date passed with nothing), not yet due, or the
// course ended before it.
function iptDoseCell(c, n, status, today) {
  if (c.doseDates[n]) {
    var due = n === 1 ? c.regDate : iptAddDays(c.doseDates[n - 1], 1);
    var late = n === 1 ? 0 : iptDayDiff(due, c.doseDates[n]);
    return {
      state: late > 0 ? 'late' : 'given',
      date: c.doseDates[n],
      late: late > 0 ? late : 0,
    };
  }
  var attempted = c.attempts[n] && c.attempts[n].length > 0;
  var prevGiven = n === 1 || !!c.doseDates[n - 1];
  if (status.bucket === 'stopped' || status.bucket === 'referred') {
    return { state: attempted ? 'failed' : 'ended' };
  }
  if (!prevGiven) return { state: attempted ? 'failed' : 'blocked' };
  if (attempted && status.nextDose === n && status.bucket === 'on_track')
    return { state: 'failed' };
  var dueDate = n === 1 ? c.regDate : iptAddDays(c.doseDates[n - 1], 1);
  var overdue = iptDayDiff(dueDate, today);
  if (overdue > 0)
    return { state: attempted ? 'failed' : 'missed', overdue: overdue };
  return { state: attempted ? 'failed' : 'pending' };
}

function iptConsecutive(c) {
  return !!(
    c.doseDates[1] &&
    c.doseDates[2] &&
    c.doseDates[3] &&
    iptDayDiff(c.doseDates[1], c.doseDates[2]) === 1 &&
    iptDayDiff(c.doseDates[2], c.doseDates[3]) === 1
  );
}

// The 1 -> 2 -> 3 funnel. A step's denominator holds only children who could
// already have had it: given it, or due before today. A child registered this
// morning is not a drop-out.
function iptCascade(children, today, opts) {
  var out = {
    registered: children.length,
    d1: { got: 0, due: 0 },
    d2: { got: 0, due: 0 },
    d3: { got: 0, due: 0 },
    complete: { got: 0, due: 0 },
    consecutive: { got: 0, due: 0 },
  };
  children.forEach(function (c) {
    out.d1.due += 1;
    if (c.doseDates[1]) out.d1.got += 1;
    if (c.doseDates[1]) {
      var d2due = iptAddDays(c.doseDates[1], 1);
      if (c.doseDates[2] || d2due < today) out.d2.due += 1;
      if (c.doseDates[2]) out.d2.got += 1;
    }
    if (c.doseDates[2]) {
      var d3due = iptAddDays(c.doseDates[2], 1);
      if (c.doseDates[3] || d3due < today) out.d3.due += 1;
      if (c.doseDates[3]) out.d3.got += 1;
    }
    // Full course: graded once the last revisit chance for dose 3 has passed.
    var closes = c.regDate ? iptAddDays(c.regDate, 2 + opts.revisitDays) : null;
    if (c.successes >= 3 || (closes && closes < today)) {
      out.complete.due += 1;
      if (c.successes >= 3) out.complete.got += 1;
    }
    if (c.successes >= 3) {
      out.consecutive.due += 1;
      if (iptConsecutive(c)) out.consecutive.got += 1;
    }
  });
  return out;
}

// Registration cohorts (by day or ISO week): how each one has progressed.
function iptCohorts(children, today, opts, by) {
  var groups = {};
  children.forEach(function (c) {
    if (!c.regDate) return;
    var key = by === 'week' ? iptWeekStart(c.regDate) : c.regDate;
    (groups[key] = groups[key] || []).push(c);
  });
  return Object.keys(groups)
    .sort()
    .reverse()
    .map(function (key) {
      var list = groups[key];
      var cas = iptCascade(list, today, opts);
      var open = 0;
      var onTime2 = 0;
      var late2 = 0;
      var onTime3 = 0;
      var late3 = 0;
      list.forEach(function (c) {
        var b = iptChildStatus(c, today, opts).bucket;
        if (b === 'on_track' || b === 'overdue') open += 1;
        if (c.doseDates[2]) {
          if (iptDayDiff(c.doseDates[1], c.doseDates[2]) === 1) onTime2 += 1;
          else late2 += 1;
        }
        if (c.doseDates[3]) {
          if (iptDayDiff(c.doseDates[2], c.doseDates[3]) === 1) onTime3 += 1;
          else late3 += 1;
        }
      });
      var closes =
        by === 'week'
          ? iptAddDays(key, 6 + 2 + opts.revisitDays)
          : iptAddDays(key, 2 + opts.revisitDays);
      return {
        key: key,
        label: by === 'week' ? 'Week of ' + key : key,
        mature: closes < today,
        cascade: cas,
        open: open,
        onTime2: onTime2,
        late2: late2,
        onTime3: onTime3,
        late3: late3,
      };
    });
}

// Days between doses 1->2 and 2->3. 1 is the protocol.
function iptIntervals(children) {
  function bucket(d) {
    if (d === null) return null;
    if (d <= 0) return 'same day';
    if (d >= 4) return '4+';
    return String(d);
  }
  var out = {
    d1d2: { 'same day': 0, 1: 0, 2: 0, 3: 0, '4+': 0 },
    d2d3: { 'same day': 0, 1: 0, 2: 0, 3: 0, '4+': 0 },
  };
  children.forEach(function (c) {
    if (c.doseDates[1] && c.doseDates[2])
      out.d1d2[bucket(iptDayDiff(c.doseDates[1], c.doseDates[2]))] += 1;
    if (c.doseDates[2] && c.doseDates[3])
      out.d2d3[bucket(iptDayDiff(c.doseDates[2], c.doseDates[3]))] += 1;
  });
  return out;
}

// Successful doses per day, split by dose number, plus failed attempts.
function iptDailyActivity(children) {
  var days = {};
  children.forEach(function (c) {
    c.forms.forEach(function (f) {
      if (!f.date) return;
      var d = (days[f.date] = days[f.date] || {
        date: f.date,
        d1: 0,
        d2: 0,
        d3: 0,
        failed: 0,
      });
      if (!f.success) d.failed += 1;
      else if (f.dose === 1) d.d1 += 1;
      else if (f.dose === 2) d.d2 += 1;
      else if (f.dose === 3) d.d3 += 1;
    });
  });
  // Every calendar day from first to last, so a day with no dosing is a visible
  // gap rather than a missing column that squeezes the time axis.
  var keys = Object.keys(days).sort();
  if (!keys.length) return [];
  var out = [];
  for (var d = keys[0]; d <= keys[keys.length - 1]; d = iptAddDays(d, 1)) {
    out.push(days[d] || { date: d, d1: 0, d2: 0, d3: 0, failed: 0 });
  }
  return out;
}

// Per dose number: how attempts turned out.
function iptAttemptStats(children) {
  var out = {};
  [1, 2, 3].forEach(function (n) {
    out[n] = {
      forms: 0,
      dot: 0,
      firstSwallow: 0,
      vomited: 0,
      redoseOk: 0,
      failed: 0,
      refused: 0,
    };
  });
  children.forEach(function (c) {
    [1, 2, 3].forEach(function (n) {
      c.attempts[n].forEach(function (f) {
        var r = f.row;
        var s = out[n];
        s.forms += 1;
        if (iptYes(r.dot)) s.dot += 1;
        var sw = iptStr(r.swallowed).toLowerCase();
        if (sw === 'yes') s.firstSwallow += 1;
        if (sw === 'vomited') s.vomited += 1;
        if (sw === 'no') s.refused += 1;
        if (sw === 'vomited' && iptYes(r.redose_swallowed)) s.redoseOk += 1;
        if (!f.success) s.failed += 1;
      });
    });
  });
  return out;
}

// Why children fall out of the course, most common first.
function iptDropReasons(children, today, opts) {
  var counts = {};
  function add(k) {
    counts[k] = (counts[k] || 0) + 1;
  }
  children.forEach(function (c) {
    var st = iptChildStatus(c, today, opts);
    if (st.bucket === 'completed' || st.bucket === 'on_track') return;
    var last = c.forms[c.forms.length - 1];
    var r = last ? last.row : {};
    if (st.bucket === 'stopped')
      add('Stopped for safety (screen or adverse event)');
    else if (st.bucket === 'referred') add('Referred to a health facility');
    else if (last && iptStr(r.well_enough).toLowerCase() === 'no')
      add('Not well enough on the day');
    else if (last && iptStr(r.revisit_possible).toLowerCase() === 'no')
      add('Dose not given and no revisit possible');
    else if (last && iptStr(r.swallowed).toLowerCase() === 'no')
      add('Refused or could not swallow');
    else if (last && iptStr(r.swallowed).toLowerCase() === 'vomited')
      add('Vomited, no successful re-dose');
    else if (
      last &&
      iptStr(r.due_status).toLowerCase() === 'missed' &&
      !iptYes(r.missed_supervisor_ok)
    )
      add('Came back late, catch-up not approved');
    else if (st.bucket === 'no_dose1') add('Dose 1 not given');
    else add('Did not come back for the next dose');
  });
  return Object.keys(counts)
    .map(function (k) {
      return { reason: k, n: counts[k] };
    })
    .sort(function (a, b) {
      return b.n - a.n;
    });
}

// Plausible weight range for an age: wider than the WHO 1st-99th centiles for
// 5-15 year-olds, because underweight is common where IPTsc runs and the
// scales are classroom scales. Only clearly impossible pairs fall outside.
function iptWeightRange(age) {
  if (age === null || age === undefined) return null;
  if (age <= 6) return [10, 35];
  if (age <= 8) return [12, 45];
  if (age <= 10) return [14, 55];
  if (age <= 12) return [16, 65];
  return [19, 85];
}

function iptWeightOutlier(c) {
  var r = iptWeightRange(c.age);
  if (!r || c.weight === null || c.weight === undefined) return false;
  return c.weight < r[0] || c.weight > r[1];
}

// Authenticity checks for one set of forms + children (one FLW, or everyone).
// Each check returns {value, n, band} where band is null, 'yellow' or 'red'.
var IPT_AUTH_CHECKS = [
  {
    id: 'fast_day1',
    label: 'Day 1 forms under 3 minutes',
    unit: '%',
    contributes: true,
    yellow: 15,
    red: 40,
    hint: 'Day 1 includes weighing, consent photo, name audio and the dose; under 3 minutes is very fast.',
  },
  {
    id: 'throughput',
    label: 'Busiest hour (registrations)',
    unit: 'children',
    contributes: true,
    yellow: 15,
    red: 25,
    hint: 'Most Day 1 registrations finished inside any 60 minutes.',
  },
  {
    id: 'gps_missing',
    label: 'Forms without GPS',
    unit: '%',
    contributes: true,
    yellow: 5,
    red: 20,
    hint: 'Every dosing form asks for a GPS stamp at the school.',
  },
  {
    id: 'gps_poor',
    label: 'GPS accuracy worse than 100 m (or 0)',
    unit: '%',
    contributes: false,
    yellow: 20,
    red: 50,
    hint: 'Poor or zero accuracy can mean an indoor fix, or a mocked location.',
  },
  {
    id: 'drift',
    label: 'Median distance of doses 2-3 from dose 1',
    unit: 'm',
    contributes: true,
    yellow: 200,
    red: 500,
    hint: 'Doses 2 and 3 should be given at the school where the child had dose 1.',
  },
  {
    id: 'off_hours',
    label: 'Forms outside 07:00-17:00',
    unit: '%',
    contributes: true,
    yellow: 5,
    red: 20,
    hint: 'Dosing happens in school hours (WAT).',
  },
  {
    id: 'weight_round5',
    label: 'Weights that are a multiple of 5 kg',
    unit: '%',
    contributes: true,
    yellow: 35,
    red: 60,
    hint: 'About 20% expected by chance. Much more suggests weights are guessed, not measured.',
  },
  {
    id: 'weight_outlier',
    label: 'Weight implausible for age',
    unit: '%',
    contributes: true,
    yellow: 5,
    red: 15,
    hint: 'Clearly implausible for the recorded age: ranges are wider than WHO growth references, to allow for underweight children.',
  },
  {
    id: 'age_heap',
    label: 'Ages recorded as exactly 10',
    unit: '%',
    contributes: false,
    yellow: 30,
    red: 50,
    hint: 'Ages pile up on round numbers when they are estimated.',
  },
  {
    id: 'consent_photo',
    label: 'Registrations missing a consent photo',
    unit: '%',
    contributes: true,
    yellow: 1,
    red: 5,
    hint: 'A signed or thumbprinted consent form photo is required for every registration.',
  },
  {
    id: 'dose_photo',
    label: 'Doses missing a dose photo',
    unit: '%',
    contributes: true,
    yellow: 2,
    red: 10,
    hint: 'A photo of the prepared dose is required before giving it.',
  },
  {
    id: 'child_audio',
    label: 'Child said name, but no recording',
    unit: '%',
    contributes: true,
    yellow: 5,
    red: 15,
    hint: 'When the child says their name the form asks for a recording of it.',
  },
  {
    id: 'no_name',
    label: 'Child did not say their name',
    unit: '%',
    contributes: false,
    yellow: 15,
    red: 40,
    hint: 'A high share from one worker is worth a look.',
  },
  {
    id: 'dup_name',
    label: 'Possible duplicates: same name, same school',
    unit: 'children',
    dup: true,
    contributes: false,
    yellow: 1,
    red: 3,
    hint: 'Same name at the same school, registered more than once. Common names make this the loosest check, so it is shown but never raises the flag.',
  },
  {
    id: 'dup_name_age',
    label: 'Possible duplicates: same name, age and school',
    unit: 'children',
    dup: true,
    contributes: true,
    yellow: 1,
    red: 3,
    hint: 'Same name and same age at the same school, registered more than once. Children with no recorded age are not compared.',
  },
  {
    id: 'dup_name_age_phone',
    label: 'Possible duplicates: same name, age, caregiver phone and school',
    unit: 'children',
    dup: true,
    contributes: true,
    yellow: 1,
    red: 2,
    hint: 'Same name, same age and same caregiver phone at the same school, registered more than once: the strongest sign of one child registered twice. Children with no recorded age or phone are not compared.',
  },
  {
    id: 'shared_phone',
    label: 'Children sharing a caregiver phone with 4+ others',
    unit: 'children',
    contributes: false,
    yellow: 1,
    red: 10,
    hint: 'Siblings share a phone; five or more children on one number is unusual.',
  },
  {
    id: 'too_perfect',
    label: 'Flawless at volume',
    unit: '',
    contributes: false,
    yellow: 1,
    red: 99,
    hint: 'At least 30 doses with no refusal, no vomiting, no reaction and every dose on time. Real data has some friction.',
  },
];

var IPT_DUP_CHECK_IDS = ['dup_name', 'dup_name_age', 'dup_name_age_phone'];

function iptNormPhone(p) {
  var d = iptStr(p).replace(/\D/g, '');
  // 0803... and +234 803... are the same number
  if (d.indexOf('234') === 0 && d.length === 13) d = '0' + d.slice(3);
  return d.length >= 10 ? d : '';
}

// Children registered more than once under one check's matching rule.
// Returns [{key, childIds}] -- each group is one suspected child -- largest
// first. Name and school are normalised (case, spacing, spelling variants of
// a school grouped by iptClusterSchools); a child missing a matched attribute
// is left out of that check.
function iptDuplicateGroups(children, checkId, schoolMap) {
  var seen = {};
  children.forEach(function (c) {
    var nm = iptNormName(c.name);
    if (!nm) return;
    var parts = [schoolMap ? schoolMap[c.id] : iptNormName(c.school), nm];
    if (checkId === 'dup_name_age' || checkId === 'dup_name_age_phone') {
      if (c.age === null || c.age === undefined) return;
      parts.push(String(c.age));
    }
    if (checkId === 'dup_name_age_phone') {
      var ph = iptNormPhone(c.phone);
      if (!ph) return;
      parts.push(ph);
    }
    var k = parts.join('|');
    (seen[k] = seen[k] || []).push(c.id);
  });
  return Object.keys(seen)
    .filter(function (k) {
      return seen[k].length > 1;
    })
    .map(function (k) {
      return { key: k, childIds: seen[k] };
    })
    .sort(function (a, b) {
      return b.childIds.length - a.childIds.length || (a.key < b.key ? -1 : 1);
    });
}

// The blob id of a visit's consent photo, from the visit-images endpoint's
// list for that visit ({blob_id, name, question_id}). The form stores the
// photo's filename in consent_photo; match on it, or on the question id.
function iptConsentBlob(images, filename) {
  var list = images || [];
  for (var i = 0; i < list.length; i++) {
    if (filename && list[i].name === filename) return list[i].blob_id || null;
  }
  for (var j = 0; j < list.length; j++) {
    if (/consent_photo$/.test(iptStr(list[j].question_id)))
      return list[j].blob_id || null;
  }
  return null;
}

// "Open this visit in Connect", the format the audit grid uses; null when a
// piece is missing (the caller then falls back to the labs visit page).
function iptConnectVisitUrl(
  orgSlug,
  opportunityId,
  connectUserId,
  userVisitId,
) {
  if (!orgSlug || !opportunityId || !connectUserId || !userVisitId) return null;
  return (
    'https://connect.dimagi.com/a/' +
    encodeURIComponent(orgSlug) +
    '/opportunity/' +
    encodeURIComponent(opportunityId) +
    '/user_visits/?user=' +
    encodeURIComponent(connectUserId) +
    '&visit_id=' +
    encodeURIComponent(userVisitId)
  );
}

function iptBandHigh(value, yellow, red) {
  if (value === null || value === undefined) return null;
  if (value >= red) return 'red';
  if (value >= yellow) return 'yellow';
  return null;
}

function iptAuthenticity(children, opts, schoolMap) {
  var off = opts.utcOffsetHours;
  var forms = [];
  children.forEach(function (c) {
    c.forms.forEach(function (f) {
      forms.push({ f: f, c: c });
    });
  });
  var day1 = forms.filter(function (x) {
    return x.f.dose === 1 && iptIsDay1(x.f.row);
  });
  var res = {};
  function set(id, value, n, extra) {
    var spec = null;
    for (var i = 0; i < IPT_AUTH_CHECKS.length; i++)
      if (IPT_AUTH_CHECKS[i].id === id) spec = IPT_AUTH_CHECKS[i];
    var enough =
      id === 'throughput' ||
      id.indexOf('dup_') === 0 ||
      id === 'shared_phone' ||
      id === 'too_perfect' ||
      n >= 5;
    res[id] = {
      value: value,
      n: n,
      band: enough ? iptBandHigh(value, spec.yellow, spec.red) : null,
      extra: extra || null,
    };
  }
  // Day 1 duration
  var fast = 0;
  var durN = 0;
  day1.forEach(function (x) {
    var s = iptParseTs(x.f.row.time_start);
    var e = iptParseTs(x.f.row.time_end);
    if (s !== null && e !== null && e >= s) {
      durN += 1;
      if (e - s < 3 * 60000) fast += 1;
    }
  });
  set('fast_day1', iptPct(fast, durN), durN);
  // Busiest 60 minutes of Day 1 completions, for ONE worker: across several
  // workers the count only measures team size. With many workers in view the
  // value is the busiest worker's.
  var timesBy = {};
  day1.forEach(function (x) {
    if (x.f.ms === null) return;
    (timesBy[x.f.username] = timesBy[x.f.username] || []).push(x.f.ms);
  });
  var best = null;
  var bestWho = null;
  Object.keys(timesBy).forEach(function (u) {
    var times = timesBy[u].sort(function (a, b) {
      return a - b;
    });
    var lo = 0;
    for (var hi = 0; hi < times.length; hi++) {
      while (times[hi] - times[lo] > 3600000) lo += 1;
      if (best === null || hi - lo + 1 > best) {
        best = hi - lo + 1;
        bestWho = u;
      }
    }
  });
  set('throughput', best, day1.length, { username: bestWho });
  // GPS
  var noGps = 0;
  var poor = 0;
  var withGps = 0;
  forms.forEach(function (x) {
    if (!x.f.gps) noGps += 1;
    else {
      withGps += 1;
      if (x.f.gps.acc !== null && (x.f.gps.acc > 100 || x.f.gps.acc === 0))
        poor += 1;
    }
  });
  set('gps_missing', iptPct(noGps, forms.length), forms.length);
  set('gps_poor', iptPct(poor, withGps), withGps);
  // Drift of follow-up doses from the child's Day 1 point
  var drifts = [];
  forms.forEach(function (x) {
    if (x.f.dose > 1 && x.f.gps && x.c.gps)
      drifts.push(iptHaversine(x.f.gps, x.c.gps));
  });
  set('drift', iptMedian(drifts), drifts.length);
  // Hours
  var offHours = 0;
  var hourN = 0;
  forms.forEach(function (x) {
    var h = iptLocalHour(x.f.ms, off);
    if (h === null) return;
    hourN += 1;
    if (h < 7 || h >= 17) offHours += 1;
  });
  set('off_hours', iptPct(offHours, hourN), hourN);
  // Weight / age plausibility
  var regs = children.filter(function (c) {
    return c.day1;
  });
  var weighed = regs.filter(function (c) {
    return c.weight !== null && c.weight !== undefined;
  });
  var r5 = weighed.filter(function (c) {
    return Math.abs(c.weight % 5) < 1e-9;
  }).length;
  set('weight_round5', iptPct(r5, weighed.length), weighed.length);
  var withAge = weighed.filter(function (c) {
    return c.age !== null && c.age !== undefined;
  });
  set(
    'weight_outlier',
    iptPct(
      withAge.filter(function (c) {
        return iptWeightOutlier(c);
      }).length,
      withAge.length,
    ),
    withAge.length,
  );
  var aged = regs.filter(function (c) {
    return c.age !== null && c.age !== undefined;
  });
  set(
    'age_heap',
    iptPct(
      aged.filter(function (c) {
        return c.age === 10;
      }).length,
      aged.length,
    ),
    aged.length,
  );
  // Evidence captured
  set(
    'consent_photo',
    iptPct(
      regs.filter(function (c) {
        return !iptPresent(c.day1.row.consent_photo);
      }).length,
      regs.length,
    ),
    regs.length,
  );
  var given = forms.filter(function (x) {
    return iptYes(x.f.row.dose_prepared);
  });
  set(
    'dose_photo',
    iptPct(
      given.filter(function (x) {
        return !iptPresent(x.f.row.dose_photo);
      }).length,
      given.length,
    ),
    given.length,
  );
  var saidName = forms.filter(function (x) {
    return iptYes(x.f.row.child_said_name);
  });
  set(
    'child_audio',
    iptPct(
      saidName.filter(function (x) {
        return !iptPresent(x.f.row.child_name_audio);
      }).length,
      saidName.length,
    ),
    saidName.length,
  );
  var asked = forms.filter(function (x) {
    return iptPresent(x.f.row.child_said_name);
  });
  set(
    'no_name',
    iptPct(
      asked.filter(function (x) {
        return iptStr(x.f.row.child_said_name).toLowerCase() === 'no';
      }).length,
      asked.length,
    ),
    asked.length,
  );
  // Duplicates, three ways. Value: children flagged. extra.groups: how many
  // distinct children they appear to be (one group per suspected child).
  IPT_DUP_CHECK_IDS.forEach(function (id) {
    var groups = iptDuplicateGroups(regs, id, schoolMap);
    var ids = [];
    groups.forEach(function (g) {
      ids = ids.concat(g.childIds);
    });
    set(id, ids.length, regs.length, {
      childIds: ids,
      groups: groups.length,
      groupList: groups,
    });
  });
  var phones = {};
  regs.forEach(function (c) {
    var p = iptStr(c.phone).replace(/\D/g, '');
    if (p.length >= 10) (phones[p] = phones[p] || []).push(c.id);
  });
  var shared = [];
  Object.keys(phones).forEach(function (p) {
    if (phones[p].length >= 5) shared = shared.concat(phones[p]);
  });
  set('shared_phone', shared.length, regs.length, { childIds: shared });
  // Flawless at volume
  var doseForms = forms.filter(function (x) {
    return x.f.dose;
  });
  var friction = doseForms.filter(function (x) {
    var r = x.f.row;
    var sw = iptStr(r.swallowed).toLowerCase();
    return (
      sw === 'no' ||
      sw === 'vomited' ||
      iptStr(r.obs_result).toLowerCase() === 'yes' ||
      !x.f.success
    );
  }).length;
  var lateDoses = 0;
  children.forEach(function (c) {
    if (c.doseDates[2] && iptDayDiff(c.doseDates[1], c.doseDates[2]) !== 1)
      lateDoses += 1;
    if (c.doseDates[3] && iptDayDiff(c.doseDates[2], c.doseDates[3]) !== 1)
      lateDoses += 1;
  });
  var perfect =
    doseForms.length >= 30 && friction === 0 && lateDoses === 0 ? 1 : 0;
  set('too_perfect', doseForms.length >= 30 ? perfect : null, doseForms.length);
  return res;
}

// A check's value as text: "85%", "1,540 m", "26".
function iptCheckValueText(spec, r) {
  if (!r || r.value === null || r.value === undefined) return '–';
  if (spec.id === 'too_perfect') return r.value ? 'Yes' : 'No';
  if (spec.dup)
    return (
      Math.round(r.value).toLocaleString() +
      ' (' +
      ((r.extra && r.extra.groups) || 0) +
      ' unique)'
    );
  if (spec.unit === '%')
    return r.value >= 99.5 && r.value < 100
      ? '>99%'
      : Math.round(r.value) + '%';
  if (spec.unit === 'm') return Math.round(r.value).toLocaleString() + ' m';
  return Math.round(r.value).toLocaleString();
}

// One overall flag from the contributing checks. Reasons carry their values,
// e.g. "Day 1 forms under 3 minutes: 85%".
function iptAuthFlag(checks) {
  var reds = [];
  var yellows = [];
  IPT_AUTH_CHECKS.forEach(function (spec) {
    var r = checks[spec.id];
    if (!r || !spec.contributes) return;
    var reason =
      spec.label +
      ': ' +
      iptCheckValueText(spec, r) +
      (spec.unit === '%' && r.n ? ' of ' + r.n : '');
    if (r.band === 'red') reds.push(reason);
    else if (r.band === 'yellow') yellows.push(reason);
  });
  return {
    band: reds.length ? 'red' : yellows.length ? 'yellow' : 'green',
    reasons: reds.concat(yellows),
    reds: reds,
    yellows: yellows,
  };
}

// Protocol compliance: each row is {id, label, num, den, value, band}.
function iptCompliance(children, screenings) {
  var regs = children.filter(function (c) {
    return c.day1;
  });
  var day1Forms = regs
    .map(function (c) {
      return c.day1.row;
    })
    .concat(
      screenings.map(function (s) {
        return s.row;
      }),
    );
  var allForms = [];
  children.forEach(function (c) {
    c.forms.forEach(function (f) {
      allForms.push(f);
    });
  });
  var followUps = allForms.filter(function (f) {
    return !iptIsDay1(f.row);
  });
  function count(list, pred) {
    return list.filter(pred).length;
  }
  function row(id, label, num, den, goodHigh, yellow, red, note) {
    var v = iptPct(num, den);
    var band = null;
    if (v !== null && den >= 5) {
      if (goodHigh) band = v < red ? 'red' : v < yellow ? 'yellow' : 'green';
      else band = v > red ? 'red' : v > yellow ? 'yellow' : 'green';
    }
    return {
      id: id,
      label: label,
      num: num,
      den: den,
      value: v,
      band: band,
      goodHigh: goodHigh,
      note: note || '',
    };
  }
  var offWindow = regs.filter(function (c) {
    var wd = c.regDate ? iptWeekday(c.regDate) : null;
    return wd !== null && (wd === 0 || wd >= 4);
  });
  var rows = [
    row(
      'reg_window',
      'Registrations on Thursday-Sunday',
      offWindow.length,
      regs.length,
      false,
      2,
      5,
      'Day 1 must leave two school days for doses 2 and 3.',
    ),
    row(
      'reg_window_override',
      'Off-window registrations with supervisor approval',
      count(offWindow, function (c) {
        return iptYes(c.day1.row.supervisor_override);
      }),
      offWindow.length,
      true,
      100,
      100,
      'Every Thursday-Sunday registration needs a supervisor override with audio.',
    ),
    row(
      'holiday',
      'Registrations with a holiday in the next 2 days (yes or not sure)',
      count(regs, function (c) {
        var h = iptStr(c.day1.row.holiday_intervening).toLowerCase();
        return h === 'yes' || h === 'not_sure';
      }),
      regs.length,
      false,
      2,
      5,
      'A holiday breaks the 3-consecutive-day schedule.',
    ),
    row(
      'availability',
      'Registered although the child may not be back for doses 2-3',
      count(regs, function (c) {
        var a = iptStr(c.day1.row.availability_next2).toLowerCase();
        return a === 'no' || a === 'not_sure';
      }),
      regs.length,
      false,
      2,
      5,
      'Predicts missed doses.',
    ),
    row(
      'screened_out',
      'Day 1 forms that ended without registration',
      screenings.length,
      day1Forms.length,
      false,
      15,
      30,
      'Not eligible to proceed today, or no consent.',
    ),
    row(
      'consent_photo',
      'Consent photo captured',
      count(regs, function (c) {
        return iptPresent(c.day1.row.consent_photo);
      }),
      regs.length,
      true,
      100,
      98,
    ),
    row(
      'dot',
      'Doses given under direct observation',
      count(allForms, function (f) {
        return iptYes(f.row.dot);
      }),
      count(allForms, function (f) {
        return iptPresent(f.row.dot);
      }),
      true,
      98,
      95,
      'The core of the intervention: each dose is watched.',
    ),
    row(
      'dose_photo',
      'Dose photo captured',
      count(allForms, function (f) {
        return iptPresent(f.row.dose_photo);
      }),
      count(allForms, function (f) {
        return iptYes(f.row.dose_prepared);
      }),
      true,
      98,
      90,
    ),
    row(
      'band_10_20',
      'Children in the 10-<20 kg band (unusual over age 5)',
      count(regs, function (c) {
        return c.band === 'band_10_20';
      }),
      regs.length,
      false,
      5,
      10,
      'Each one needs a supervisor review before dosing.',
    ),
    row(
      'band_review',
      'Children over 40 kg (higher band, needs approval)',
      count(regs, function (c) {
        return c.band === 'review';
      }),
      regs.length,
      false,
      5,
      10,
    ),
    row(
      'over10',
      'Children over 10 with protocol confirmation',
      count(regs, function (c) {
        return c.age > 10 && iptYes(c.day1.row.age_over10_confirm);
      }),
      count(regs, function (c) {
        return c.age > 10;
      }),
      true,
      100,
      95,
      'Dosing over age 10 needs programme confirmation.',
    ),
    row(
      'catch_up',
      'Follow-ups after a missed dose, with supervisor approval',
      count(followUps, function (f) {
        return (
          iptStr(f.row.due_status).toLowerCase() === 'missed' &&
          iptYes(f.row.missed_supervisor_ok)
        );
      }),
      count(followUps, function (f) {
        return iptStr(f.row.due_status).toLowerCase() === 'missed';
      }),
      true,
      100,
      90,
      'A catch-up dose needs supervisor approval and PIN.',
    ),
    row(
      'early',
      'Follow-ups opened before the dose was due',
      count(followUps, function (f) {
        return iptStr(f.row.due_status).toLowerCase() === 'early';
      }),
      followUps.length,
      false,
      1,
      5,
      'Doses must not be given early.',
    ),
    row(
      'over_limit',
      'Children given more than 3 doses',
      count(children, function (c) {
        return c.extraDoses > 0;
      }),
      children.length,
      false,
      0,
      1,
      'Over-dosing, or a duplicate case. Doses beyond 3 are not paid.',
    ),
    row(
      'return_confirm',
      'Return dates explained at registration',
      count(regs, function (c) {
        return iptYes(c.day1.row.return_confirm);
      }),
      count(regs, function (c) {
        return iptPresent(c.day1.row.return_confirm);
      }),
      true,
      98,
      90,
    ),
    row(
      'observer_staff',
      'Post-dose observation by FLW or supervisor (not school staff)',
      count(allForms, function (f) {
        var o = iptStr(f.row.obs_responsible).toLowerCase();
        return o === 'flw' || o === 'supervisor';
      }),
      count(allForms, function (f) {
        return iptPresent(f.row.obs_responsible);
      }),
      true,
      0,
      0,
      'Informational: who watched the child for 30 minutes.',
    ),
  ];
  return rows;
}

var IPT_SYMPTOMS = {
  vomiting: 'Vomiting',
  nausea: 'Nausea / stomach pain',
  rash: 'Rash / itching',
  fever: 'Fever',
  dizziness: 'Dizziness / weakness',
  swelling: 'Swelling',
  breathing: 'Difficulty breathing',
  fainting: 'Fainting',
  other: 'Other',
};

var IPT_SEVERITY = ['mild', 'moderate', 'severe', 'danger'];

var IPT_AE_ACTION = {
  observed_only: 'Observed only',
  informed_teacher: 'Informed teacher',
  called_caregiver: 'Called caregiver',
  called_supervisor: 'Called supervisor',
  referred: 'Referred to facility',
  emergency_referral: 'Emergency referral',
  other: 'Other',
};

var IPT_AE_STATUS = {
  resolved: 'Resolved',
  improving: 'Improving',
  same: 'No change',
  worsening: 'Worsening',
  unknown: 'Unknown',
};

var IPT_COURSE_ACTION = {
  no_change: 'Continue',
  hold: 'Hold pending review',
  stop_ae: 'Stopped (adverse event)',
  already_completed: 'Already completed',
  supervisor_review: 'Supervisor review',
};

// An adverse event still needs someone: not resolved or improving, and the
// worker said follow-up was required (or did not say).
function iptAeOpen(a) {
  return (
    (a.status === 'worsening' ||
      a.status === 'unknown' ||
      a.status === 'same' ||
      !a.status) &&
    a.followup !== 'no'
  );
}

// Safety: screening, tolerance, observation and adverse events.
function iptSafety(children, screenings, aeRows, opts) {
  var regs = children.filter(function (c) {
    return c.day1;
  });
  var screenRows = regs
    .map(function (c) {
      return c.day1.row;
    })
    .concat(
      screenings.map(function (s) {
        return s.row;
      }),
    );
  function share(field, values) {
    var asked = screenRows.filter(function (r) {
      return iptPresent(r[field]);
    });
    var hit = asked.filter(function (r) {
      return values.indexOf(iptStr(r[field]).toLowerCase()) >= 0;
    });
    return { num: hit.length, den: asked.length };
  }
  var screen = [
    { label: 'Feverish or too unwell', s: share('acute_illness', ['yes']) },
    {
      label: 'Allergy to SP / AQ / sulfa (yes or unknown)',
      s: share('allergy', ['yes', 'dont_know']),
    },
    {
      label: 'Recent antimalarial (yes or unknown)',
      s: share('recent_antimalarial', ['yes', 'dont_know']),
    },
    {
      label: 'On cotrimoxazole (yes or unknown)',
      s: share('cotrimoxazole', ['yes', 'dont_know']),
    },
    {
      label: 'Possibly pregnant (girls 10+)',
      s: share('pregnancy_screen', ['yes']),
    },
    {
      label: 'Screened out: not dosed today',
      s: share('dose_eligible', ['no']),
    },
  ];
  var forms = [];
  children.forEach(function (c) {
    c.forms.forEach(function (f) {
      forms.push({ f: f, c: c });
    });
  });
  var obs = forms.filter(function (x) {
    return iptPresent(x.f.row.obs_result);
  });
  var obsCounts = { no: 0, yes: 0, pending: 0, left_early: 0 };
  var observer = {};
  forms.forEach(function (x) {
    var o = iptStr(x.f.row.obs_responsible).toLowerCase();
    if (o) observer[o] = (observer[o] || 0) + 1;
  });
  obs.forEach(function (x) {
    var k = iptStr(x.f.row.obs_result).toLowerCase();
    if (obsCounts[k] !== undefined) obsCounts[k] += 1;
  });
  // Adverse events: the AE Log is the record; an inline "reaction reported" with
  // no log for that child is a gap in the paperwork, counted separately.
  var childIds = {};
  children.forEach(function (c) {
    childIds[c.id] = c;
  });
  var aes = (aeRows || [])
    .map(function (r) {
      var ms = iptParseTs(r.event_datetime) || iptParseTs(r.time_end);
      var c = childIds[iptStr(r.child_id)] || null;
      return {
        row: r,
        ms: ms,
        date: ms !== null ? iptLocalDate(ms, opts.utcOffsetHours) : null,
        child: c,
        severity: iptStr(r.severity).toLowerCase(),
        dose: iptStr(r.which_dose).replace('dose_', ''),
        symptoms: iptStr(r.symptoms).split(/\s+/).filter(Boolean),
        actions: iptStr(r.action).split(/\s+/).filter(Boolean),
        status: iptStr(r.current_status).toLowerCase(),
        followup: iptStr(r.followup_required).toLowerCase(),
        courseAction: iptStr(r.course_action).toLowerCase(),
      };
    })
    .filter(function (a) {
      // A form for a child outside the current filter is not this view's.
      return !opts.restrictToChildren || a.child;
    });
  var loggedChildren = {};
  aes.forEach(function (a) {
    if (a.child) loggedChildren[a.child.id] = true;
  });
  var unlogged = forms.filter(function (x) {
    return (
      iptStr(x.f.row.obs_result).toLowerCase() === 'yes' &&
      !loggedChildren[x.c.id]
    );
  });
  var bySeverity = { mild: 0, moderate: 0, severe: 0, danger: 0 };
  var bySymptom = {};
  var byDose = { 1: 0, 2: 0, 3: 0, not_sure: 0 };
  aes.forEach(function (a) {
    if (bySeverity[a.severity] !== undefined) bySeverity[a.severity] += 1;
    a.symptoms.forEach(function (s) {
      bySymptom[s] = (bySymptom[s] || 0) + 1;
    });
    if (byDose[a.dose] !== undefined) byDose[a.dose] += 1;
  });
  var dosesGiven = forms.filter(function (x) {
    return x.f.success;
  }).length;
  var moderatePlus =
    bySeverity.moderate + bySeverity.severe + bySeverity.danger;
  aes.forEach(function (a) {
    a.isOpen = iptAeOpen(a);
  });
  var open = aes
    .filter(function (a) {
      var serious =
        a.severity !== 'mild' ||
        a.actions.indexOf('referred') >= 0 ||
        a.actions.indexOf('emergency_referral') >= 0;
      return serious;
    })
    .sort(function (a, b) {
      var openA = a.isOpen;
      var openB = b.isOpen;
      if (openA !== openB) return openA ? -1 : 1;
      return (
        IPT_SEVERITY.indexOf(b.severity) - IPT_SEVERITY.indexOf(a.severity) ||
        (b.ms || 0) - (a.ms || 0)
      );
    });
  var tolerance = iptAttemptStats(children);
  return {
    screen: screen,
    screenTotal: screenRows.length,
    obsCounts: obsCounts,
    obsTotal: obs.length,
    observer: observer,
    aes: aes,
    bySeverity: bySeverity,
    bySymptom: bySymptom,
    byDose: byDose,
    dosesGiven: dosesGiven,
    moderatePlus: moderatePlus,
    ratePer1000: dosesGiven ? (1000 * moderatePlus) / dosesGiven : null,
    lineList: open,
    unlogged: unlogged,
    tolerance: tolerance,
  };
}

// The FLW scorecard: one row per worker who registered or dosed a child.
function iptWorkerRows(children, today, opts, nameOf, schoolMap) {
  var by = {};
  children.forEach(function (c) {
    var u = c.username || '(unknown)';
    (by[u] = by[u] || []).push(c);
  });
  return Object.keys(by).map(function (u) {
    var list = by[u];
    var cas = iptCascade(list, today, opts);
    var att = iptAttemptStats(list);
    var checks = iptAuthenticity(list, opts, schoolMap);
    var flag = iptAuthFlag(checks);
    var missing = 0;
    var schools = {};
    var lastDate = null;
    var doses = 0;
    list.forEach(function (c) {
      var b = iptChildStatus(c, today, opts).bucket;
      if (b === 'overdue' || b === 'missed' || b === 'no_dose1') missing += 1;
      if (schoolMap && schoolMap[c.id]) schools[schoolMap[c.id]] = true;
      c.forms.forEach(function (f) {
        if (f.success) doses += 1;
        if (f.date && (!lastDate || f.date > lastDate)) lastDate = f.date;
      });
    });
    var forms = att[1].forms + att[2].forms + att[3].forms;
    var dot = att[1].dot + att[2].dot + att[3].dot;
    var swallowedFirst =
      att[1].firstSwallow + att[2].firstSwallow + att[3].firstSwallow;
    var onTime = 0;
    var timed = 0;
    list.forEach(function (c) {
      [2, 3].forEach(function (n) {
        if (c.doseDates[n]) {
          timed += 1;
          if (iptDayDiff(c.doseDates[n - 1], c.doseDates[n]) === 1) onTime += 1;
        }
      });
    });
    return {
      username: u,
      name: nameOf(u),
      children: list.length,
      schools: Object.keys(schools).length,
      doses: doses,
      d1: cas.d1.got,
      d2: cas.d2.got,
      d3: cas.d3.got,
      complete: cas.complete,
      missing: missing,
      onTime: { got: onTime, due: timed },
      dot: { got: dot, due: forms },
      swallowed: { got: swallowedFirst, due: forms },
      checks: checks,
      flag: flag,
      lastDate: lastDate,
      daysSince: lastDate ? iptDayDiff(lastDate, today) : null,
    };
  });
}

// "What stands out": short sentences from rules, worst first, at most `max`.
function iptHighlights(ctx, max) {
  var out = [];
  var cas = ctx.cascade;
  if (ctx.buckets.missed > 0)
    out.push({
      tone: 'red',
      text:
        ctx.buckets.missed +
        (ctx.buckets.missed === 1 ? ' child has' : ' children have') +
        ' missed a dose and are past the 3-day revisit window.',
      tab: 'completion',
    });
  if (ctx.buckets.overdue > 0)
    out.push({
      tone: 'yellow',
      text:
        ctx.buckets.overdue +
        (ctx.buckets.overdue === 1 ? ' child is' : ' children are') +
        ' overdue but can still be dosed within the revisit window: follow up now.',
      tab: 'completion',
    });
  if (ctx.safety && ctx.safety.bySeverity.danger > 0)
    out.push({
      tone: 'red',
      text:
        ctx.safety.bySeverity.danger + ' adverse event(s) with a danger sign.',
      tab: 'safety',
    });
  if (ctx.safety && ctx.safety.unlogged.length > 0)
    out.push({
      tone: 'yellow',
      text:
        ctx.safety.unlogged.length +
        ' reaction(s) reported during observation without an Adverse Event Log.',
      tab: 'safety',
    });
  var red = ctx.workers.filter(function (w) {
    return w.flag.band === 'red';
  });
  red.slice(0, 3).forEach(function (w) {
    out.push({
      tone: 'red',
      text: w.name + ' needs a review. ' + w.flag.reds.join('. ') + '.',
      tab: 'authenticity',
    });
  });
  if (red.length > 3)
    out.push({
      tone: 'red',
      text: red.length - 3 + ' more field worker(s) need a review.',
      tab: 'authenticity',
    });
  if (cas.d2.due >= ctx.opts.minChildren && iptPct(cas.d2.got, cas.d2.due) < 90)
    out.push({
      tone: 'yellow',
      text:
        'Only ' +
        Math.round(iptPct(cas.d2.got, cas.d2.due)) +
        '% of children due dose 2 received it.',
      tab: 'completion',
    });
  if (ctx.auth.weight_round5 && ctx.auth.weight_round5.band)
    out.push({
      tone: ctx.auth.weight_round5.band,
      text:
        Math.round(ctx.auth.weight_round5.value) +
        '% of recorded weights are a multiple of 5 kg: check that children are being weighed.',
      tab: 'authenticity',
    });
  var rank = { red: 0, yellow: 1, info: 2 };
  out.sort(function (a, b) {
    return rank[a.tone] - rank[b.tone];
  });
  return out.slice(0, max || 6);
}

// CSV with a guard against formula injection (a cell starting with = + - @).
function iptCsv(header, rows) {
  function cell(v) {
    var s = v === null || v === undefined ? '' : String(v);
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
    if (/[",\n]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
    return s;
  }
  return [header.map(cell).join(',')]
    .concat(
      rows.map(function (r) {
        return r.map(cell).join(',');
      }),
    )
    .join('\n');
}

// ===========================================================================
// 2. Presentational components (top level -- see the header for why)
// ===========================================================================

var IPT_TONE = {
  red: { bg: '#fef2f2', fg: '#b91c1c', bd: '#fecaca' },
  yellow: { bg: '#fffbeb', fg: '#b45309', bd: '#fde68a' },
  green: { bg: '#f0fdf4', fg: '#15803d', bd: '#bbf7d0' },
  info: { bg: '#eef2ff', fg: '#4338ca', bd: '#c7d2fe' },
  muted: { bg: '#f9fafb', fg: '#6b7280', bd: '#e5e7eb' },
};

function fmtPct(v) {
  return v === null || v === undefined
    ? '–'
    : (v >= 99.5 && v < 100 ? '>99' : Math.round(v)) + '%';
}

function fmtNum(v, digits) {
  if (v === null || v === undefined) return '–';
  return Number(v).toLocaleString(undefined, {
    maximumFractionDigits: digits || 0,
  });
}

function fmtDate(d) {
  if (!d) return '–';
  var dt = new Date(iptDateMs(d));
  return (
    IPT_WEEKDAYS[dt.getUTCDay()] +
    ' ' +
    dt.getUTCDate() +
    ' ' +
    [
      'Jan',
      'Feb',
      'Mar',
      'Apr',
      'May',
      'Jun',
      'Jul',
      'Aug',
      'Sep',
      'Oct',
      'Nov',
      'Dec',
    ][dt.getUTCMonth()]
  );
}

function Badge(props) {
  var t = IPT_TONE[props.tone || 'muted'];
  return (
    <span
      title={props.title}
      style={{
        background: t.bg,
        color: t.fg,
        border: '1px solid ' + t.bd,
        borderRadius: 9999,
        padding: '1px 8px',
        fontSize: 12,
        fontWeight: 600,
        whiteSpace: 'nowrap',
      }}
    >
      {props.children}
    </span>
  );
}

// A rate with its numerator/denominator, banded, or "too few" under the floor.
function Rate(props) {
  var den = props.den || 0;
  if (!den) return <span className="text-gray-400">–</span>;
  if (props.floor && den < props.floor)
    return (
      <span
        className="text-gray-400"
        title={'Only ' + den + ' to divide by: too few to say'}
      >
        {props.num}/{den}
      </span>
    );
  var v = iptPct(props.num, den);
  var t = props.band ? IPT_TONE[props.band] : null;
  return (
    <span title={props.num + ' of ' + den}>
      <span style={t ? { color: t.fg, fontWeight: 700 } : { fontWeight: 600 }}>
        {fmtPct(v)}
      </span>
      <span className="text-xs text-gray-400" style={{ marginLeft: 4 }}>
        {props.num}/{den}
      </span>
    </span>
  );
}

function bandHighGood(v, green, yellow) {
  if (v === null || v === undefined) return null;
  return v >= green ? 'green' : v >= yellow ? 'yellow' : 'red';
}

function InfoLink(props) {
  return (
    <button
      type="button"
      onClick={function () {
        props.onOpen(props.id);
      }}
      title="How is this calculated?"
      aria-label="How is this calculated?"
      style={{
        marginLeft: 4,
        color: '#9ca3af',
        fontSize: 12,
        lineHeight: 1,
        cursor: 'pointer',
        background: 'none',
        border: 0,
      }}
    >
      ⓘ
    </button>
  );
}

// Horizontal bars: [{label, n, color?}] scaled to the largest.
function HBars(props) {
  var items = props.items || [];
  var max = Math.max.apply(
    null,
    [1].concat(
      items.map(function (i) {
        return i.n;
      }),
    ),
  );
  var total = props.total;
  if (!items.length)
    return (
      <div className="text-sm text-gray-400">
        {props.empty || 'Nothing recorded yet.'}
      </div>
    );
  return (
    <div className="space-y-1.5">
      {items.map(function (i) {
        return (
          <div key={i.label} className="flex items-center gap-2 text-sm">
            <div
              className="text-gray-700 truncate"
              style={{
                width: 'min(' + (props.labelWidth || 220) + 'px, 45%)',
                flexShrink: 0,
              }}
              title={i.label}
            >
              {i.label}
            </div>
            <div
              style={{
                flex: 1,
                minWidth: 40,
                background: '#f3f4f6',
                borderRadius: 4,
                height: 14,
                position: 'relative',
              }}
            >
              <div
                style={{
                  width: (100 * i.n) / max + '%',
                  background: i.color || '#6366f1',
                  height: 14,
                  borderRadius: 4,
                }}
              />
            </div>
            <div
              className="tabular-nums text-gray-700"
              style={{
                minWidth: total ? 74 : 32,
                textAlign: 'right',
                flexShrink: 0,
                whiteSpace: 'nowrap',
              }}
            >
              {i.n}
              {total ? (
                <span className="text-xs text-gray-400">
                  {' '}
                  ({fmtPct(iptPct(i.n, total))})
                </span>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// Daily stacked columns: [{date, parts: [{n, color, label}]}].
function DayColumns(props) {
  var days = props.days || [];
  if (!days.length)
    return <div className="text-sm text-gray-400">No dosing recorded yet.</div>;
  var max = Math.max.apply(
    null,
    [1].concat(
      days.map(function (d) {
        return d.parts.reduce(function (s, p) {
          return s + p.n;
        }, 0);
      }),
    ),
  );
  var h = props.height || 140;
  return (
    <div>
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-end',
          gap: 6,
          height: h + 56,
          overflowX: 'auto',
          paddingBottom: 2,
          paddingTop: 4,
        }}
      >
        {days.map(function (d) {
          var total = d.parts.reduce(function (s, p) {
            return s + p.n;
          }, 0);
          var wd = iptWeekday(d.date);
          return (
            <div
              key={d.date}
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                minWidth: 34,
                flex: '0 1 56px',
              }}
              title={
                fmtDate(d.date) +
                ': ' +
                d.parts
                  .map(function (p) {
                    return p.label + ' ' + p.n;
                  })
                  .join(', ')
              }
            >
              <div
                className="text-xs text-gray-500 tabular-nums"
                style={{ minHeight: 16 }}
              >
                {total || ''}
              </div>
              <div
                style={{
                  height: (h * total) / max,
                  width: '70%',
                  display: 'flex',
                  flexDirection: 'column-reverse',
                  borderRadius: 3,
                  overflow: 'hidden',
                }}
              >
                {d.parts.map(function (p, i) {
                  return p.n ? (
                    <div
                      key={i}
                      style={{
                        height: (100 * p.n) / total + '%',
                        background: p.color,
                      }}
                    />
                  ) : null;
                })}
              </div>
              <div
                className="text-xs"
                style={{
                  color: wd === 0 || wd === 6 ? '#dc2626' : '#6b7280',
                  marginTop: 2,
                  whiteSpace: 'nowrap',
                }}
              >
                {IPT_WEEKDAYS[wd]}
              </div>
              <div
                className="text-xs text-gray-400"
                style={{ whiteSpace: 'nowrap' }}
              >
                {d.date.slice(8)}/{d.date.slice(5, 7)}
              </div>
            </div>
          );
        })}
      </div>
      {props.legend ? <Legend items={props.legend} /> : null}
    </div>
  );
}

function Legend(props) {
  return (
    <div className="flex flex-wrap gap-3 mt-2 text-xs text-gray-600">
      {props.items.map(function (l) {
        return (
          <span key={l.label} className="inline-flex items-center gap-1">
            <span
              style={{
                width: 10,
                height: 10,
                borderRadius: l.round ? 9999 : 2,
                background: l.color,
                border: l.border || 'none',
                display: 'inline-block',
              }}
            />
            {l.label}
          </span>
        );
      })}
    </div>
  );
}

// Cumulative line: two series over dates, plain SVG.
function CumulativeLine(props) {
  var days = props.days || [];
  if (days.length < 2)
    return (
      <div className="text-sm text-gray-400">
        The line appears after a second day of dosing.
      </div>
    );
  var w = 600;
  var h = 160;
  var padL = 32;
  var padB = 20;
  var series = props.series;
  var max = 1;
  series.forEach(function (s) {
    days.forEach(function (d) {
      if (d[s.key] > max) max = d[s.key];
    });
  });
  function x(i) {
    return padL + ((w - padL - 8) * i) / (days.length - 1);
  }
  function y(v) {
    return 8 + (h - padB - 8) * (1 - v / max);
  }
  return (
    <div>
      <svg
        viewBox={'0 0 ' + w + ' ' + h}
        style={{ width: '100%', height: 'auto' }}
        role="img"
        aria-label={props.label}
      >
        <line x1={padL} y1={y(0)} x2={w - 8} y2={y(0)} stroke="#e5e7eb" />
        <text
          x={padL - 6}
          y={y(max) + 4}
          fontSize="10"
          textAnchor="end"
          fill="#9ca3af"
        >
          {max}
        </text>
        <text
          x={padL - 6}
          y={y(0) + 4}
          fontSize="10"
          textAnchor="end"
          fill="#9ca3af"
        >
          0
        </text>
        {series.map(function (s) {
          var pts = days
            .map(function (d, i) {
              return x(i) + ',' + y(d[s.key]);
            })
            .join(' ');
          return (
            <polyline
              key={s.key}
              points={pts}
              fill="none"
              stroke={s.color}
              strokeWidth="2.5"
            />
          );
        })}
        <text x={x(0)} y={h - 4} fontSize="10" fill="#9ca3af">
          {fmtDate(days[0].date)}
        </text>
        <text
          x={x(days.length - 1)}
          y={h - 4}
          fontSize="10"
          textAnchor="end"
          fill="#9ca3af"
        >
          {fmtDate(days[days.length - 1].date)}
        </text>
      </svg>
      <Legend
        items={series.map(function (s) {
          return { label: s.label, color: s.color };
        })}
      />
    </div>
  );
}

// Cumulative registrations at a school over calendar days: a curve that
// flattens means the school is probably covered.
function SaturationSpark(props) {
  var pts = props.points || [];
  if (!pts.length) return null;
  var start = props.first;
  var span = Math.max(1, iptDayDiff(start, props.today));
  var max = pts[pts.length - 1].n || 1;
  var w = 140;
  var h = 30;
  var path = [];
  var prevN = 0;
  pts.forEach(function (p) {
    var x = (w * iptDayDiff(start, p.date)) / span;
    path.push(x.toFixed(1) + ',' + (h - (h * prevN) / max).toFixed(1));
    path.push(x.toFixed(1) + ',' + (h - (h * p.n) / max).toFixed(1));
    prevN = p.n;
  });
  path.push(w + ',' + (h - (h * prevN) / max).toFixed(1));
  return (
    <svg
      width={w}
      height={h + 2}
      viewBox={'0 -1 ' + w + ' ' + (h + 2)}
      role="img"
      aria-label={'Cumulative registrations, ' + max + ' so far'}
    >
      <polyline
        points={path.join(' ')}
        fill="none"
        stroke="#6366f1"
        strokeWidth="2"
      />
    </svg>
  );
}

// One registration in the duplicate review: its consent photo, and the facts a
// reviewer compares across the row. Declared at top level (see the header).
function DupPhotoCard(props) {
  var c = props.child;
  var photo = props.photo || { status: 'loading' };
  var imgUrl =
    photo.status === 'ok' && props.oppId
      ? '/labs/workflow/api/image/' +
        props.oppId +
        '/' +
        encodeURIComponent(photo.blob) +
        '/'
      : null;
  var box = {
    width: '100%',
    height: 240,
    borderRadius: 8,
    background: '#f3f4f6',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  };
  return (
    <div
      style={{
        width: 240,
        flex: '0 0 240px',
        border: '1px solid #e5e7eb',
        borderRadius: 10,
        padding: 8,
        background: '#fff',
      }}
    >
      {imgUrl ? (
        <a
          href={imgUrl}
          target="_blank"
          rel="noreferrer"
          title="Open the full-size consent photo"
        >
          <div style={box}>
            <img
              src={imgUrl}
              alt={'Consent photo for ' + (c.name || 'child')}
              loading="lazy"
              style={{
                maxWidth: '100%',
                maxHeight: '100%',
                objectFit: 'contain',
              }}
            />
          </div>
        </a>
      ) : (
        <div style={box} className="text-xs text-gray-500 text-center">
          {photo.status === 'loading'
            ? 'Loading photo…'
            : photo.status === 'error'
              ? 'Could not load the photo'
              : 'No consent photo on this visit'}
        </div>
      )}
      <div className="mt-2 text-sm">
        <div className="font-semibold text-gray-900 truncate" title={c.name}>
          {c.name || 'Name not recorded'}
        </div>
        <div className="text-xs text-gray-600 mt-0.5">{props.schoolLabel}</div>
        <div className="text-xs text-gray-600">
          Age {c.age !== null && c.age !== undefined ? c.age : '–'} ·{' '}
          {c.sex ? iptTitle(c.sex) : 'sex –'}
        </div>
        <div className="text-xs text-gray-600">
          Caregiver phone: {c.phone || '–'}
        </div>
        <div className="text-xs text-gray-600">
          Registered {fmtDate(c.regDate)} by {props.workerName}
        </div>
        <div className="flex flex-wrap gap-3 mt-1">
          <a
            href={props.visitUrl}
            target="_blank"
            rel="noreferrer"
            className="text-xs text-indigo-700 hover:underline"
            title="The Day 1 registration visit"
          >
            Open visit in Connect ↗
          </a>
          <button
            type="button"
            onClick={props.onOpenChild}
            className="text-xs text-indigo-700 hover:underline"
          >
            Timeline
          </button>
        </div>
      </div>
    </div>
  );
}

var DOSE_CELL = {
  given: { color: '#16a34a', fill: true, label: 'Given on the day' },
  late: { color: '#d97706', fill: true, label: 'Given late (catch-up)' },
  failed: {
    color: '#dc2626',
    fill: false,
    half: true,
    label: 'Attempted, not swallowed',
  },
  missed: { color: '#dc2626', fill: false, label: 'Missed (due date passed)' },
  pending: { color: '#9ca3af', fill: false, label: 'Not yet due' },
  blocked: {
    color: '#d1d5db',
    fill: false,
    dash: true,
    label: 'Waiting on the dose before',
  },
  ended: {
    color: '#64748b',
    fill: false,
    cross: true,
    label: 'Course stopped or referred',
  },
};

function DoseDot(props) {
  var spec = DOSE_CELL[props.cell.state] || DOSE_CELL.pending;
  var title = spec.label;
  if (props.cell.date) title += ' · ' + fmtDate(props.cell.date);
  if (props.cell.late) title += ' · ' + props.cell.late + ' day(s) late';
  if (props.cell.overdue)
    title += ' · ' + props.cell.overdue + ' day(s) overdue';
  return (
    <span
      title={title}
      aria-label={title}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}
    >
      <svg width="16" height="16" viewBox="0 0 16 16">
        {spec.half ? (
          <g>
            <circle
              cx="8"
              cy="8"
              r="6"
              fill="none"
              stroke={spec.color}
              strokeWidth="2"
            />
            <path d="M8 2 A6 6 0 0 0 8 14 Z" fill={spec.color} />
          </g>
        ) : spec.dash ? (
          <line
            x1="4"
            y1="8"
            x2="12"
            y2="8"
            stroke={spec.color}
            strokeWidth="2"
            strokeLinecap="round"
          />
        ) : spec.cross ? (
          <g stroke={spec.color} strokeWidth="2">
            <line x1="3" y1="3" x2="13" y2="13" />
            <line x1="13" y1="3" x2="3" y2="13" />
          </g>
        ) : (
          <circle
            cx="8"
            cy="8"
            r="6"
            fill={spec.fill ? spec.color : 'none'}
            stroke={spec.color}
            strokeWidth="2"
          />
        )}
      </svg>
      {props.showLate && props.cell.late ? (
        <span className="text-xs" style={{ color: spec.color }}>
          +{props.cell.late}d
        </span>
      ) : null}
    </span>
  );
}

function SortHeader(props) {
  var on = props.sort.key === props.k;
  return (
    <th
      onClick={function () {
        props.onSort(props.k);
      }}
      className="px-3 py-2 text-left text-xs font-semibold text-gray-600 cursor-pointer select-none whitespace-nowrap"
      style={props.right ? { textAlign: 'right' } : null}
      title="Sort"
    >
      {props.children}
      <span style={{ color: on ? '#4f46e5' : '#d1d5db', marginLeft: 3 }}>
        {on ? (props.sort.dir === 'asc' ? '▲' : '▼') : '↕'}
      </span>
    </th>
  );
}

function useSort(initialKey, initialDir) {
  var st = React.useState({ key: initialKey, dir: initialDir || 'desc' });
  function onSort(k) {
    st[1](function (s) {
      return { key: k, dir: s.key === k && s.dir === 'desc' ? 'asc' : 'desc' };
    });
  }
  function apply(rows, getters) {
    var g = getters[st[0].key];
    if (!g) return rows;
    var dir = st[0].dir === 'asc' ? 1 : -1;
    return rows.slice().sort(function (a, b) {
      var va = g(a);
      var vb = g(b);
      if (va === vb) return 0;
      if (va === null || va === undefined) return 1;
      if (vb === null || vb === undefined) return -1;
      return va < vb ? -dir : dir;
    });
  }
  return { sort: st[0], onSort: onSort, apply: apply };
}

function downloadCsv(filename, text) {
  try {
    var blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 1000);
  } catch (e) {
    window.alert('Could not build the CSV: ' + e.message);
  }
}

function Select(props) {
  return (
    <label
      className="flex flex-col text-xs text-gray-500"
      style={{ minWidth: props.minWidth || 150 }}
    >
      <span className="mb-1 font-semibold">{props.label}</span>
      <select
        value={props.value}
        onChange={function (e) {
          props.onChange(e.target.value);
        }}
        className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm text-gray-800 bg-white"
      >
        {props.options.map(function (o) {
          return (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          );
        })}
      </select>
    </label>
  );
}

function DateInput(props) {
  return (
    <label className="flex flex-col text-xs text-gray-500">
      <span className="mb-1 font-semibold">{props.label}</span>
      <input
        type="date"
        value={props.value}
        onChange={function (e) {
          props.onChange(e.target.value);
        }}
        className="border border-gray-300 rounded-lg px-2 py-1 text-sm text-gray-800 bg-white"
      />
    </label>
  );
}

// A two-column key figure list inside a card.
function KV(props) {
  return (
    <div
      className="flex items-baseline justify-between gap-3 py-1.5 text-sm"
      style={{ borderBottom: '1px solid #f3f4f6' }}
    >
      <span className="text-gray-600">{props.label}</span>
      <span className="tabular-nums text-gray-900 text-right">
        {props.children}
      </span>
    </div>
  );
}

// The drill-down for one child: every form in order, against the 3-day schedule.
function ChildDrawer(props) {
  var c = props.child;
  if (!c) return null;
  var st = props.status;
  var opts = props.opts;
  var meta = iptBucketMeta(st.bucket);
  React.useEffect(function () {
    function onKey(e) {
      if (e.key === 'Escape') props.onClose();
    }
    window.addEventListener('keydown', onKey);
    return function () {
      window.removeEventListener('keydown', onKey);
    };
  }, []);
  var schedule = [1, 2, 3].map(function (n) {
    return { n: n, cell: iptDoseCell(c, n, st, props.today) };
  });
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Child timeline"
      onClick={props.onClose}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(17,24,39,0.35)',
        zIndex: 60,
        display: 'flex',
        justifyContent: 'flex-end',
      }}
    >
      <div
        onClick={function (e) {
          e.stopPropagation();
        }}
        style={{
          width: 'min(560px, 100%)',
          height: '100%',
          background: '#fff',
          overflowY: 'auto',
          boxShadow: '-8px 0 24px rgba(0,0,0,0.12)',
        }}
      >
        <div
          className="px-5 py-4"
          style={{
            borderBottom: '1px solid #e5e7eb',
            position: 'sticky',
            top: 0,
            background: '#fff',
          }}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-xs text-gray-500">Child</div>
              <div className="text-lg font-bold text-gray-900 truncate">
                {c.name || 'Name not recorded'}
              </div>
              <div className="text-xs text-gray-500 mt-0.5">
                {props.schoolLabel} · {c.sex ? iptTitle(c.sex) : 'sex –'} ·{' '}
                {c.age !== null && c.age !== undefined ? c.age + ' y' : 'age –'}{' '}
                ·{' '}
                {c.weight !== null && c.weight !== undefined
                  ? c.weight + ' kg'
                  : 'weight –'}
              </div>
            </div>
            <button
              type="button"
              onClick={props.onClose}
              className="text-gray-400 hover:text-gray-700 text-xl"
              aria-label="Close"
            >
              ×
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2 mt-2">
            <span
              style={{
                background: meta.color,
                color: '#fff',
                borderRadius: 9999,
                padding: '1px 10px',
                fontSize: 12,
                fontWeight: 700,
              }}
            >
              {meta.label}
            </span>
            {st.overdueDays ? (
              <Badge tone="yellow">{st.overdueDays} day(s) overdue</Badge>
            ) : null}
            {st.nextDose && st.bucket === 'on_track' ? (
              <Badge tone="info">
                Dose {st.nextDose} due {fmtDate(st.nextDue)}
              </Badge>
            ) : null}
            {c.caseOnly ? (
              <Badge
                tone="yellow"
                title="This child has a CommCare case but no Connect visit yet"
              >
                No Connect visit yet
              </Badge>
            ) : null}
          </div>
        </div>
        <div className="px-5 py-4 space-y-5">
          <div>
            <div
              className="text-xs font-semibold text-gray-500 mb-2"
              style={{ textTransform: 'uppercase', letterSpacing: '0.04em' }}
            >
              Course
            </div>
            <div className="grid grid-cols-3 gap-2">
              {schedule.map(function (s) {
                var due =
                  s.n === 1
                    ? c.regDate
                    : c.doseDates[s.n - 1]
                      ? iptAddDays(c.doseDates[s.n - 1], 1)
                      : null;
                return (
                  <div
                    key={s.n}
                    className="rounded-lg px-3 py-2"
                    style={{ border: '1px solid #e5e7eb' }}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold text-gray-800">
                        Dose {s.n}
                      </span>
                      <DoseDot cell={s.cell} />
                    </div>
                    <div className="text-xs text-gray-500 mt-1">
                      Due {due ? fmtDate(due) : '–'}
                    </div>
                    <div className="text-xs text-gray-700">
                      {c.doseDates[s.n]
                        ? 'Given ' + fmtDate(c.doseDates[s.n])
                        : DOSE_CELL[s.cell.state].label}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
          <div>
            <div
              className="text-xs font-semibold text-gray-500 mb-2"
              style={{ textTransform: 'uppercase', letterSpacing: '0.04em' }}
            >
              Forms ({c.forms.length})
            </div>
            {c.forms.length === 0 ? (
              <div className="text-sm text-gray-400">
                No dosing form has reached Connect for this child.
              </div>
            ) : null}
            <ol style={{ borderLeft: '2px solid #e5e7eb', marginLeft: 6 }}>
              {c.forms.map(function (f, i) {
                var r = f.row;
                var drift =
                  f.dose > 1 && f.gps && c.gps
                    ? iptHaversine(f.gps, c.gps)
                    : null;
                var sw = iptStr(r.swallowed).toLowerCase();
                return (
                  <li
                    key={i}
                    style={{
                      position: 'relative',
                      paddingLeft: 16,
                      paddingBottom: 14,
                    }}
                  >
                    <span
                      style={{
                        position: 'absolute',
                        left: -7,
                        top: 4,
                        width: 12,
                        height: 12,
                        borderRadius: 9999,
                        background: f.success ? '#16a34a' : '#dc2626',
                        border: '2px solid #fff',
                      }}
                    />
                    <div className="text-sm font-semibold text-gray-900">
                      {iptIsDay1(r)
                        ? 'Day 1 · registration and dose 1'
                        : 'Follow-up · dose ' + (f.dose || '?')}
                      <span className="font-normal text-gray-500">
                        {' '}
                        ·{' '}
                        {f.ms
                          ? fmtDate(f.date) +
                            ' ' +
                            pad2(iptLocalHour(f.ms, opts.utcOffsetHours)) +
                            ':' +
                            pad2(new Date(f.ms).getUTCMinutes())
                          : f.date}
                      </span>
                    </div>
                    <div className="text-xs text-gray-600 mt-0.5">
                      By {props.nameOf(f.username)}
                      {iptStr(r.due_status) ? ' · ' + iptStr(r.due_status) : ''}
                      {drift !== null
                        ? ' · ' + Math.round(drift) + ' m from dose 1'
                        : ''}
                      {f.gps && f.gps.acc !== null
                        ? ' · GPS ±' + Math.round(f.gps.acc) + ' m'
                        : f.gps
                          ? ''
                          : ' · no GPS'}
                    </div>
                    <div className="flex flex-wrap gap-1 mt-1">
                      <Badge tone={iptYes(r.dot) ? 'green' : 'red'}>
                        {iptYes(r.dot) ? 'Observed' : 'Not observed'}
                      </Badge>
                      {sw ? (
                        <Badge tone={sw === 'yes' ? 'green' : 'red'}>
                          {sw === 'yes'
                            ? 'Swallowed'
                            : sw === 'vomited'
                              ? 'Vomited' +
                                (iptYes(r.redose_swallowed)
                                  ? ', re-dosed OK'
                                  : '')
                              : 'Not swallowed'}
                        </Badge>
                      ) : null}
                      {iptPresent(r.obs_result) ? (
                        <Badge
                          tone={
                            iptStr(r.obs_result) === 'no' ? 'muted' : 'yellow'
                          }
                        >
                          {{
                            no: 'No reaction',
                            yes: 'Reaction reported',
                            pending: 'Still observing',
                            left_early: 'Left before 30 min',
                          }[iptStr(r.obs_result)] || iptStr(r.obs_result)}
                        </Badge>
                      ) : null}
                      <Badge tone={iptPresent(r.dose_photo) ? 'muted' : 'red'}>
                        {iptPresent(r.dose_photo)
                          ? 'Dose photo'
                          : 'No dose photo'}
                      </Badge>
                      {iptIsDay1(r) ? (
                        <Badge
                          tone={iptPresent(r.consent_photo) ? 'muted' : 'red'}
                        >
                          {iptPresent(r.consent_photo)
                            ? 'Consent photo'
                            : 'No consent photo'}
                        </Badge>
                      ) : null}
                      <Badge
                        tone={
                          iptPresent(r.child_name_audio) ? 'muted' : 'yellow'
                        }
                      >
                        {iptPresent(r.child_name_audio)
                          ? 'Name audio'
                          : 'No name audio'}
                      </Badge>
                      {iptStr(r.status) && iptStr(r.status) !== 'approved' ? (
                        <Badge tone="yellow">Connect: {iptStr(r.status)}</Badge>
                      ) : null}
                    </div>
                    {iptStr(r.not_given_reason) ? (
                      <div className="text-xs text-gray-600 mt-1">
                        Reason not given: {iptStr(r.not_given_reason)}
                      </div>
                    ) : null}
                  </li>
                );
              })}
              {(props.aes || []).map(function (a, i) {
                return (
                  <li
                    key={'ae' + i}
                    style={{
                      position: 'relative',
                      paddingLeft: 16,
                      paddingBottom: 14,
                    }}
                  >
                    <span
                      style={{
                        position: 'absolute',
                        left: -7,
                        top: 4,
                        width: 12,
                        height: 12,
                        borderRadius: 2,
                        background: '#f59e0b',
                        border: '2px solid #fff',
                      }}
                    />
                    <div className="text-sm font-semibold text-gray-900">
                      Adverse event · {iptTitle(a.severity) || 'severity –'}
                      <span className="font-normal text-gray-500">
                        {' '}
                        · {fmtDate(a.date)}
                      </span>
                    </div>
                    <div className="text-xs text-gray-600">
                      After dose {a.dose || '?'} ·{' '}
                      {a.symptoms
                        .map(function (s) {
                          return IPT_SYMPTOMS[s] || s;
                        })
                        .join(', ') || 'no symptoms recorded'}{' '}
                      ·{' '}
                      {IPT_AE_STATUS[a.status] ||
                        a.status ||
                        'status not recorded'}
                    </div>
                  </li>
                );
              })}
            </ol>
          </div>
          {c.caseRow ? (
            <div className="text-xs text-gray-500">
              CommCare case: {iptStr(c.caseRow.doses_given_count) || '0'}{' '}
              dose(s) recorded, course status{' '}
              {iptStr(c.caseRow.course_status) || '–'}
              {iptStr(c.caseRow.next_dose_due_date)
                ? ', next due ' + fmtDate(iptStr(c.caseRow.next_dose_due_date))
                : ''}
              .
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

// ===========================================================================
// 3. Definitions -- one entry per figure on the page (the "ⓘ" links land here)
// ===========================================================================

var IPT_DEFINITIONS = [
  {
    id: 'registered',
    section: 'Course',
    name: 'Children registered',
    def: 'Day 1 forms that reached registration: eligible to proceed today and consent for all 3 doses. Children who have a CommCare case but no Connect visit yet are included and marked.',
    source: 'Connect visits (Day 1 form); CommCare child cases',
  },
  {
    id: 'status',
    section: 'Course',
    name: 'Course status (one per child)',
    def: 'Completed: 3 doses given and swallowed. On track: next dose due today or later. Overdue: the next dose was due 1-3 days ago (the form allows a revisit within 3 days). Missed: more than 3 days overdue, or the course was closed as incomplete. No dose 1: registered, but dose 1 was not given successfully. Stopped / Referred: from the safety screen, observation or an Adverse Event Log. A dose counts as given when it was observed and swallowed (or vomited and successfully re-dosed).',
    source: 'Connect visits; course_status on the CommCare case when available',
  },
  {
    id: 'cascade',
    section: 'Course',
    name: 'Dose 1 → 2 → 3 funnel',
    def: 'Each step counts only children who could already have had that dose: those who got it, plus those whose due date (the day after the previous dose) has passed. A child registered today is not counted as a drop-out tomorrow morning.',
    source: 'Connect visits',
  },
  {
    id: 'full_course',
    section: 'Course',
    name: 'Full course (3/3)',
    def: 'Children with all 3 doses, out of children whose last chance to finish (registration date + 2 days + the 3-day revisit window) has passed, plus any who already finished.',
    source: 'Connect visits',
    threshold: 'Green ≥ 90%, amber ≥ 80%',
  },
  {
    id: 'consecutive',
    section: 'Course',
    name: 'Consecutive-day completion',
    def: 'Of children who completed, those whose doses were on three consecutive calendar days (West Africa Time).',
    source: 'Connect visits',
    threshold: 'Green ≥ 85%, amber ≥ 70%',
  },
  {
    id: 'dose_grid',
    section: 'Course',
    name: 'Child × dose grid',
    def: 'One dot per dose. Filled green: given on its due day. Filled amber: given late (catch-up, with the number of days). Half red: attempted, not swallowed. Hollow red: due date passed with no dose. Hollow grey: not yet due. Grey X: course stopped or referred.',
    source: 'Connect visits',
  },
  {
    id: 'intervals',
    section: 'Course',
    name: 'Days between doses',
    def: 'Calendar days (WAT) from one successful dose to the next. The protocol is exactly 1.',
    source: 'Connect visits',
  },
  {
    id: 'attempts',
    section: 'Course',
    name: 'Attempts vs successes',
    def: 'Every dosing form for that dose number, and how it ended. Separates children who did not come back from children who came back but whose dose failed.',
    source: 'Connect visits',
  },
  {
    id: 'schools',
    section: 'Reach',
    name: 'Schools',
    def: 'School is typed by hand, so the page groups spellings that normalise to the same letters ("Deba cps", "debacps") and then joins groups whose Day 1 GPS points are within 250 m. The label is the most common full spelling; every spelling seen is listed. There is no school enrolment list, so reach means children registered, not a share of an eligible population.',
    source: 'Connect visits (school name, GPS)',
  },
  {
    id: 'saturation',
    section: 'Reach',
    name: 'School saturation',
    def: 'Cumulative registrations per school by day. A curve that flattens suggests the school is covered; one still rising means work remains. This stands in for coverage until enrolment figures exist.',
    source: 'Connect visits',
  },
  {
    id: 'ae_rate',
    section: 'Safety',
    name: 'Adverse events per 1,000 doses',
    def: 'Moderate, severe and danger-sign Adverse Event Logs, per 1,000 doses given successfully in the same view.',
    source:
      'CommCare HQ (Adverse Event Log form); Connect visits for the dose count',
  },
  {
    id: 'unlogged',
    section: 'Safety',
    name: 'Reactions without a log',
    def: 'Dosing forms whose 30-minute observation recorded a reaction, for a child with no Adverse Event Log. The form tells the worker to open one.',
    source: 'Connect visits; CommCare HQ',
  },
  {
    id: 'compliance',
    section: 'Protocol',
    name: 'Protocol compliance rows',
    def: 'Each row is a count over its own denominator (shown). Rows with fewer than 5 in the denominator are not coloured.',
    source: 'Connect visits',
  },
  {
    id: 'authenticity',
    section: 'Authenticity',
    name: 'Authenticity flag',
    def: 'Red if any contributing check is red, amber if any is amber, otherwise green. Checks marked "info" are shown but never set the flag. A check needs at least 5 forms or children before it is coloured. A flag is a reason to look (open an audit), not a finding.',
    source: 'Connect visits',
  },
].concat(
  IPT_AUTH_CHECKS.map(function (c) {
    return {
      id: 'auth_' + c.id,
      section: 'Authenticity',
      name: c.label,
      def: c.hint,
      source: 'Connect visits',
      threshold:
        c.id === 'too_perfect'
          ? 'Shown when it applies'
          : 'Amber ≥ ' +
            c.yellow +
            (c.unit === '%' ? '%' : ' ' + c.unit) +
            ', red ≥ ' +
            c.red +
            (c.unit === '%' ? '%' : ' ' + c.unit) +
            (c.contributes ? '' : ' (info only)'),
    };
  }),
);

// ===========================================================================
// 4. The page
// ===========================================================================

var IPT_TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'completion', label: 'Dose completion' },
  { id: 'reach', label: 'Reach' },
  { id: 'safety', label: 'Safety' },
  { id: 'protocol', label: 'Protocol' },
  { id: 'authenticity', label: 'Authenticity' },
  { id: 'duplicates', label: 'Duplicates' },
  { id: 'workers', label: 'Field workers' },
  { id: 'definitions', label: 'Definitions' },
];

var DOSE_COLORS = {
  d1: '#6366f1',
  d2: '#0ea5e9',
  d3: '#16a34a',
  failed: '#f87171',
};

// The opportunity's organisation slug, for links into Connect: from the
// opportunity list the labs header embeds on the page (the context picker's
// #opportunity-data, or the multi-opp editor's #user-opportunities), else
// config.connect_org_slug.
function iptOrgSlugFor(oppId, cfg) {
  var ids = ['opportunity-data', 'user-opportunities'];
  for (var i = 0; i < ids.length; i++) {
    try {
      var el = document.getElementById(ids[i]);
      var list = el ? JSON.parse(el.textContent || '[]') : [];
      for (var j = 0; j < list.length; j++) {
        if (String(list[j].id) === String(oppId) && list[j].organization)
          return list[j].organization;
      }
    } catch (e) {
      /* malformed or absent: try the next source */
    }
  }
  return (cfg && cfg.connect_org_slug) || '';
}

function readUrlParam(name) {
  try {
    return new URLSearchParams(window.location.search).get(name) || '';
  } catch (e) {
    return '';
  }
}

function writeUrlParam(name, value) {
  try {
    var u = new URL(window.location.href);
    if (value) u.searchParams.set(name, value);
    else u.searchParams.delete(name);
    window.history.replaceState(null, '', u.toString());
  } catch (e) {
    /* the page works without it */
  }
}

function pipelineProblem(p) {
  if (!p || !p.metadata) return null;
  var m = p.metadata;
  if (m.auth_error) return m.auth_error;
  if (m.error) return m.error;
  var per = m.per_opp || {};
  var msgs = [];
  Object.keys(per).forEach(function (k) {
    if (per[k] && (per[k].error || per[k].auth_error))
      msgs.push(per[k].error || per[k].auth_error);
  });
  return msgs.length ? msgs.join('; ') : null;
}

function WorkflowUI(props) {
  var R = window.LabsReport;
  var definition = props.definition || {};
  var cfg = definition.config || {};
  var opts = {
    utcOffsetHours:
      cfg.utc_offset_hours !== undefined
        ? Number(cfg.utc_offset_hours)
        : IPT_DEFAULTS.utcOffsetHours,
    minChildren: cfg.min_children || IPT_DEFAULTS.minChildren,
    minDoses: cfg.min_doses || IPT_DEFAULTS.minDoses,
    revisitDays: IPT_DEFAULTS.revisitDays,
    schoolRadiusM: IPT_DEFAULTS.schoolRadiusM,
  };
  var view = props.view || {};
  var pipelines = view.pipelines || props.pipelines || {};
  var workers = view.workers || props.workers || [];
  var doseRows = (pipelines.doses && pipelines.doses.rows) || [];
  var caseRows = (pipelines.child_cases && pipelines.child_cases.rows) || [];
  var aeRows = (pipelines.ae_log && pipelines.ae_log.rows) || [];
  var problems = [
    { label: 'Dosing forms (Connect)', msg: pipelineProblem(pipelines.doses) },
    {
      label: 'Child cases (CommCare)',
      msg: pipelineProblem(pipelines.child_cases),
    },
    {
      label: 'Adverse Event Log (CommCare)',
      msg: pipelineProblem(pipelines.ae_log),
    },
  ].filter(function (p) {
    return p.msg;
  });

  var today = iptLocalDate(Date.now(), opts.utcOffsetHours);

  var tabState = React.useState(function () {
    var t = readUrlParam('tab');
    return IPT_TABS.some(function (x) {
      return x.id === t;
    })
      ? t
      : 'overview';
  });
  var tab = tabState[0];
  var setTabRaw = tabState[1];
  function setTab(t) {
    setTabRaw(t);
    writeUrlParam('tab', t === 'overview' ? '' : t);
    try {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (e) {
      /* old browsers */
    }
  }
  var defFocus = React.useState('');
  function openDef(id) {
    defFocus[1](id);
    setTab('definitions');
  }
  var filterState = React.useState({
    flw: '',
    school: '',
    sex: '',
    from: '',
    to: '',
  });
  var filters = filterState[0];
  function setFilter(k, v) {
    filterState[1](function (f) {
      var n = Object.assign({}, f);
      n[k] = v;
      return n;
    });
  }
  var childState = React.useState(null);
  var openChildId = childState[0];
  var setOpenChildId = childState[1];
  var gridFilter = React.useState('attention');
  var cohortBy = React.useState('day');
  var workerOpen = React.useState('');
  var workerSort = useSort('missing', 'desc');
  var authList = React.useState('');
  var dupCheck = React.useState(function () {
    var d = readUrlParam('dup');
    return IPT_DUP_CHECK_IDS.indexOf(d) >= 0 ? d : 'dup_name_age';
  });
  function setDupCheck(id) {
    dupCheck[1](id);
    writeUrlParam('dup', id);
  }
  // visitId -> {status: 'loading' | 'ok' | 'none' | 'error', blob}
  var photoState = React.useState({});

  var nameOf = React.useMemo(
    function () {
      var m = {};
      workers.forEach(function (w) {
        m[w.username] = w.name || w.username;
      });
      return function (u) {
        return m[u] || u || 'Unknown worker';
      };
    },
    [workers],
  );

  // ---- everything below is derived; the filter bar narrows the children ----
  var built = React.useMemo(
    function () {
      return iptBuildChildren(doseRows, caseRows, opts);
    },
    [doseRows, caseRows],
  );
  var allChildren = built.children;
  var schoolInfo = React.useMemo(
    function () {
      return iptClusterSchools(allChildren, opts.schoolRadiusM);
    },
    [allChildren],
  );
  var children = React.useMemo(
    function () {
      return allChildren.filter(function (c) {
        if (filters.flw && c.username !== filters.flw && !c.flws[filters.flw])
          return false;
        if (filters.school && schoolInfo.byChild[c.id] !== filters.school)
          return false;
        if (filters.sex && (c.sex || '').toLowerCase() !== filters.sex)
          return false;
        if (filters.from && (!c.regDate || c.regDate < filters.from))
          return false;
        if (filters.to && (!c.regDate || c.regDate > filters.to)) return false;
        return true;
      });
    },
    [allChildren, schoolInfo, filters],
  );
  var screenings = React.useMemo(
    function () {
      return built.screenings.filter(function (s) {
        if (filters.flw && iptStr(s.row.username) !== filters.flw) return false;
        if (filters.from && (!s.date || s.date < filters.from)) return false;
        if (filters.to && (!s.date || s.date > filters.to)) return false;
        return !filters.school && !filters.sex;
      });
    },
    [built, filters],
  );
  var statusById = React.useMemo(
    function () {
      var m = {};
      children.forEach(function (c) {
        m[c.id] = iptChildStatus(c, today, opts);
      });
      return m;
    },
    [children, today],
  );
  var buckets = React.useMemo(
    function () {
      var b = {};
      IPT_BUCKETS.forEach(function (x) {
        b[x.id] = 0;
      });
      children.forEach(function (c) {
        b[statusById[c.id].bucket] += 1;
      });
      return b;
    },
    [children, statusById],
  );
  var cascade = React.useMemo(
    function () {
      return iptCascade(children, today, opts);
    },
    [children, today],
  );
  var daily = React.useMemo(
    function () {
      return iptDailyActivity(children);
    },
    [children],
  );
  var safety = React.useMemo(
    function () {
      return iptSafety(
        children,
        screenings,
        aeRows,
        Object.assign({}, opts, {
          restrictToChildren: !!(
            filters.flw ||
            filters.school ||
            filters.sex ||
            filters.from ||
            filters.to
          ),
        }),
      );
    },
    [children, screenings, aeRows, filters],
  );
  var auth = React.useMemo(
    function () {
      return iptAuthenticity(children, opts, schoolInfo.byChild);
    },
    [children, schoolInfo],
  );
  var workerRows = React.useMemo(
    function () {
      return iptWorkerRows(children, today, opts, nameOf, schoolInfo.byChild);
    },
    [children, today, nameOf, schoolInfo],
  );
  var compliance = React.useMemo(
    function () {
      return iptCompliance(children, screenings);
    },
    [children, screenings],
  );
  // ---- duplicate review: fetch consent photos for the flagged children only
  var oppId = (props.instance && props.instance.opportunity_id) || null;
  var childById = React.useMemo(
    function () {
      var m = {};
      allChildren.forEach(function (c) {
        m[c.id] = c;
      });
      return m;
    },
    [allChildren],
  );
  var dupResult = auth[dupCheck[0]];
  var dupGroups =
    (dupResult && dupResult.extra && dupResult.extra.groupList) || [];
  var dupVisitKey = dupGroups
    .map(function (g) {
      return g.childIds.join(',');
    })
    .join(';');
  React.useEffect(
    function () {
      if (tab !== 'duplicates' || !oppId) return;
      var photos = photoState[0];
      var byVisit = {};
      dupGroups.forEach(function (g) {
        g.childIds.forEach(function (id) {
          var c = childById[id];
          if (c && c.visitId && !photos[c.visitId]) byVisit[c.visitId] = c;
        });
      });
      var want = Object.keys(byVisit);
      if (!want.length) return;
      photoState[1](function (prev) {
        var next = Object.assign({}, prev);
        want.forEach(function (v) {
          next[v] = { status: 'loading' };
        });
        return next;
      });
      // The endpoint takes at most 100 visits per call.
      for (var i = 0; i < want.length; i += 100) {
        (function (batch) {
          fetch(
            '/labs/workflow/api/' +
              oppId +
              '/visit-images/?visit_ids=' +
              batch.join(','),
            {
              credentials: 'same-origin',
            },
          )
            .then(function (r) {
              if (!r.ok) throw new Error('HTTP ' + r.status);
              return r.json();
            })
            .then(function (data) {
              var vi = (data && data.visit_images) || {};
              photoState[1](function (prev) {
                var next = Object.assign({}, prev);
                batch.forEach(function (v) {
                  var blob = iptConsentBlob(vi[v], byVisit[v].consentPhoto);
                  next[v] = blob
                    ? { status: 'ok', blob: blob }
                    : { status: 'none' };
                });
                return next;
              });
            })
            .catch(function () {
              photoState[1](function (prev) {
                var next = Object.assign({}, prev);
                batch.forEach(function (v) {
                  next[v] = { status: 'error' };
                });
                return next;
              });
            });
        })(want.slice(i, i + 100));
      }
    },
    [tab, dupCheck[0], dupVisitKey, oppId],
  );
  var orgSlug = React.useMemo(
    function () {
      return iptOrgSlugFor(oppId, cfg);
    },
    [oppId],
  );

  var highlights = iptHighlights(
    {
      cascade: cascade,
      buckets: buckets,
      safety: safety,
      workers: workerRows,
      auth: auth,
      opts: opts,
    },
    6,
  );

  var schoolOptions = [{ value: '', label: 'All schools' }].concat(
    Object.keys(schoolInfo.schools)
      .map(function (id) {
        return schoolInfo.schools[id];
      })
      .sort(function (a, b) {
        return b.childIds.length - a.childIds.length;
      })
      .map(function (s) {
        return { value: s.id, label: s.label + ' (' + s.childIds.length + ')' };
      }),
  );
  var flwOptions = [{ value: '', label: 'All field workers' }].concat(
    Object.keys(
      allChildren.reduce(function (m, c) {
        c.flwList.forEach(function (u) {
          m[u] = true;
        });
        return m;
      }, {}),
    )
      .map(function (u) {
        return { value: u, label: nameOf(u) };
      })
      .sort(function (a, b) {
        return a.label.localeCompare(b.label);
      }),
  );
  var filtered = !!(
    filters.flw ||
    filters.school ||
    filters.sex ||
    filters.from ||
    filters.to
  );
  var openChild = openChildId
    ? allChildren.filter(function (c) {
        return c.id === openChildId;
      })[0]
    : null;

  function schoolLabelOf(c) {
    var s = schoolInfo.schools[schoolInfo.byChild[c.id]];
    return s ? s.label : iptTitle(c.school) || 'School not recorded';
  }

  // ---------------------------------------------------------------- header
  var dataDates = daily.length
    ? fmtDate(daily[0].date) + ' – ' + fmtDate(daily[daily.length - 1].date)
    : 'no dosing yet';
  var latestMs = null;
  allChildren.forEach(function (c) {
    c.forms.forEach(function (f) {
      if (f.ms !== null && (latestMs === null || f.ms > latestMs))
        latestMs = f.ms;
    });
  });
  var latestText = latestMs
    ? fmtDate(iptLocalDate(latestMs, opts.utcOffsetHours)) +
      ' ' +
      pad2(iptLocalHour(latestMs, opts.utcOffsetHours)) +
      ':' +
      pad2(new Date(latestMs).getUTCMinutes())
    : null;
  var quietDays = latestMs
    ? iptDayDiff(iptLocalDate(latestMs, opts.utcOffsetHours), today)
    : null;
  var header = (
    <R.ReportHeader
      title="IPTsc School Delivery"
      subtitle={
        'Malaria chemoprevention in schools: 3 observed SPAQ doses on 3 consecutive days. Dosing ' +
        dataDates +
        '.'
      }
      badges={[
        <R.Pill
          key="live"
          tone="current"
          title="Recomputed from the latest forms each time the page loads"
        >
          Live · as of {fmtDate(today)}
        </R.Pill>,
        <R.Pill key="n" tone="source">
          {allChildren.length} children · {doseRows.length} dosing forms
        </R.Pill>,
        latestText ? (
          <R.Pill
            key="latest"
            tone={quietDays >= 3 ? 'current' : 'muted'}
            title="When the most recent dosing form was completed (WAT)"
          >
            Latest form {latestText}
            {quietDays >= 3 ? ' · ' + quietDays + ' days ago' : ''}
          </R.Pill>
        ) : null,
      ]}
    />
  );

  var filterBar = (
    <R.Card>
      <div className="flex flex-wrap items-end gap-3">
        <Select
          label="Field worker"
          value={filters.flw}
          options={flwOptions}
          onChange={function (v) {
            setFilter('flw', v);
          }}
          minWidth={190}
        />
        <Select
          label="School"
          value={filters.school}
          options={schoolOptions}
          onChange={function (v) {
            setFilter('school', v);
          }}
          minWidth={190}
        />
        <Select
          label="Sex"
          value={filters.sex}
          options={[
            { value: '', label: 'All' },
            { value: 'female', label: 'Girls' },
            { value: 'male', label: 'Boys' },
          ]}
          onChange={function (v) {
            setFilter('sex', v);
          }}
          minWidth={100}
        />
        <DateInput
          label="Registered from"
          value={filters.from}
          onChange={function (v) {
            setFilter('from', v);
          }}
        />
        <DateInput
          label="Registered to"
          value={filters.to}
          onChange={function (v) {
            setFilter('to', v);
          }}
        />
        {filtered ? (
          <R.Button
            onClick={function () {
              filterState[1]({
                flw: '',
                school: '',
                sex: '',
                from: '',
                to: '',
              });
            }}
          >
            Clear filters
          </R.Button>
        ) : null}
        <div
          className="text-xs text-gray-500 ml-auto"
          style={{ alignSelf: 'center' }}
        >
          {filtered
            ? 'Showing ' +
              children.length +
              ' of ' +
              allChildren.length +
              ' children'
            : 'Showing all ' + allChildren.length + ' children'}
        </div>
      </div>
    </R.Card>
  );

  var problemNotice = problems.length ? (
    <R.Notice tone="warn">
      <div className="font-semibold mb-1">
        Some data could not be loaded, so parts of this page are incomplete:
      </div>
      <ul style={{ listStyle: 'disc', paddingLeft: 18 }}>
        {problems.map(function (p) {
          return (
            <li key={p.label}>
              <span className="font-semibold">{p.label}:</span>{' '}
              {String(p.msg).slice(0, 240)}
            </li>
          );
        })}
      </ul>
    </R.Notice>
  ) : null;

  var empty = !allChildren.length ? (
    <R.Notice tone="info">
      No registrations have reached Connect yet. Once Day 1 forms are submitted
      and synced they will appear here; reload the page to refresh.
    </R.Notice>
  ) : null;

  // ---------------------------------------------------------------- tabs
  var body = null;
  if (tab === 'overview') body = renderOverview();
  else if (tab === 'completion') body = renderCompletion();
  else if (tab === 'reach') body = renderReach();
  else if (tab === 'safety') body = renderSafety();
  else if (tab === 'protocol') body = renderProtocol();
  else if (tab === 'authenticity') body = renderAuthenticity();
  else if (tab === 'workers') body = renderWorkers();
  else if (tab === 'duplicates') body = renderDuplicates();
  else body = renderDefinitions();

  return (
    <div className="space-y-4" style={{ maxWidth: 1280, margin: '0 auto' }}>
      {header}
      {problemNotice}
      {filterBar}
      <div style={{ overflowX: 'auto' }}>
        <R.Tabs tabs={IPT_TABS} active={tab} onChange={setTab} />
      </div>
      {empty}
      {body}
      {openChild ? (
        <ChildDrawer
          child={openChild}
          status={
            statusById[openChild.id] || iptChildStatus(openChild, today, opts)
          }
          today={today}
          opts={opts}
          nameOf={nameOf}
          schoolLabel={schoolLabelOf(openChild)}
          aes={safety.aes.filter(function (a) {
            return a.child && a.child.id === openChild.id;
          })}
          onClose={function () {
            setOpenChildId(null);
          }}
        />
      ) : null}
    </div>
  );

  // ======================================================== tab renderers
  // Plain functions returning elements (not components), so nothing remounts.

  function renderOverview() {
    var full = cascade.complete;
    var tiles = [
      {
        id: 'reg',
        label: 'Registered',
        value: fmtNum(children.length),
        sub:
          Object.keys(schoolInfo.schools).length +
          ' school(s) · ' +
          workerRows.length +
          ' field worker(s)',
      },
      {
        id: 'd1',
        label: 'Dose 1 given',
        value: fmtPct(iptPct(cascade.d1.got, cascade.d1.due)),
        sub: cascade.d1.got + ' of ' + cascade.d1.due + ' registered',
        tone:
          cascade.d1.due >= opts.minChildren
            ? iptPct(cascade.d1.got, cascade.d1.due) >= 95
              ? 'good'
              : iptPct(cascade.d1.got, cascade.d1.due) >= 90
                ? 'watch'
                : 'bad'
            : undefined,
      },
      {
        id: 'full',
        label: 'Full course (3/3)',
        value: full.due ? fmtPct(iptPct(full.got, full.due)) : '–',
        sub: full.due
          ? full.got + ' of ' + full.due + ' due'
          : 'none due to finish yet',
        tone:
          full.due >= opts.minChildren
            ? iptPct(full.got, full.due) >= 90
              ? 'good'
              : iptPct(full.got, full.due) >= 80
                ? 'watch'
                : 'bad'
            : undefined,
      },
      {
        id: 'track',
        label: 'On track',
        value: fmtNum(buckets.on_track),
        sub: 'next dose due today or later',
      },
      {
        id: 'missing',
        label: 'Missing a dose',
        value: fmtNum(buckets.overdue + buckets.missed + buckets.no_dose1),
        sub:
          [
            buckets.overdue ? buckets.overdue + ' overdue' : '',
            buckets.missed ? buckets.missed + ' missed' : '',
            buckets.no_dose1 ? buckets.no_dose1 + ' no dose 1' : '',
          ]
            .filter(Boolean)
            .join(' · ') || 'nobody',
        tone:
          buckets.missed + buckets.no_dose1 > 0
            ? 'bad'
            : buckets.overdue > 0
              ? 'watch'
              : 'good',
      },
      {
        id: 'ae',
        label: 'Adverse events',
        value: fmtNum(safety.aes.length),
        sub: safety.moderatePlus + ' moderate or worse',
        tone:
          safety.bySeverity.danger || safety.bySeverity.severe
            ? 'bad'
            : safety.moderatePlus
              ? 'watch'
              : undefined,
      },
    ].map(function (t) {
      var toneLabel =
        t.tone === 'good'
          ? 'On target'
          : t.tone === 'watch'
            ? 'Watch'
            : t.tone === 'bad'
              ? 'Act'
              : undefined;
      return {
        id: t.id,
        label: t.label,
        value: t.value,
        sub: t.sub,
        tone: t.tone,
        toneLabel: toneLabel,
      };
    });
    var funnel = [
      {
        label: 'Registered',
        got: cascade.registered,
        due: cascade.registered,
        color: '#94a3b8',
      },
      {
        label: 'Dose 1',
        got: cascade.d1.got,
        due: cascade.d1.due,
        color: DOSE_COLORS.d1,
      },
      {
        label: 'Dose 2',
        got: cascade.d2.got,
        due: cascade.d2.due,
        color: DOSE_COLORS.d2,
      },
      {
        label: 'Dose 3',
        got: cascade.d3.got,
        due: cascade.d3.due,
        color: DOSE_COLORS.d3,
      },
    ];
    var cumulative = [];
    var runD1 = 0;
    var runFull = 0;
    var completeByDate = {};
    children.forEach(function (c) {
      if (c.doseDates[3] && c.successes >= 3)
        completeByDate[c.doseDates[3]] =
          (completeByDate[c.doseDates[3]] || 0) + 1;
    });
    daily.forEach(function (d) {
      runD1 += d.d1;
      runFull += completeByDate[d.date] || 0;
      cumulative.push({ date: d.date, d1: runD1, full: runFull });
    });
    return (
      <div className="space-y-4">
        <R.StatTiles tiles={tiles} columns={3} />
        <div
          className="grid gap-4"
          style={{
            gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))',
          }}
        >
          <R.Card>
            <R.SectionTitle
              right={<InfoLink id="cascade" onOpen={openDef} />}
              sub="Each step counts only children who could already have had that dose."
            >
              Dose 1 → 2 → 3
            </R.SectionTitle>
            <div className="space-y-2 mt-2">
              {funnel.map(function (f, i) {
                var pct = iptPct(f.got, f.due);
                return (
                  <div key={f.label}>
                    <div className="flex justify-between text-sm">
                      <span className="font-semibold text-gray-800">
                        {f.label}
                      </span>
                      <span className="tabular-nums text-gray-700">
                        {i === 0
                          ? f.got
                          : f.due
                            ? f.got + ' of ' + f.due + ' due · ' + fmtPct(pct)
                            : f.got + ' · none due yet'}
                      </span>
                    </div>
                    <div
                      style={{
                        background: '#f3f4f6',
                        borderRadius: 6,
                        height: 12,
                        marginTop: 3,
                      }}
                    >
                      <div
                        style={{
                          width: (i === 0 ? 100 : pct || 0) + '%',
                          background: f.color,
                          height: 12,
                          borderRadius: 6,
                        }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              {IPT_BUCKETS.map(function (b) {
                return buckets[b.id] ? (
                  <span
                    key={b.id}
                    title={b.hint}
                    className="text-xs"
                    style={{
                      border: '1px solid #e5e7eb',
                      borderRadius: 9999,
                      padding: '2px 8px',
                    }}
                  >
                    <span
                      style={{
                        display: 'inline-block',
                        width: 8,
                        height: 8,
                        borderRadius: 9999,
                        background: b.color,
                        marginRight: 5,
                      }}
                    />
                    {b.label} <b>{buckets[b.id]}</b>
                  </span>
                ) : null;
              })}
            </div>
          </R.Card>
          <R.Card>
            <R.SectionTitle sub="Generated from the rules on the other tabs. Click one to go there.">
              What stands out
            </R.SectionTitle>
            {highlights.length ? (
              <ul className="space-y-2 mt-1">
                {highlights.map(function (h, i) {
                  var t = IPT_TONE[h.tone];
                  return (
                    <li key={i}>
                      <button
                        type="button"
                        onClick={function () {
                          setTab(h.tab);
                        }}
                        className="w-full text-left text-sm rounded-lg px-3 py-2"
                        style={{
                          background: t.bg,
                          color: t.fg,
                          border: '1px solid ' + t.bd,
                          cursor: 'pointer',
                        }}
                      >
                        {h.text}
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <div className="text-sm text-gray-500 mt-1">
                Nothing needs attention right now.
              </div>
            )}
          </R.Card>
        </div>
        <R.Card>
          <R.SectionTitle sub="Successful doses per day by dose number, plus failed attempts. A healthy week shows dose 1 early in the week and doses 2 and 3 on the next two days. Weekend days are labelled in red.">
            Daily dosing
          </R.SectionTitle>
          <DayColumns
            days={daily.map(function (d) {
              return {
                date: d.date,
                parts: [
                  { n: d.d1, color: DOSE_COLORS.d1, label: 'Dose 1' },
                  { n: d.d2, color: DOSE_COLORS.d2, label: 'Dose 2' },
                  { n: d.d3, color: DOSE_COLORS.d3, label: 'Dose 3' },
                  {
                    n: d.failed,
                    color: DOSE_COLORS.failed,
                    label: 'Failed attempts',
                  },
                ],
              };
            })}
            legend={[
              { label: 'Dose 1', color: DOSE_COLORS.d1 },
              { label: 'Dose 2', color: DOSE_COLORS.d2 },
              { label: 'Dose 3', color: DOSE_COLORS.d3 },
              { label: 'Failed attempt', color: DOSE_COLORS.failed },
            ]}
          />
        </R.Card>
        <R.Card>
          <R.SectionTitle sub="Cumulative children who received dose 1, and who completed all 3 doses.">
            Progress over time
          </R.SectionTitle>
          <CumulativeLine
            label="Cumulative dose 1 and completed courses"
            days={cumulative}
            series={[
              { key: 'd1', label: 'Received dose 1', color: DOSE_COLORS.d1 },
              {
                key: 'full',
                label: 'Completed 3 doses',
                color: DOSE_COLORS.d3,
              },
            ]}
          />
        </R.Card>
      </div>
    );
  }

  function renderCompletion() {
    var cohorts = iptCohorts(children, today, opts, cohortBy[0]);
    var intervals = iptIntervals(children);
    var reasons = iptDropReasons(children, today, opts);
    var attempts = iptAttemptStats(children);
    var gf = gridFilter[0];
    var gridRows = children
      .map(function (c) {
        return { c: c, st: statusById[c.id] };
      })
      .filter(function (x) {
        if (gf === 'attention')
          return ['overdue', 'missed', 'no_dose1'].indexOf(x.st.bucket) >= 0;
        if (gf === 'all') return true;
        return x.st.bucket === gf;
      });
    var rank = {
      missed: 0,
      no_dose1: 1,
      overdue: 2,
      stopped: 3,
      referred: 4,
      on_track: 5,
      completed: 6,
    };
    gridRows.sort(function (a, b) {
      var sa = schoolLabelOf(a.c);
      var sb = schoolLabelOf(b.c);
      if (sa !== sb) return sa.localeCompare(sb);
      return (
        rank[a.st.bucket] - rank[b.st.bucket] ||
        b.st.overdueDays - a.st.overdueDays ||
        (a.c.name || '').localeCompare(b.c.name || '')
      );
    });
    var bySchool = [];
    gridRows.forEach(function (x) {
      var s = schoolLabelOf(x.c);
      if (!bySchool.length || bySchool[bySchool.length - 1].school !== s)
        bySchool.push({ school: s, rows: [] });
      bySchool[bySchool.length - 1].rows.push(x);
    });
    function exportGrid() {
      var rows = gridRows.map(function (x) {
        var c = x.c;
        return [
          schoolLabelOf(c),
          c.name,
          nameOf(c.username),
          c.regDate,
          iptBucketMeta(x.st.bucket).label,
          c.doseDates[1] || '',
          c.doseDates[2] || '',
          c.doseDates[3] || '',
          x.st.nextDose ? 'Dose ' + x.st.nextDose : '',
          x.st.nextDue || '',
          x.st.overdueDays || '',
        ];
      });
      downloadCsv(
        'iptsc_children_' + gf + '_' + today + '.csv',
        iptCsv(
          [
            'School',
            'Child',
            'Field worker',
            'Registered',
            'Status',
            'Dose 1',
            'Dose 2',
            'Dose 3',
            'Next dose',
            'Next due',
            'Days overdue',
          ],
          rows,
        ),
      );
    }
    var gridOptions = [
      {
        value: 'attention',
        label:
          'Missing a dose (' +
          (buckets.overdue + buckets.missed + buckets.no_dose1) +
          ')',
      },
      { value: 'all', label: 'All children (' + children.length + ')' },
    ].concat(
      IPT_BUCKETS.map(function (b) {
        return { value: b.id, label: b.label + ' (' + buckets[b.id] + ')' };
      }),
    );
    function ivRows(obj) {
      return ['same day', '1', '2', '3', '4+'].map(function (k) {
        return {
          label:
            k === '1'
              ? '1 day (protocol)'
              : k === 'same day'
                ? 'Same day'
                : k + ' days',
          n: obj[k],
          color: k === '1' ? '#16a34a' : '#f59e0b',
        };
      });
    }
    return (
      <div className="space-y-4">
        <div
          className="grid gap-2"
          style={{
            gridTemplateColumns: 'repeat(auto-fit, minmax(118px, 1fr))',
          }}
        >
          {IPT_BUCKETS.map(function (b) {
            var on = gf === b.id;
            return (
              <button
                key={b.id}
                type="button"
                onClick={function () {
                  gridFilter[1](on ? 'all' : b.id);
                }}
                title={b.hint + ' (click to list them)'}
                className="text-left rounded-xl px-3 py-2"
                style={{
                  background: '#fff',
                  border: on ? '2px solid ' + b.color : '1px solid #e5e7eb',
                  cursor: 'pointer',
                }}
              >
                <div
                  className="text-xs font-semibold text-gray-500"
                  style={{
                    textTransform: 'uppercase',
                    letterSpacing: '0.04em',
                  }}
                >
                  <span
                    style={{
                      display: 'inline-block',
                      width: 8,
                      height: 8,
                      borderRadius: 9999,
                      background: b.color,
                      marginRight: 5,
                    }}
                  />
                  {b.label}
                </div>
                <div className="text-2xl font-bold text-gray-900 tabular-nums">
                  {buckets[b.id]}
                </div>
                <div className="text-xs text-gray-500">
                  {fmtPct(iptPct(buckets[b.id], children.length))} of children
                </div>
              </button>
            );
          })}
        </div>

        <R.Card padded={false}>
          <div className="px-4 pt-3">
            <R.SectionTitle
              right={
                <span className="inline-flex items-center gap-2">
                  <InfoLink id="dose_grid" onOpen={openDef} />
                  <R.Button onClick={exportGrid} disabled={!gridRows.length}>
                    Download CSV
                  </R.Button>
                </span>
              }
              sub="One row per child, grouped by school so a supervisor gets a list of who to follow up. Click a row for the child's timeline."
            >
              Who got which dose
            </R.SectionTitle>
            <div className="flex flex-wrap items-end gap-3 mb-2">
              <Select
                label="Show"
                value={gf}
                options={gridOptions}
                onChange={gridFilter[1]}
                minWidth={220}
              />
              <Legend
                items={[
                  'given',
                  'late',
                  'failed',
                  'missed',
                  'pending',
                  'ended',
                ].map(function (k) {
                  return {
                    label: DOSE_CELL[k].label,
                    color: DOSE_CELL[k].fill ? DOSE_CELL[k].color : '#fff',
                    border: '2px solid ' + DOSE_CELL[k].color,
                    round: true,
                  };
                })}
              />
            </div>
          </div>
          {gridRows.length ? (
            <div
              style={{ overflowX: 'auto', maxHeight: 560, overflowY: 'auto' }}
            >
              <table className="min-w-full text-sm">
                <thead
                  className="bg-gray-50"
                  style={{ position: 'sticky', top: 0, zIndex: 1 }}
                >
                  <tr>
                    <th className="px-3 py-2 text-left text-xs font-semibold text-gray-600">
                      Child
                    </th>
                    <th className="px-3 py-2 text-left text-xs font-semibold text-gray-600">
                      Field worker
                    </th>
                    <th className="px-3 py-2 text-left text-xs font-semibold text-gray-600">
                      Registered
                    </th>
                    <th className="px-3 py-2 text-center text-xs font-semibold text-gray-600">
                      Dose 1
                    </th>
                    <th className="px-3 py-2 text-center text-xs font-semibold text-gray-600">
                      Dose 2
                    </th>
                    <th className="px-3 py-2 text-center text-xs font-semibold text-gray-600">
                      Dose 3
                    </th>
                    <th className="px-3 py-2 text-left text-xs font-semibold text-gray-600">
                      Status
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {bySchool.map(function (g) {
                    return [
                      <tr key={'s-' + g.school} className="bg-gray-50">
                        <td
                          colSpan={7}
                          className="px-3 py-1.5 text-xs font-semibold text-gray-700"
                        >
                          {g.school} · {g.rows.length} child
                          {g.rows.length === 1 ? '' : 'ren'}
                        </td>
                      </tr>,
                    ].concat(
                      g.rows.map(function (x) {
                        var c = x.c;
                        var meta = iptBucketMeta(x.st.bucket);
                        return (
                          <tr
                            key={c.id}
                            onClick={function () {
                              setOpenChildId(c.id);
                            }}
                            className="hover:bg-indigo-50 cursor-pointer"
                            style={{ borderTop: '1px solid #f3f4f6' }}
                          >
                            <td className="px-3 py-1.5 text-gray-900">
                              {c.name || (
                                <span className="text-gray-400">
                                  Name not recorded
                                </span>
                              )}
                            </td>
                            <td className="px-3 py-1.5 text-gray-600">
                              {nameOf(c.username)}
                            </td>
                            <td className="px-3 py-1.5 text-gray-600 whitespace-nowrap">
                              {fmtDate(c.regDate)}
                            </td>
                            {[1, 2, 3].map(function (n) {
                              return (
                                <td key={n} className="px-3 py-1.5 text-center">
                                  <DoseDot
                                    cell={iptDoseCell(c, n, x.st, today)}
                                    showLate={true}
                                  />
                                </td>
                              );
                            })}
                            <td className="px-3 py-1.5 whitespace-nowrap">
                              <span
                                style={{ color: meta.color, fontWeight: 600 }}
                              >
                                {meta.label}
                              </span>
                              {x.st.overdueDays ? (
                                <span className="text-xs text-gray-500">
                                  {' '}
                                  · {x.st.overdueDays}d overdue
                                </span>
                              ) : null}
                              {x.st.bucket === 'on_track' && x.st.nextDose ? (
                                <span className="text-xs text-gray-500">
                                  {' '}
                                  · dose {x.st.nextDose}{' '}
                                  {x.st.nextDue === today
                                    ? 'today'
                                    : fmtDate(x.st.nextDue)}
                                </span>
                              ) : null}
                            </td>
                          </tr>
                        );
                      }),
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="px-4 pb-4 text-sm text-gray-500">
              {gf === 'attention'
                ? 'No child is missing a dose right now.'
                : 'No children in this group.'}
            </div>
          )}
        </R.Card>

        <R.Card padded={false}>
          <div className="px-4 pt-3">
            <R.SectionTitle
              right={
                <span className="inline-flex items-center gap-2">
                  <InfoLink id="full_course" onOpen={openDef} />
                  <Select
                    label=""
                    value={cohortBy[0]}
                    options={[
                      { value: 'day', label: 'By registration day' },
                      { value: 'week', label: 'By registration week' },
                    ]}
                    onChange={cohortBy[1]}
                    minWidth={170}
                  />
                </span>
              }
              sub="How each group of registrations has progressed. Groups whose courses could still be running are marked “in progress” and are not graded."
            >
              Completion by registration cohort
            </R.SectionTitle>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  {[
                    'Cohort',
                    'Registered',
                    'Dose 1',
                    'Dose 2',
                    'on time / late',
                    'Dose 3',
                    'on time / late',
                    'Full course',
                    'Still open',
                  ].map(function (h, i) {
                    return (
                      <th
                        key={i}
                        className="px-3 py-2 text-xs font-semibold text-gray-600"
                        style={{
                          textAlign: i === 0 ? 'left' : 'right',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {h}
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {cohorts.map(function (k) {
                  var cas = k.cascade;
                  return (
                    <tr key={k.key} style={{ borderTop: '1px solid #f3f4f6' }}>
                      <td className="px-3 py-1.5 whitespace-nowrap text-gray-800">
                        {cohortBy[0] === 'week'
                          ? 'Week of ' + fmtDate(k.key)
                          : fmtDate(k.key)}
                        {!k.mature ? (
                          <span className="ml-2">
                            <Badge tone="info">in progress</Badge>
                          </span>
                        ) : null}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {cas.registered}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        <Rate
                          num={cas.d1.got}
                          den={cas.d1.due}
                          band={
                            k.mature
                              ? bandHighGood(
                                  iptPct(cas.d1.got, cas.d1.due),
                                  95,
                                  90,
                                )
                              : null
                          }
                        />
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        <Rate
                          num={cas.d2.got}
                          den={cas.d2.due}
                          band={
                            k.mature
                              ? bandHighGood(
                                  iptPct(cas.d2.got, cas.d2.due),
                                  95,
                                  90,
                                )
                              : null
                          }
                        />
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums text-gray-600">
                        {k.onTime2} / {k.late2}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        <Rate
                          num={cas.d3.got}
                          den={cas.d3.due}
                          band={
                            k.mature
                              ? bandHighGood(
                                  iptPct(cas.d3.got, cas.d3.due),
                                  95,
                                  90,
                                )
                              : null
                          }
                        />
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums text-gray-600">
                        {k.onTime3} / {k.late3}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        <Rate
                          num={cas.complete.got}
                          den={cas.complete.due}
                          band={
                            k.mature
                              ? bandHighGood(
                                  iptPct(cas.complete.got, cas.complete.due),
                                  90,
                                  80,
                                )
                              : null
                          }
                        />
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {k.open}
                      </td>
                    </tr>
                  );
                })}
                {!cohorts.length ? (
                  <tr>
                    <td colSpan={9} className="px-3 py-3 text-sm text-gray-500">
                      No registrations yet.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </R.Card>

        <div
          className="grid gap-4"
          style={{
            gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))',
          }}
        >
          <R.Card>
            <R.SectionTitle
              right={<InfoLink id="intervals" onOpen={openDef} />}
              sub="The protocol is exactly one day between doses."
            >
              Days between doses
            </R.SectionTitle>
            <div className="text-xs font-semibold text-gray-500 mt-2 mb-1">
              Dose 1 → dose 2
            </div>
            <HBars
              items={ivRows(intervals.d1d2)}
              labelWidth={140}
              empty="No dose 2 yet."
            />
            <div className="text-xs font-semibold text-gray-500 mt-3 mb-1">
              Dose 2 → dose 3
            </div>
            <HBars
              items={ivRows(intervals.d2d3)}
              labelWidth={140}
              empty="No dose 3 yet."
            />
            <div className="text-sm text-gray-700 mt-3">
              Courses on three consecutive days:{' '}
              <Rate
                num={cascade.consecutive.got}
                den={cascade.consecutive.due}
                band={bandHighGood(
                  iptPct(cascade.consecutive.got, cascade.consecutive.due),
                  85,
                  70,
                )}
              />
              <InfoLink id="consecutive" onOpen={openDef} />
            </div>
          </R.Card>
          <R.Card>
            <R.SectionTitle sub="For children not completed or on track: the last thing their forms recorded.">
              Why children fall out
            </R.SectionTitle>
            <HBars
              items={reasons.map(function (r) {
                return { label: r.reason, n: r.n, color: '#f97316' };
              })}
              labelWidth={250}
              empty="No child has fallen out of the course."
            />
          </R.Card>
        </div>

        <R.Card padded={false}>
          <div className="px-4 pt-3">
            <R.SectionTitle
              right={<InfoLink id="attempts" onOpen={openDef} />}
              sub="Every dosing form, by the dose it was trying to give."
            >
              Attempts and outcomes
            </R.SectionTitle>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  {[
                    'Dose',
                    'Forms',
                    'Observed (DOT)',
                    'Swallowed first time',
                    'Vomited',
                    'Re-dosed OK',
                    'Refused / could not',
                    'Not given',
                  ].map(function (h, i) {
                    return (
                      <th
                        key={h}
                        className="px-3 py-2 text-xs font-semibold text-gray-600"
                        style={{ textAlign: i === 0 ? 'left' : 'right' }}
                      >
                        {h}
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {[1, 2, 3].map(function (n) {
                  var a = attempts[n];
                  return (
                    <tr key={n} style={{ borderTop: '1px solid #f3f4f6' }}>
                      <td className="px-3 py-1.5 font-semibold text-gray-800">
                        Dose {n}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {a.forms}
                      </td>
                      <td className="px-3 py-1.5 text-right">
                        <Rate
                          num={a.dot}
                          den={a.forms}
                          band={
                            a.forms >= 5
                              ? bandHighGood(iptPct(a.dot, a.forms), 98, 95)
                              : null
                          }
                        />
                      </td>
                      <td className="px-3 py-1.5 text-right">
                        <Rate num={a.firstSwallow} den={a.forms} />
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {a.vomited}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {a.redoseOk}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {a.refused}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {a.failed}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </R.Card>
      </div>
    );
  }

  function renderReach() {
    var schools = Object.keys(schoolInfo.schools)
      .map(function (id) {
        var s = schoolInfo.schools[id];
        var list = children.filter(function (c) {
          return schoolInfo.byChild[c.id] === id;
        });
        if (!list.length) return null;
        var cas = iptCascade(list, today, opts);
        var missing = list.filter(function (c) {
          var b = statusById[c.id].bucket;
          return b === 'overdue' || b === 'missed' || b === 'no_dose1';
        }).length;
        var flws = {};
        var dates = {};
        list.forEach(function (c) {
          if (c.username) flws[c.username] = true;
          if (c.regDate) dates[c.regDate] = (dates[c.regDate] || 0) + 1;
        });
        var dayKeys = Object.keys(dates).sort();
        var cumul = [];
        var run = 0;
        dayKeys.forEach(function (d) {
          run += dates[d];
          cumul.push({ date: d, n: run, add: dates[d] });
        });
        return {
          s: s,
          list: list,
          cas: cas,
          missing: missing,
          flws: Object.keys(flws).length,
          first: dayKeys[0],
          last: dayKeys[dayKeys.length - 1],
          days: dayKeys.length,
          cumul: cumul,
        };
      })
      .filter(Boolean)
      .sort(function (a, b) {
        return b.list.length - a.list.length;
      });
    function split(field, labels) {
      var counts = {};
      children.forEach(function (c) {
        var v = field(c);
        counts[v] = (counts[v] || 0) + 1;
      });
      return labels
        .map(function (l) {
          return { label: l.label, n: counts[l.key] || 0, color: l.color };
        })
        .filter(function (x) {
          return x.n;
        });
    }
    var sexBars = split(
      function (c) {
        return (c.sex || '').toLowerCase() || 'unknown';
      },
      [
        { key: 'female', label: 'Girls', color: '#db2777' },
        { key: 'male', label: 'Boys', color: '#2563eb' },
        { key: 'unknown', label: 'Not recorded', color: '#9ca3af' },
      ],
    );
    var ageBars = split(
      function (c) {
        return c.age === null || c.age === undefined
          ? 'unknown'
          : c.age <= 6
            ? '5-6'
            : c.age <= 8
              ? '7-8'
              : c.age <= 10
                ? '9-10'
                : c.age <= 12
                  ? '11-12'
                  : '13+';
      },
      [
        { key: '5-6', label: '5-6 years' },
        { key: '7-8', label: '7-8 years' },
        { key: '9-10', label: '9-10 years' },
        { key: '11-12', label: '11-12 years' },
        { key: '13+', label: '13+ years' },
        { key: 'unknown', label: 'Not recorded', color: '#9ca3af' },
      ],
    );
    var bandBars = split(
      function (c) {
        return c.band || 'unknown';
      },
      [
        { key: 'band_10_20', label: '10 to <20 kg (review)', color: '#f59e0b' },
        { key: 'band_20_40', label: '20-40 kg (standard)', color: '#16a34a' },
        { key: 'review', label: 'Over 40 kg (approval)', color: '#f59e0b' },
        {
          key: 'hard_stop',
          label: 'Under 10 kg (do not dose)',
          color: '#dc2626',
        },
        { key: 'unknown', label: 'Not recorded', color: '#9ca3af' },
      ],
    );
    var girls = children.filter(function (c) {
      return (c.sex || '').toLowerCase() === 'female';
    });
    var boys = children.filter(function (c) {
      return (c.sex || '').toLowerCase() === 'male';
    });
    var cg = iptCascade(girls, today, opts);
    var cb = iptCascade(boys, today, opts);
    var girlsPct = iptPct(girls.length, girls.length + boys.length);
    var geo = {};
    children.forEach(function (c) {
      var k =
        (iptTitle(c.state) || '–') +
        ' › ' +
        (iptTitle(c.district) || '–') +
        ' › ' +
        (iptTitle(c.ward) || '–');
      geo[k] = (geo[k] || 0) + 1;
    });
    var regByWeekday = [0, 0, 0, 0, 0, 0, 0];
    children.forEach(function (c) {
      if (c.regDate) regByWeekday[iptWeekday(c.regDate)] += 1;
    });
    return (
      <div className="space-y-4">
        <R.Notice tone="muted">
          There is no list of target schools or enrolment, so reach here means
          children registered, not a share of the eligible children. A school
          whose registrations have levelled off is probably covered.
          <InfoLink id="saturation" onOpen={openDef} />
        </R.Notice>
        <R.Card padded={false}>
          <div className="px-4 pt-3">
            <R.SectionTitle
              right={<InfoLink id="schools" onOpen={openDef} />}
              sub="Schools are grouped from the typed names and Day 1 GPS. Each spelling seen is listed under the name."
            >
              Schools
            </R.SectionTitle>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  {[
                    'School',
                    'Children',
                    'Full course',
                    'Missing a dose',
                    'Workers',
                    'Active days',
                    'First / last registration',
                    'Registrations so far',
                  ].map(function (h, i) {
                    return (
                      <th
                        key={h}
                        className="px-3 py-2 text-xs font-semibold text-gray-600"
                        style={{
                          textAlign: i === 0 || i === 7 ? 'left' : 'right',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {h}
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {schools.map(function (x) {
                  var sinceLast = x.last ? iptDayDiff(x.last, today) : null;
                  return (
                    <tr
                      key={x.s.id}
                      style={{
                        borderTop: '1px solid #f3f4f6',
                        verticalAlign: 'top',
                      }}
                    >
                      <td className="px-3 py-2">
                        <button
                          type="button"
                          className="font-semibold text-indigo-700 hover:underline text-left"
                          onClick={function () {
                            setFilter('school', x.s.id);
                            setTab('completion');
                          }}
                          title="Show this school's children"
                        >
                          {x.s.label}
                        </button>
                        {x.s.variants.length > 1 ? (
                          <div
                            className="text-xs text-gray-400"
                            style={{ maxWidth: 280 }}
                          >
                            Typed as:{' '}
                            {x.s.variants
                              .map(function (v) {
                                return '“' + v.name + '” ' + v.n;
                              })
                              .join(', ')}
                          </div>
                        ) : null}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {x.list.length}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <Rate
                          num={x.cas.complete.got}
                          den={x.cas.complete.due}
                          floor={opts.minChildren}
                          band={bandHighGood(
                            iptPct(x.cas.complete.got, x.cas.complete.due),
                            90,
                            80,
                          )}
                        />
                      </td>
                      <td
                        className="px-3 py-2 text-right tabular-nums"
                        style={{
                          color: x.missing ? '#b91c1c' : undefined,
                          fontWeight: x.missing ? 700 : 400,
                        }}
                      >
                        {x.missing}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {x.flws}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {x.days}
                      </td>
                      <td className="px-3 py-2 text-right whitespace-nowrap text-gray-600">
                        {fmtDate(x.first)} / {fmtDate(x.last)}
                      </td>
                      <td className="px-3 py-2">
                        <SaturationSpark
                          points={x.cumul}
                          first={x.first}
                          today={today}
                        />
                        <div className="text-xs text-gray-500">
                          {sinceLast === 0
                            ? 'new registrations today'
                            : sinceLast !== null
                              ? 'last new ' + sinceLast + ' day(s) ago'
                              : ''}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </R.Card>
        <div
          className="grid gap-4"
          style={{
            gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))',
          }}
        >
          <R.Card>
            <R.SectionTitle
              sub={girlsPct !== null ? Math.round(girlsPct) + '% girls' : ''}
            >
              Sex
            </R.SectionTitle>
            <HBars items={sexBars} total={children.length} labelWidth={110} />
            <div className="text-xs text-gray-600 mt-3 space-y-1">
              <div>
                Full course, girls:{' '}
                <Rate
                  num={cg.complete.got}
                  den={cg.complete.due}
                  floor={opts.minChildren}
                />
              </div>
              <div>
                Full course, boys:{' '}
                <Rate
                  num={cb.complete.got}
                  den={cb.complete.due}
                  floor={opts.minChildren}
                />
              </div>
            </div>
          </R.Card>
          <R.Card>
            <R.SectionTitle sub="IPTsc covers ages 5-15; over 10 needs protocol confirmation.">
              Age
            </R.SectionTitle>
            <HBars items={ageBars} total={children.length} labelWidth={110} />
          </R.Card>
          <R.Card>
            <R.SectionTitle sub="The dose is set by weight band.">
              Weight band
            </R.SectionTitle>
            <HBars items={bandBars} total={children.length} labelWidth={170} />
          </R.Card>
        </div>
        <div
          className="grid gap-4"
          style={{
            gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))',
          }}
        >
          <R.Card>
            <R.SectionTitle sub="As typed on the Day 1 form (state › district › ward/village).">
              Where
            </R.SectionTitle>
            <HBars
              items={Object.keys(geo)
                .map(function (k) {
                  return { label: k, n: geo[k], color: '#0ea5e9' };
                })
                .sort(function (a, b) {
                  return b.n - a.n;
                })}
              total={children.length}
              labelWidth={230}
            />
          </R.Card>
          <R.Card>
            <R.SectionTitle sub="New registrations are allowed Monday to Wednesday; other days need a supervisor override.">
              Registrations by weekday
            </R.SectionTitle>
            <HBars
              items={[1, 2, 3, 4, 5, 6, 0].map(function (d) {
                return {
                  label: IPT_WEEKDAYS[d],
                  n: regByWeekday[d],
                  color: d >= 1 && d <= 3 ? '#16a34a' : '#f59e0b',
                };
              })}
              total={children.length}
              labelWidth={60}
            />
          </R.Card>
        </div>
      </div>
    );
  }

  function renderSafety() {
    var s = safety;
    var aeProblem = pipelineProblem(pipelines.ae_log);
    var obsItems = [
      { label: 'No reaction', n: s.obsCounts.no, color: '#16a34a' },
      { label: 'Reaction reported', n: s.obsCounts.yes, color: '#dc2626' },
      {
        label: 'Still under observation',
        n: s.obsCounts.pending,
        color: '#f59e0b',
      },
      {
        label: 'Left before 30 minutes',
        n: s.obsCounts.left_early,
        color: '#f97316',
      },
    ];
    var observerLabels = {
      school: 'School member',
      flw: 'Field worker',
      supervisor: 'Supervisor',
      other: 'Other',
    };
    return (
      <div className="space-y-4">
        {aeProblem ? (
          <R.Notice tone="warn">
            The Adverse Event Log could not be read from CommCare HQ, so adverse
            events below are incomplete: {String(aeProblem).slice(0, 200)}
          </R.Notice>
        ) : null}
        <R.StatTiles
          columns={4}
          tiles={[
            {
              id: 'aes',
              label: 'Adverse event logs',
              value: fmtNum(s.aes.length),
              sub:
                s.bySeverity.mild +
                ' mild · ' +
                s.bySeverity.moderate +
                ' moderate',
            },
            {
              id: 'serious',
              label: 'Severe or danger sign',
              value: fmtNum(s.bySeverity.severe + s.bySeverity.danger),
              tone: s.bySeverity.severe + s.bySeverity.danger ? 'bad' : 'good',
              toneLabel:
                s.bySeverity.severe + s.bySeverity.danger ? 'Act' : 'None',
            },
            {
              id: 'rate',
              label: 'Moderate+ per 1,000 doses',
              value: s.ratePer1000 === null ? '–' : fmtNum(s.ratePer1000, 1),
              sub: s.dosesGiven + ' doses given',
            },
            {
              id: 'unlogged',
              label: 'Reactions with no log',
              value: fmtNum(s.unlogged.length),
              sub: 'reported in observation, no AE form',
              tone: s.unlogged.length ? 'watch' : undefined,
              toneLabel: s.unlogged.length ? 'Follow up' : undefined,
            },
          ]}
        />
        <R.Card padded={false}>
          <div className="px-4 pt-3">
            <R.SectionTitle
              right={<InfoLink id="ae_rate" onOpen={openDef} />}
              sub="Moderate or worse, and every referral. Open items (status worsening, same or unknown, with follow-up required) are listed first."
            >
              Adverse events to follow up
            </R.SectionTitle>
          </div>
          {s.lineList.length ? (
            <div style={{ overflowX: 'auto' }}>
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    {[
                      '',
                      'Date',
                      'Child',
                      'School',
                      'Dose',
                      'Severity',
                      'Symptoms',
                      'Action taken',
                      'Child now',
                      'Course',
                    ].map(function (h) {
                      return (
                        <th
                          key={h}
                          className="px-3 py-2 text-left text-xs font-semibold text-gray-600"
                        >
                          {h}
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody>
                  {s.lineList.map(function (a, i) {
                    var sevTone =
                      a.severity === 'danger' || a.severity === 'severe'
                        ? 'red'
                        : 'yellow';
                    return (
                      <tr
                        key={i}
                        style={{
                          borderTop: '1px solid #f3f4f6',
                          cursor: a.child ? 'pointer' : 'default',
                        }}
                        className={a.child ? 'hover:bg-indigo-50' : ''}
                        onClick={function () {
                          if (a.child) setOpenChildId(a.child.id);
                        }}
                      >
                        <td className="px-3 py-1.5">
                          {a.isOpen ? (
                            <Badge
                              tone="red"
                              title="Not resolved or improving, and follow-up was required"
                            >
                              Open
                            </Badge>
                          ) : null}
                        </td>
                        <td className="px-3 py-1.5 whitespace-nowrap">
                          {fmtDate(a.date)}
                        </td>
                        <td className="px-3 py-1.5">
                          {a.child ? (
                            a.child.name || 'Child'
                          ) : (
                            <span className="text-gray-400">Not matched</span>
                          )}
                        </td>
                        <td className="px-3 py-1.5">
                          {a.child ? schoolLabelOf(a.child) : '–'}
                        </td>
                        <td className="px-3 py-1.5">{a.dose || '–'}</td>
                        <td className="px-3 py-1.5">
                          <Badge tone={sevTone}>
                            {iptTitle(a.severity) || '–'}
                          </Badge>
                        </td>
                        <td className="px-3 py-1.5">
                          {a.symptoms
                            .map(function (x) {
                              return IPT_SYMPTOMS[x] || x;
                            })
                            .join(', ')}
                        </td>
                        <td className="px-3 py-1.5">
                          {a.actions
                            .map(function (x) {
                              return IPT_AE_ACTION[x] || x;
                            })
                            .join(', ') || '–'}
                        </td>
                        <td className="px-3 py-1.5 whitespace-nowrap">
                          {IPT_AE_STATUS[a.status] || a.status || '–'}
                        </td>
                        <td className="px-3 py-1.5">
                          {IPT_COURSE_ACTION[a.courseAction] ||
                            a.courseAction ||
                            '–'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="px-4 pb-4 text-sm text-gray-500">
              No moderate or worse adverse event, and no referral, has been
              logged.
            </div>
          )}
        </R.Card>
        <div
          className="grid gap-4"
          style={{
            gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))',
          }}
        >
          <R.Card>
            <R.SectionTitle
              sub={
                'Of ' +
                s.screenTotal +
                ' Day 1 forms that reached the safety screen.'
              }
            >
              Pre-dose safety screen
            </R.SectionTitle>
            <div>
              {s.screen.map(function (x) {
                return (
                  <KV key={x.label} label={x.label}>
                    <Rate num={x.s.num} den={x.s.den} />
                  </KV>
                );
              })}
            </div>
          </R.Card>
          <R.Card>
            <R.SectionTitle sub="Every dose is followed by 30 minutes of observation.">
              30-minute observation
            </R.SectionTitle>
            <HBars items={obsItems} total={s.obsTotal} labelWidth={180} />
            <div className="text-xs font-semibold text-gray-500 mt-3 mb-1">
              Who observed
            </div>
            <HBars
              items={Object.keys(s.observer).map(function (k) {
                return {
                  label: observerLabels[k] || k,
                  n: s.observer[k],
                  color: '#64748b',
                };
              })}
              total={Object.keys(s.observer).reduce(function (t, k) {
                return t + s.observer[k];
              }, 0)}
              labelWidth={180}
            />
          </R.Card>
        </div>
        <div
          className="grid gap-4"
          style={{
            gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))',
          }}
        >
          <R.Card>
            <R.SectionTitle>Adverse events by severity</R.SectionTitle>
            <HBars
              items={[
                { label: 'Mild', n: s.bySeverity.mild, color: '#fbbf24' },
                {
                  label: 'Moderate',
                  n: s.bySeverity.moderate,
                  color: '#f97316',
                },
                { label: 'Severe', n: s.bySeverity.severe, color: '#dc2626' },
                {
                  label: 'Danger sign',
                  n: s.bySeverity.danger,
                  color: '#7f1d1d',
                },
              ].filter(function (x) {
                return x.n;
              })}
              total={s.aes.length}
              labelWidth={110}
              empty="No adverse event logged."
            />
          </R.Card>
          <R.Card>
            <R.SectionTitle>Symptoms reported</R.SectionTitle>
            <HBars
              items={Object.keys(s.bySymptom)
                .map(function (k) {
                  return {
                    label: IPT_SYMPTOMS[k] || k,
                    n: s.bySymptom[k],
                    color: '#fb923c',
                  };
                })
                .sort(function (a, b) {
                  return b.n - a.n;
                })}
              labelWidth={170}
              empty="No symptoms logged."
            />
          </R.Card>
          <R.Card>
            <R.SectionTitle sub="Vomiting and re-dosing, per dose number.">
              Tolerance
            </R.SectionTitle>
            {[1, 2, 3].map(function (n) {
              var t = s.tolerance[n];
              return (
                <KV key={n} label={'Dose ' + n + ' · vomited'}>
                  <Rate num={t.vomited} den={t.forms} />
                  {t.vomited ? (
                    <span className="text-xs text-gray-500">
                      {' '}
                      · {t.redoseOk} re-dosed OK
                    </span>
                  ) : null}
                </KV>
              );
            })}
          </R.Card>
        </div>
      </div>
    );
  }

  function renderProtocol() {
    return (
      <div className="space-y-4">
        <R.Card padded={false}>
          <div className="px-4 pt-3">
            <R.SectionTitle
              right={<InfoLink id="compliance" onOpen={openDef} />}
              sub="Did workers follow the rules built into the app? Each row shows its count and denominator."
            >
              Protocol compliance
            </R.SectionTitle>
          </div>
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-3 py-2 text-left text-xs font-semibold text-gray-600">
                  Check
                </th>
                <th className="px-3 py-2 text-right text-xs font-semibold text-gray-600">
                  Result
                </th>
                <th className="px-3 py-2 text-left text-xs font-semibold text-gray-600">
                  Why it matters
                </th>
              </tr>
            </thead>
            <tbody>
              {compliance.map(function (r) {
                return (
                  <tr key={r.id} style={{ borderTop: '1px solid #f3f4f6' }}>
                    <td className="px-3 py-2 text-gray-800">{r.label}</td>
                    <td className="px-3 py-2 text-right whitespace-nowrap">
                      {r.den ? (
                        <Rate
                          num={r.num}
                          den={r.den}
                          band={r.id === 'observer_staff' ? null : r.band}
                        />
                      ) : (
                        <span className="text-gray-400">none yet</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-xs text-gray-500">
                      {r.note}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </R.Card>
      </div>
    );
  }

  function renderAuthenticity() {
    var wOpen = workerOpen[0];
    var flagged = workerRows.slice().sort(function (a, b) {
      var r = { red: 0, yellow: 1, green: 2 };
      return r[a.flag.band] - r[b.flag.band] || b.children - a.children;
    });
    var valueText = iptCheckValueText;
    function dupList(ids) {
      return ids
        .map(function (id) {
          return allChildren.filter(function (c) {
            return c.id === id;
          })[0];
        })
        .filter(Boolean);
    }
    return (
      <div className="space-y-4">
        <R.Notice tone="muted">
          These checks look for patterns that real fieldwork rarely produces. A
          flag is a reason to look, for example by opening an audit of that
          worker's photos and recordings. It is not a finding.
          <InfoLink id="authenticity" onOpen={openDef} />
        </R.Notice>
        <R.Card padded={false}>
          <div className="px-4 pt-3">
            <R.SectionTitle sub="Everyone in the current filter. Checks marked info are shown but never raise the flag.">
              Programme checks
            </R.SectionTitle>
          </div>
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-3 py-2 text-left text-xs font-semibold text-gray-600">
                  Check
                </th>
                <th className="px-3 py-2 text-right text-xs font-semibold text-gray-600">
                  Value
                </th>
                <th
                  className="px-3 py-2 text-right text-xs font-semibold text-gray-600"
                  title="Forms or children the check was computed over"
                >
                  Based on
                </th>
              </tr>
            </thead>
            <tbody>
              {IPT_AUTH_CHECKS.map(function (spec) {
                var r = auth[spec.id];
                var t = r && r.band ? IPT_TONE[r.band] : null;
                var ids =
                  r && r.extra && r.extra.childIds ? r.extra.childIds : null;
                var listOpen = authList[0] === spec.id;
                var shown = ids && !spec.dup ? dupList(ids) : [];
                return (
                  <tr
                    key={spec.id}
                    style={{
                      borderTop: '1px solid #f3f4f6',
                      verticalAlign: 'top',
                    }}
                  >
                    <td className="px-3 py-2 text-gray-800">
                      <div>
                        {spec.label}
                        {!spec.contributes ? (
                          <span className="ml-2">
                            <Badge
                              tone="muted"
                              title="Shown, but never raises a worker's flag"
                            >
                              info
                            </Badge>
                          </span>
                        ) : null}
                      </div>
                      <div className="text-xs text-gray-500 mt-0.5">
                        {spec.hint}
                      </div>
                      {spec.dup && r && r.value ? (
                        <button
                          type="button"
                          className="text-xs text-indigo-700 hover:underline mt-1"
                          onClick={function () {
                            setDupCheck(spec.id);
                            setTab('duplicates');
                          }}
                        >
                          Review the consent photos side by side →
                        </button>
                      ) : null}
                      {spec.id === 'throughput' &&
                      r &&
                      r.extra &&
                      r.extra.username ? (
                        <div className="text-xs text-gray-500 mt-0.5">
                          Busiest: {nameOf(r.extra.username)}
                        </div>
                      ) : null}
                      {shown.length ? (
                        <div className="text-xs mt-1">
                          <button
                            type="button"
                            className="text-indigo-700 hover:underline"
                            onClick={function () {
                              authList[1](listOpen ? '' : spec.id);
                            }}
                          >
                            {listOpen ? 'Hide' : 'Show'} the {shown.length}{' '}
                            child{shown.length === 1 ? '' : 'ren'}
                          </button>
                          {listOpen ? (
                            <div
                              className="mt-1"
                              style={{
                                display: 'flex',
                                flexWrap: 'wrap',
                                gap: '2px 10px',
                              }}
                            >
                              {shown.map(function (c) {
                                return (
                                  <button
                                    key={c.id}
                                    type="button"
                                    className="text-indigo-700 hover:underline"
                                    onClick={function () {
                                      setOpenChildId(c.id);
                                    }}
                                  >
                                    {c.name || 'child'} ({nameOf(c.username)})
                                  </button>
                                );
                              })}
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                    </td>
                    <td
                      className="px-3 py-2 text-right whitespace-nowrap tabular-nums"
                      style={t ? { color: t.fg, fontWeight: 700 } : null}
                    >
                      {valueText(spec, r)}
                    </td>
                    <td className="px-3 py-2 text-right text-xs text-gray-500 whitespace-nowrap">
                      {r && r.n ? r.n : '–'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </R.Card>
        <R.Card padded={false}>
          <div className="px-4 pt-3">
            <R.SectionTitle sub="One flag per worker from the contributing checks. Click a worker to see every check.">
              By field worker
            </R.SectionTitle>
          </div>
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-3 py-2 text-left text-xs font-semibold text-gray-600">
                  Field worker
                </th>
                <th className="px-3 py-2 text-right text-xs font-semibold text-gray-600">
                  Children
                </th>
                <th className="px-3 py-2 text-left text-xs font-semibold text-gray-600">
                  Flag
                </th>
                <th className="px-3 py-2 text-left text-xs font-semibold text-gray-600">
                  Reasons
                </th>
              </tr>
            </thead>
            <tbody>
              {flagged.map(function (w) {
                var open = wOpen === w.username;
                var tone = w.flag.band;
                return [
                  <tr
                    key={w.username}
                    onClick={function () {
                      workerOpen[1](open ? '' : w.username);
                    }}
                    className="hover:bg-indigo-50 cursor-pointer"
                    style={{ borderTop: '1px solid #f3f4f6' }}
                  >
                    <td className="px-3 py-2 font-semibold text-gray-800">
                      <span style={{ color: '#9ca3af', marginRight: 6 }}>
                        {open ? '▾' : '▸'}
                      </span>
                      {w.name}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {w.children}
                    </td>
                    <td className="px-3 py-2">
                      <Badge tone={tone}>
                        {tone === 'red'
                          ? 'Review'
                          : tone === 'yellow'
                            ? 'Watch'
                            : 'OK'}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 text-xs text-gray-600">
                      {w.flag.reasons.join(' · ') || '–'}
                    </td>
                  </tr>,
                  open ? (
                    <tr key={w.username + '-d'}>
                      <td
                        colSpan={4}
                        className="px-3 pb-3"
                        style={{ background: '#fafafa' }}
                      >
                        <div
                          className="grid gap-x-6"
                          style={{
                            gridTemplateColumns:
                              'repeat(auto-fit, minmax(300px, 1fr))',
                          }}
                        >
                          {IPT_AUTH_CHECKS.map(function (spec) {
                            var r = w.checks[spec.id];
                            var t = r && r.band ? IPT_TONE[r.band] : null;
                            return (
                              <KV
                                key={spec.id}
                                label={
                                  spec.label +
                                  (spec.contributes ? '' : ' (info)')
                                }
                              >
                                <span
                                  style={
                                    t ? { color: t.fg, fontWeight: 700 } : null
                                  }
                                >
                                  {valueText(spec, r)}
                                </span>
                                <span className="text-xs text-gray-400">
                                  {' '}
                                  · n {r && r.n ? r.n : 0}
                                </span>
                              </KV>
                            );
                          })}
                        </div>
                      </td>
                    </tr>
                  ) : null,
                ];
              })}
            </tbody>
          </table>
        </R.Card>
      </div>
    );
  }

  function renderDuplicates() {
    var checks = IPT_AUTH_CHECKS.filter(function (c) {
      return c.dup;
    });
    var spec = checks.filter(function (c) {
      return c.id === dupCheck[0];
    })[0];
    var r = auth[dupCheck[0]];
    var photos = photoState[0];
    function visitUrlFor(c) {
      return (
        iptConnectVisitUrl(orgSlug, oppId, c.connectUserId, c.userVisitId) ||
        '/audit/visits/' + encodeURIComponent(c.visitId) + '/'
      );
    }
    function groupTitle(list) {
      var c = list[0];
      var bits = [c.name || 'Name not recorded', schoolLabelOf(c)];
      if (dupCheck[0] !== 'dup_name') bits.push('age ' + c.age);
      if (dupCheck[0] === 'dup_name_age_phone') bits.push(c.phone);
      return bits.join(' · ');
    }
    return (
      <div className="space-y-4">
        <R.Card>
          <div className="flex flex-wrap items-end gap-4">
            <Select
              label="Duplicate check"
              value={dupCheck[0]}
              minWidth={360}
              options={checks.map(function (c) {
                var x = auth[c.id];
                return {
                  value: c.id,
                  label:
                    c.label.replace('Possible duplicates: ', '') +
                    ' — ' +
                    iptCheckValueText(c, x),
                };
              })}
              onChange={setDupCheck}
            />
            <div
              className="text-sm text-gray-600"
              style={{ flex: 1, minWidth: 260 }}
            >
              {spec ? spec.hint : ''}
            </div>
          </div>
          <div className="text-xs text-gray-500 mt-2">
            Each row is one suspected child: every registration that matches on
            the check's attributes, side by side. Compare the consent photos and
            details, then open the visit in Connect.
            {orgSlug
              ? ''
              : ' (The opportunity’s organisation is not known on this page, so visit links open the labs copy of the visit.)'}
          </div>
        </R.Card>
        {!dupGroups.length ? (
          <R.Notice tone="muted">
            No children match on this check
            {filtered ? ' in the current filter' : ''}.
          </R.Notice>
        ) : (
          <div className="text-sm text-gray-700">
            <b>{r.value}</b> registrations flagged, <b>{r.extra.groups}</b>{' '}
            unique suspected {r.extra.groups === 1 ? 'child' : 'children'}.
          </div>
        )}
        {dupGroups.map(function (g) {
          var list = g.childIds
            .map(function (id) {
              return childById[id];
            })
            .filter(Boolean)
            .sort(function (a, b) {
              return (a.regDate || '') < (b.regDate || '') ? -1 : 1;
            });
          if (!list.length) return null;
          var workers = {};
          list.forEach(function (c) {
            workers[nameOf(c.username)] = true;
          });
          var nWorkers = Object.keys(workers).length;
          return (
            <R.Card key={g.key}>
              <div className="flex flex-wrap items-baseline justify-between gap-2 mb-2">
                <div className="text-sm font-semibold text-gray-900">
                  {groupTitle(list)}
                </div>
                <div className="text-xs text-gray-500">
                  {list.length} registrations ·{' '}
                  {nWorkers === 1
                    ? 'one field worker'
                    : nWorkers + ' field workers'}
                  {nWorkers > 1 ? (
                    <span className="ml-2">
                      <Badge
                        tone="yellow"
                        title="Registered by more than one field worker"
                      >
                        across workers
                      </Badge>
                    </span>
                  ) : null}
                </div>
              </div>
              <div
                style={{
                  display: 'flex',
                  gap: 12,
                  overflowX: 'auto',
                  paddingBottom: 4,
                }}
              >
                {list.map(function (c) {
                  return (
                    <DupPhotoCard
                      key={c.id}
                      child={c}
                      photo={photos[c.visitId]}
                      oppId={oppId}
                      schoolLabel={schoolLabelOf(c)}
                      workerName={nameOf(c.username)}
                      visitUrl={visitUrlFor(c)}
                      onOpenChild={function () {
                        setOpenChildId(c.id);
                      }}
                    />
                  );
                })}
              </div>
            </R.Card>
          );
        })}
      </div>
    );
  }

  function renderWorkers() {
    var sorter = workerSort;
    // Ties keep this order (Array.sort is stable): flagged first, then by name.
    var base = workerRows.slice().sort(function (a, b) {
      var r = { red: 0, yellow: 1, green: 2 };
      return r[a.flag.band] - r[b.flag.band] || a.name.localeCompare(b.name);
    });
    var rows = sorter.apply(base, {
      name: function (w) {
        return w.name.toLowerCase();
      },
      children: function (w) {
        return w.children;
      },
      complete: function (w) {
        return w.complete.due ? w.complete.got / w.complete.due : null;
      },
      missing: function (w) {
        return w.missing;
      },
      ontime: function (w) {
        return w.onTime.due ? w.onTime.got / w.onTime.due : null;
      },
      dot: function (w) {
        return w.dot.due ? w.dot.got / w.dot.due : null;
      },
      flag: function (w) {
        return { red: 2, yellow: 1, green: 0 }[w.flag.band];
      },
      last: function (w) {
        return w.lastDate;
      },
    });
    function exportWorkers() {
      downloadCsv(
        'iptsc_workers_' + today + '.csv',
        iptCsv(
          [
            'Field worker',
            'Children',
            'Schools',
            'Doses given',
            'Dose 1',
            'Dose 2',
            'Dose 3',
            'Full course %',
            'Missing a dose',
            'On time %',
            'Observed %',
            'Authenticity flag',
            'Reasons',
            'Last active',
          ],
          rows.map(function (w) {
            return [
              w.name,
              w.children,
              w.schools,
              w.doses,
              w.d1,
              w.d2,
              w.d3,
              w.complete.due
                ? Math.round(iptPct(w.complete.got, w.complete.due))
                : '',
              w.missing,
              w.onTime.due
                ? Math.round(iptPct(w.onTime.got, w.onTime.due))
                : '',
              w.dot.due ? Math.round(iptPct(w.dot.got, w.dot.due)) : '',
              w.flag.band,
              w.flag.reasons.join('; '),
              w.lastDate || '',
            ];
          }),
        ),
      );
    }
    var S = sorter;
    return (
      <div className="space-y-4">
        <R.Card padded={false}>
          <div className="px-4 pt-3">
            <R.SectionTitle
              right={<R.Button onClick={exportWorkers}>Download CSV</R.Button>}
              sub="One row per worker, from the children they registered. Rates with too few children to judge are greyed. Click a column to sort."
            >
              Field workers
            </R.SectionTitle>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <SortHeader k="name" sort={S.sort} onSort={S.onSort}>
                    Field worker
                  </SortHeader>
                  <SortHeader
                    k="children"
                    sort={S.sort}
                    onSort={S.onSort}
                    right={true}
                  >
                    Children
                  </SortHeader>
                  <th className="px-3 py-2 text-right text-xs font-semibold text-gray-600 whitespace-nowrap">
                    Doses 1 / 2 / 3
                  </th>
                  <SortHeader
                    k="complete"
                    sort={S.sort}
                    onSort={S.onSort}
                    right={true}
                  >
                    Full course
                  </SortHeader>
                  <SortHeader
                    k="missing"
                    sort={S.sort}
                    onSort={S.onSort}
                    right={true}
                  >
                    Missing a dose
                  </SortHeader>
                  <SortHeader
                    k="ontime"
                    sort={S.sort}
                    onSort={S.onSort}
                    right={true}
                  >
                    On time
                  </SortHeader>
                  <SortHeader
                    k="dot"
                    sort={S.sort}
                    onSort={S.onSort}
                    right={true}
                  >
                    Observed
                  </SortHeader>
                  <SortHeader k="flag" sort={S.sort} onSort={S.onSort}>
                    Authenticity
                  </SortHeader>
                  <SortHeader k="last" sort={S.sort} onSort={S.onSort}>
                    Last active
                  </SortHeader>
                  <th className="px-3 py-2 text-xs font-semibold text-gray-600" />
                </tr>
              </thead>
              <tbody>
                {rows.map(function (w) {
                  return (
                    <tr
                      key={w.username}
                      style={{ borderTop: '1px solid #f3f4f6' }}
                    >
                      <td className="px-3 py-2 font-semibold text-gray-800 whitespace-nowrap">
                        {w.name}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {w.children}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-gray-600 whitespace-nowrap">
                        {w.d1} / {w.d2} / {w.d3}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <Rate
                          num={w.complete.got}
                          den={w.complete.due}
                          floor={opts.minChildren}
                          band={bandHighGood(
                            iptPct(w.complete.got, w.complete.due),
                            90,
                            80,
                          )}
                        />
                      </td>
                      <td
                        className="px-3 py-2 text-right tabular-nums"
                        style={{
                          color: w.missing ? '#b91c1c' : undefined,
                          fontWeight: w.missing ? 700 : 400,
                        }}
                      >
                        {w.missing}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <Rate
                          num={w.onTime.got}
                          den={w.onTime.due}
                          floor={opts.minChildren}
                          band={bandHighGood(
                            iptPct(w.onTime.got, w.onTime.due),
                            85,
                            70,
                          )}
                        />
                      </td>
                      <td className="px-3 py-2 text-right">
                        <Rate
                          num={w.dot.got}
                          den={w.dot.due}
                          floor={opts.minDoses}
                          band={bandHighGood(
                            iptPct(w.dot.got, w.dot.due),
                            98,
                            95,
                          )}
                        />
                      </td>
                      <td className="px-3 py-2">
                        <span
                          title={w.flag.reasons.join('\n') || 'No check raised'}
                        >
                          <Badge tone={w.flag.band}>
                            {w.flag.band === 'red'
                              ? 'Review'
                              : w.flag.band === 'yellow'
                                ? 'Watch'
                                : 'OK'}
                          </Badge>
                        </span>
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap text-gray-600">
                        {fmtDate(w.lastDate)}
                        {w.daysSince !== null && w.daysSince >= 2 ? (
                          <span
                            className="text-xs"
                            style={{ color: '#b45309' }}
                          >
                            {' '}
                            · {w.daysSince}d ago
                          </span>
                        ) : null}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap text-right">
                        <button
                          type="button"
                          className="text-xs text-indigo-700 hover:underline mr-3"
                          onClick={function () {
                            filterState[1](function (f) {
                              return Object.assign({}, f, { flw: w.username });
                            });
                            gridFilter[1](w.missing ? 'attention' : 'all');
                            setTab('completion');
                          }}
                        >
                          Their children
                        </button>
                        {props.links && props.links.auditUrl ? (
                          <a
                            className="text-xs text-indigo-700 hover:underline mr-3"
                            href={props.links.auditUrl({
                              username: w.username,
                              count: 10,
                              title: 'IPTsc review: ' + w.name,
                              tag: 'iptsc',
                            })}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Audit
                          </a>
                        ) : null}
                        {props.links && props.links.taskUrl ? (
                          <a
                            className="text-xs text-indigo-700 hover:underline"
                            href={props.links.taskUrl({
                              username: w.username,
                              title: 'IPTsc follow-up: ' + w.name,
                              description:
                                (w.missing
                                  ? w.missing + ' child(ren) missing a dose. '
                                  : '') +
                                (w.flag.reasons.length
                                  ? 'Checks raised: ' +
                                    w.flag.reasons.join('; ') +
                                    '.'
                                  : ''),
                              priority:
                                w.flag.band === 'red' ? 'high' : 'medium',
                            })}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Task
                          </a>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
                {!rows.length ? (
                  <tr>
                    <td
                      colSpan={10}
                      className="px-3 py-3 text-sm text-gray-500"
                    >
                      No field worker activity yet.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </R.Card>
      </div>
    );
  }

  function renderDefinitions() {
    var focus = defFocus[0];
    var sections = [];
    IPT_DEFINITIONS.forEach(function (d) {
      if (sections.indexOf(d.section) < 0) sections.push(d.section);
    });
    return (
      <div className="space-y-4">
        <R.Notice tone="muted">
          Every figure is computed in this page from the latest forms each time
          it loads. Dates are calendar days in West Africa Time. A dose counts
          as given only when the form records it as observed and swallowed (or
          vomited and re-dosed successfully).
        </R.Notice>
        {sections.map(function (sec) {
          return (
            <R.Card key={sec}>
              <R.SectionTitle>{sec}</R.SectionTitle>
              <dl>
                {IPT_DEFINITIONS.filter(function (d) {
                  return d.section === sec;
                }).map(function (d) {
                  var on = focus === d.id;
                  return (
                    <div
                      key={d.id}
                      id={'def-' + d.id}
                      ref={
                        on
                          ? function (el) {
                              if (el && el.scrollIntoView)
                                setTimeout(function () {
                                  el.scrollIntoView({
                                    block: 'center',
                                    behavior: 'smooth',
                                  });
                                }, 50);
                            }
                          : null
                      }
                      className="py-2"
                      style={{
                        borderTop: '1px solid #f3f4f6',
                        background: on ? '#eef2ff' : undefined,
                        borderRadius: on ? 6 : 0,
                        paddingLeft: on ? 8 : 0,
                      }}
                    >
                      <dt className="text-sm font-semibold text-gray-900">
                        {d.name}
                      </dt>
                      <dd className="text-sm text-gray-700 mt-0.5">{d.def}</dd>
                      <dd className="text-xs text-gray-500 mt-0.5">
                        Source: {d.source}
                        {d.threshold ? ' · ' + d.threshold : ''}
                      </dd>
                    </div>
                  );
                })}
              </dl>
            </R.Card>
          );
        })}
      </div>
    );
  }
}

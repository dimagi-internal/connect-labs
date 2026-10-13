function WorkflowUI({ definition, scope, config, supply, view }) {
  // ══ A program's supply home (workflow/templates/supply_program_home.py) ══
  //
  // A page: no run, drawn in the page sandbox. Every figure is the server's --
  // `supply.<alias>` from the supply sources this page declares, read as the
  // viewer -- and every way in is a link: the Supply tabs come from the
  // program's Settings (`config.supply.tabs`), so no workflow id is written here.
  // ES5 dialect (Babel transpiles JSX only).
  var cfg = (definition && definition.config) || {};
  var s = scope || {};
  var src = supply || {};
  var program = s.program || {};
  var programId = program.id || s.key;
  var tabs = (config && config.supply && config.supply.tabs) || {};
  var courseSize = Number(cfg.course_size) || 0;
  var courseLabel = cfg.course_label || 'courses';

  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    var x = typeof v === 'object' ? v.amount : v;
    if (x === null || x === undefined || x === '') return null;
    var n = Number(x);
    return isFinite(n) ? n : null;
  }
  function fmt(n) {
    if (n === null || n === undefined) return '—';
    return Math.round(n).toLocaleString();
  }
  function day(iso) {
    if (!iso) return '';
    var d = new Date(String(iso).slice(0, 10) + 'T00:00:00');
    return d.toLocaleDateString(undefined, {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    });
  }
  function q(path) {
    if (!programId) return path;
    return (
      path + (path.indexOf('?') === -1 ? '?' : '&') + 'program_id=' + programId
    );
  }
  function loading(alias) {
    var d = src[alias];
    return !d || (!d.rows && !(d.metadata && d.metadata.error));
  }
  function errorOf(alias) {
    var d = src[alias] || {};
    if (d.metadata && d.metadata.error) return d.metadata.error;
    var per = (d.metadata && d.metadata.per_opp) || {};
    var bad = Object.keys(per).filter(function (k) {
      return per[k] && per[k].error;
    });
    return bad.length ? per[bad[0]].error : null;
  }

  // ---- the figures ----
  var stock = src.stock || {};
  var rollup = stock.rollup || {};
  var below = rollup.below_minimum || null;
  var sachets = (rollup.by_unit || {}).sachet || {};
  var onHand = num(sachets.on_hand);

  var forecast =
    src.forecast && src.forecast.rows ? src.forecast.rows[0] : null;
  var fp = (forecast && forecast.programme) || {};
  var basis = (forecast && forecast.basis) || {};
  var delivered = basis.delivered || null;
  var atProtocol =
    (basis.profile || []).length &&
    (basis.profile || []).every(function (p) {
      return p.basis === 'protocol';
    });

  var checks = (src.checks && src.checks.rows) || [];
  var byKind = {};
  checks.forEach(function (c) {
    byKind[c.kind] = (byKind[c.kind] || 0) + 1;
  });
  var kinds = Object.keys(byKind).sort(function (a, b) {
    return byKind[b] - byKind[a];
  });

  // The network tree, top store down: every store, with its own figures and its
  // subtree's (what it and everything below it hold, paced on the workers below).
  // One unit throughout -- the item's own, sachets for RUTF.
  var stores = [];
  (function walk(nodes) {
    (nodes || []).forEach(function (r) {
      if (r.kind === 'user_held') return;
      stores.push(r);
      walk(r.children);
    });
  })(src.stores && src.stores.rows);
  var storeUnit =
    (src.stores && src.stores.metadata && src.stores.metadata.unit) ||
    (stores[0] && stores[0].unit) ||
    '';
  var asOf = stock.metadata && (stock.metadata.as_of || stock.metadata.today);

  // ---- the ways in: the program's Supply tabs, as its Settings say ----
  var tabLinks = [];
  var workflowLinks = [];
  Object.keys(tabs).forEach(function (key) {
    var t = tabs[key] || {};
    if (t.hidden || !t.fill) return;
    var slug = t.slug || key;
    tabLinks.push({
      href: q('/supply/views/' + slug + '/'),
      label: t.label || key,
      position: t.position || 0,
    });
    var opp = t.fill.opportunity_id;
    workflowLinks.push({
      href:
        '/labs/workflow/' +
        t.fill.workflow +
        '/run/' +
        (opp ? '?opportunity_id=' + opp : ''),
      label: (t.label || key) + ' workflow',
    });
  });
  tabLinks.sort(function (a, b) {
    return a.position - b.position;
  });
  var builtIn = [
    { href: q('/supply/overview/'), label: 'Overview' },
    { href: q('/supply/network/'), label: 'Network' },
    { href: q('/supply/map/'), label: 'Map' },
    { href: q('/supply/workers/'), label: 'Workers' },
    { href: q('/supply/flow/'), label: 'Where it went' },
    { href: q('/supply/orders/'), label: 'Orders' },
  ];
  var reviewHref = tabLinks.length ? tabLinks[0].href : q('/supply/workers/');
  var forecastTab = tabLinks.filter(function (t) {
    return /forecast/i.test(t.label);
  })[0];
  var forecastHref = forecastTab ? forecastTab.href : q('/supply/');

  function tile(props) {
    return (
      <a
        href={props.href}
        className="block rounded-lg border border-gray-200 bg-white p-4 hover:border-brand-indigo"
        data-testid={props.testid}
      >
        <div className="text-xs uppercase tracking-wide text-gray-500">
          {props.label}
        </div>
        <div
          className={
            'mt-1 text-2xl font-semibold ' +
            (props.alarm ? 'text-red-700' : 'text-gray-900')
          }
        >
          {props.value}
        </div>
        {props.sub ? (
          <div className="mt-1 text-xs text-gray-600">{props.sub}</div>
        ) : null}
      </a>
    );
  }

  return (
    <div className="max-w-6xl space-y-6">
      <div>
        <div className="text-xs uppercase tracking-wide text-gray-600">
          Program
          {s.organization ? (
            <span>
              {' · '}
              <a
                href={s.organization.page_url}
                className="text-brand-indigo hover:underline"
              >
                {s.organization.name}
              </a>
            </span>
          ) : null}
        </div>
        <h1 className="text-2xl font-bold text-brand-indigo">
          {program.name || 'Program'}
        </h1>
        <p className="text-sm text-gray-600">
          Supply at a glance{asOf ? ', as of ' + day(asOf) : ''}. Every figure
          is the supply chain's own, read as you; open any of them to see the
          workers and stores behind it.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {tile({
          testid: 'below-minimum',
          href: reviewHref,
          label: 'Workers below minimum',
          alarm: below && below.below > 0,
          value: loading('stock')
            ? '…'
            : below
              ? below.below + ' of ' + below.workers
              : '—',
          sub: errorOf('stock') || (below ? below.line : null),
        })}
        {tile({
          testid: 'runs-dry',
          href: forecastHref,
          label: 'Network runs dry' + (atProtocol ? ', at protocol' : ''),
          alarm: !!fp.runs_dry_on,
          value: loading('forecast')
            ? '…'
            : fp.runs_dry_on
              ? day(fp.runs_dry_on)
              : forecast
                ? 'not in ' + (forecast.weeks || []).length + ' weeks'
                : '—',
          sub:
            errorOf('forecast') ||
            (num(fp.shortfall_by_end)
              ? fmt(num(fp.shortfall_by_end)) +
                ' sachets short by the end of the forecast'
              : forecast
                ? 'covered to the end of the forecast'
                : null),
        })}
        {tile({
          testid: 'share-of-protocol',
          href: forecastHref,
          label: 'Ration delivered',
          value:
            delivered &&
            delivered.share_of_protocol !== null &&
            delivered.share_of_protocol !== undefined
              ? delivered.share_of_protocol + '% of protocol'
              : loading('forecast')
                ? '…'
                : '—',
          sub: delivered
            ? 'about ' +
              num(delivered.per_day).toFixed(1) +
              ' sachets a day per child, against ' +
              fmt(num(delivered.protocol_per_day)) +
              ' at protocol, across ' +
              delivered.children +
              ' children'
            : null,
        })}
        {tile({
          testid: 'in-hands',
          href: q('/supply/workers/'),
          label: "In workers' hands",
          value:
            onHand === null
              ? loading('stock')
                ? '…'
                : '—'
              : fmt(onHand) + ' sachets',
          sub:
            courseSize && onHand !== null
              ? '≈ ' +
                fmt(onHand / courseSize) +
                ' ' +
                courseLabel +
                ' (' +
                courseSize +
                ' sachets each)'
              : null,
        })}
      </div>

      {delivered && atProtocol ? (
        <div
          className="rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-gray-800"
          data-testid="protocol-note"
        >
          The forecast lays need forward at the protocol's ration, so it runs
          dry far sooner than each worker's own pace says: children are
          receiving about {delivered.share_of_protocol}% of protocol. Both are
          on the{' '}
          <a href={forecastHref} className="text-brand-indigo hover:underline">
            Forecast
          </a>
          .
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="rounded-lg border border-gray-200 bg-white p-4">
          <h2 className="mb-2 text-lg font-semibold text-gray-900">Stores</h2>
          {loading('stores') ? (
            <div className="text-sm text-gray-600">Loading…</div>
          ) : stores.length ? (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-gray-500">
                  <th className="py-1">Store</th>
                  <th className="py-1 text-right">
                    {'On hand' + (storeUnit ? ' (' + storeUnit + 's)' : '')}
                  </th>
                  <th className="py-1 text-right">Its cover</th>
                  <th className="py-1 text-right">Its workers' cover</th>
                </tr>
              </thead>
              <tbody>
                {stores.map(function (r) {
                  var sub = r.subtree || {};
                  return (
                    <tr
                      key={r.supply_point_id}
                      className="border-t border-gray-100"
                    >
                      <td className="py-1">{r.name}</td>
                      <td className="py-1 text-right">{fmt(num(r.on_hand))}</td>
                      <td className="py-1 text-right">
                        {r.months_of_stock
                          ? Number(r.months_of_stock).toFixed(1) + ' mo'
                          : '—'}
                        <span className="ml-1 text-xs text-gray-500">
                          {r.status || ''}
                        </span>
                      </td>
                      <td className="py-1 text-right">
                        {sub.months_of_stock
                          ? Number(sub.months_of_stock).toFixed(1) + ' mo'
                          : '—'}
                        <span className="ml-1 text-xs text-gray-500">
                          {sub.status || ''}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <div className="text-sm text-gray-600">
              {errorOf('stores') || 'No stores recorded yet.'}
            </div>
          )}
        </div>

        <div className="rounded-lg border border-gray-200 bg-white p-4">
          <h2 className="mb-2 text-lg font-semibold text-gray-900">
            Open checks
          </h2>
          {loading('checks') ? (
            <div className="text-sm text-gray-600">Loading…</div>
          ) : (
            <div className="text-sm text-gray-700">
              <div className="mb-2">
                {checks.length
                  ? checks.length +
                    ' open check' +
                    (checks.length === 1 ? '' : 's') +
                    ': gaps and disagreements in the records, unranked.'
                  : errorOf('checks') || 'No open checks.'}
              </div>
              <ul className="space-y-1">
                {kinds.slice(0, 6).map(function (k) {
                  return (
                    <li key={k} className="flex justify-between">
                      <span>{k.replace(/_/g, ' ')}</span>
                      <span className="text-gray-500">{byKind[k]}</span>
                    </li>
                  );
                })}
              </ul>
              <a
                href={q('/supply/map/')}
                className="mt-2 inline-block text-brand-indigo hover:underline"
              >
                See them on the map
              </a>
            </div>
          )}
        </div>
      </div>

      <div className="rounded-lg border border-gray-200 bg-white p-4">
        <h2 className="mb-2 text-lg font-semibold text-gray-900">Supply</h2>
        <div className="flex flex-wrap gap-2">
          {tabLinks.concat(builtIn).map(function (t) {
            return (
              <a
                key={t.href}
                href={t.href}
                className="rounded-md border border-gray-200 px-3 py-1 text-sm text-brand-indigo hover:border-brand-indigo"
              >
                {t.label}
              </a>
            );
          })}
        </div>
        {workflowLinks.length ? (
          <div className="mt-3 text-sm text-gray-600">
            The workflows behind the tabs:{' '}
            {workflowLinks.map(function (w, i) {
              return (
                <span key={w.href}>
                  {i ? ' · ' : ''}
                  <a
                    href={w.href}
                    className="text-brand-indigo hover:underline"
                  >
                    {w.label}
                  </a>
                </span>
              );
            })}
          </div>
        ) : null}
        {(s.opportunities || []).length ? (
          <div className="mt-3 text-sm text-gray-600">
            Opportunities:{' '}
            {s.opportunities.map(function (o, i) {
              return (
                <span key={o.id}>
                  {i ? ' · ' : ''}
                  <a
                    href={o.workflows_url}
                    className="text-brand-indigo hover:underline"
                  >
                    {o.name}
                  </a>
                </span>
              );
            })}
          </div>
        ) : null}
      </div>

      {s.settings_url ? (
        <div className="text-sm text-gray-600">
          Which tabs Supply shows, and which page this program opens on, are set
          in{' '}
          <a
            href={s.settings_url}
            className="text-brand-indigo hover:underline"
          >
            Settings
          </a>
          .
        </div>
      ) : null}
    </div>
  );
}

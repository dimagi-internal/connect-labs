function WorkflowUI({ definition, pipelines }) {
  var data = (pipelines || {}).workers || {};
  var rows = (data.rows || []).slice().sort(function (a, b) { return (b.forms || 0) - (a.forms || 0); });
  var total = rows.reduce(function (n, r) { return n + (r.forms || 0); }, 0);
  var top = rows.length ? rows[0].forms || 1 : 1;
  var error = data.metadata && data.metadata.per_opp && Object.values(data.metadata.per_opp).map(function (m) { return m.error; }).filter(Boolean)[0];

  function tile(label, value) {
    return (
      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <div className="text-xs text-gray-600 uppercase tracking-wide">{label}</div>
        <div className="text-3xl font-bold text-brand-indigo mt-1">{value}</div>
      </div>
    );
  }

  return (
    <div className="max-w-4xl">
      <h1 className="text-2xl font-bold text-brand-indigo mb-1">{(definition && definition.name) || 'Project overview'}</h1>
      <p className="text-sm text-gray-600 mb-6">Forms submitted per worker, from the forms this project keeps in labs.</p>
      {error ? <div className="mb-4 rounded-md border border-red-300 bg-red-50 px-4 py-2 text-sm text-red-800">{error}</div> : null}
      <div className="grid grid-cols-2 gap-4 mb-6">
        {tile('Forms', total.toLocaleString())}
        {tile('Workers', rows.length.toLocaleString())}
      </div>
      {rows.length === 0 ? (
        <div className="rounded-md border border-yellow-300 bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
          No forms stored yet. Sync the project, then reload this page.
        </div>
      ) : (
        <div className="bg-white border border-gray-200 rounded-lg divide-y divide-gray-100">
          {rows.map(function (r) {
            return (
              <div key={r.username} className="flex items-center gap-3 px-4 py-2">
                <div className="w-48 truncate text-sm text-gray-900">{r.username || '(no username)'}</div>
                <div className="flex-1 bg-gray-100 rounded h-3">
                  <div className="bg-brand-indigo h-3 rounded" style={{ width: Math.max(2, Math.round(((r.forms || 0) / top) * 100)) + '%' }}></div>
                </div>
                <div className="w-16 text-right text-sm text-gray-700">{(r.forms || 0).toLocaleString()}</div>
                <div className="w-28 text-right text-xs text-gray-500">{r.last_visit_date || ''}</div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

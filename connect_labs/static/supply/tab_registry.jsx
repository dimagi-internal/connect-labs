function RegistryTab({ ctx }) {
  const { world } = ctx;
  const [filters, setFilters] = useState({ category: "", country: "", expiring: "" });
  const [rows, setRows] = useState(world.registry || []);
  const [detail, setDetail] = useState(null);

  const apply = useCallback(async (next) => {
    const params = new URLSearchParams();
    if (next.category) params.set("category", next.category);
    if (next.country) params.set("country", next.country);
    if (next.expiring) params.set("expiring_within_days", next.expiring);
    const qs = params.toString();
    const body = await supplyGet(`/supply/api/registry/${qs ? `?${qs}` : ""}`);
    setRows(body.registry);
  }, []);

  const change = (key) => (e) => {
    const next = { ...filters, [key]: e.target.value };
    setFilters(next);
    apply(next);
  };

  const countries = Array.from(new Set((world.registry || []).map((r) => r.org.country))).sort();

  return (
    <Page
      title="Supplier registry"
      lede="Organisations holding live qualifications. Solicitations are issued from this registry."
    >
      <Card
        title={`${rows.length} qualified ${rows.length === 1 ? "supplier" : "suppliers"}`}
        actions={
          <div className="filter-row">
            <select value={filters.category} onChange={change("category")}>
              <option value="">All categories</option>
              {SUPPLY_CATEGORIES.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.label}
                </option>
              ))}
            </select>
            <select value={filters.country} onChange={change("country")}>
              <option value="">All countries</option>
              {countries.map((c) => (
                <option key={c} value={c}>
                  {countryLabel(c)}
                </option>
              ))}
            </select>
            <select value={filters.expiring} onChange={change("expiring")}>
              <option value="">Any expiry</option>
              <option value="30">Expiring in 30 days</option>
              <option value="60">Expiring in 60 days</option>
              <option value="90">Expiring in 90 days</option>
            </select>
          </div>
        }
      >
        <DataTable
          rows={rows}
          rowKey={(r) => r.org.id}
          empty="No suppliers match these filters."
          onRowClick={(r) => setDetail(r)}
          columns={[
            { key: "name", label: "Supplier", value: (r) => r.org.legal_name },
            { key: "country", label: "Country", value: (r) => r.org.country, render: (r) => countryLabel(r.org.country) },
            { key: "city", label: "Head office", value: (r) => r.org.hq_city || "—" },
            {
              key: "cats",
              label: "Qualified for",
              sortable: false,
              value: () => "",
              render: (r) => <CategoryPills categories={r.qualifications.map((q) => q.category)} />,
            },
            {
              key: "expiry",
              label: "Earliest expiry",
              value: (r) => r.qualifications.map((q) => q.expires_at).sort()[0],
              render: (r) => <ExpiryChip iso={r.qualifications.map((q) => q.expires_at).sort()[0]} />,
            },
          ]}
        />
      </Card>

      {detail ? <RegistryDetailModal row={detail} onClose={() => setDetail(null)} /> : null}
    </Page>
  );
}

function RegistryDetailModal({ row, onClose }) {
  return (
    <Modal title={row.org.legal_name} onClose={onClose}>
      <dl className="deflist">
        <dt>Country</dt>
        <dd>{countryLabel(row.org.country)}</dd>
        <dt>Head office</dt>
        <dd>{row.org.hq_city || "—"}</dd>
        <dt>Registration</dt>
        <dd>{row.org.registration_number || "—"}</dd>
        <dt>GLN</dt>
        <dd>{row.org.gln || "—"}</dd>
        <dt>Contact</dt>
        <dd>
          {row.org.contact_name || "—"}
          {row.org.contact_email ? ` · ${row.org.contact_email}` : ""}
        </dd>
      </dl>
      {row.org.description ? <p>{row.org.description}</p> : null}

      <h4>Live qualifications</h4>
      <DataTable
        rows={row.qualifications}
        rowKey={(q) => q.id}
        columns={[
          { key: "cat", label: "Category", value: (q) => categoryLabel(q.category) },
          { key: "granted", label: "Granted", value: (q) => q.granted_at, render: (q) => formatDate(q.granted_at) },
          { key: "expires", label: "Expires", value: (q) => q.expires_at, render: (q) => <ExpiryChip iso={q.expires_at} /> },
        ]}
      />

      <h4>Certifications</h4>
      <DataTable
        rows={row.org.certifications || []}
        rowKey={(c) => c.id}
        empty="No certifications on file."
        columns={[
          { key: "type", label: "Certification", value: (c) => c.cert_type },
          { key: "issuer", label: "Issuer", value: (c) => c.issuer || "—" },
          { key: "expiry", label: "Expires", value: (c) => c.expiry_date, render: (c) => <ExpiryChip iso={c.expiry_date} /> },
        ]}
      />
    </Modal>
  );
}

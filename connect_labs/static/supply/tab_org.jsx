function OrgTab({ ctx }) {
  const { world, act } = ctx;
  const org = world.org;
  const [form, setForm] = useState({
    registration_number: org.registration_number || "",
    hq_city: org.hq_city || "",
    description: org.description || "",
    contact_name: org.contact_name || "",
    contact_email: org.contact_email || "",
    gln: org.gln || "",
    gs1_company_prefix: org.gs1_company_prefix || "",
  });
  const [adding, setAdding] = useState(false);

  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  const save = () => act(() => supplyPost("/supply/api/org/profile/", form), "Profile saved.");

  return (
    <Page
      title="Organisation profile"
      lede="Reviewers assess a frozen copy of this profile at the moment you submit an expression of interest."
    >
      <div className="grid-2">
        <Card
          title="Profile"
          subtitle={`${org.legal_name} · ${countryLabel(org.country)}`}
          actions={
            <button type="button" className="btn" onClick={save} disabled={ctx.busy}>
              Save changes
            </button>
          }
        >
          <FormRow label="Registration number">
            <input type="text" value={form.registration_number} onChange={set("registration_number")} />
          </FormRow>
          <FormRow label="Head office city">
            <input type="text" value={form.hq_city} onChange={set("hq_city")} />
          </FormRow>
          <FormRow label="Description" hint="Capability summary reviewers will read.">
            <textarea rows="4" value={form.description} onChange={set("description")} />
          </FormRow>
          <div className="field-row-2">
            <FormRow label="Contact name">
              <input type="text" value={form.contact_name} onChange={set("contact_name")} />
            </FormRow>
            <FormRow label="Contact email">
              <input type="email" value={form.contact_email} onChange={set("contact_email")} />
            </FormRow>
          </div>
          <div className="field-row-2">
            <FormRow label="GLN" hint="GS1 Global Location Number (13 digits).">
              <input type="text" value={form.gln} onChange={set("gln")} maxLength="13" />
            </FormRow>
            <FormRow label="GS1 company prefix">
              <input
                type="text"
                value={form.gs1_company_prefix}
                onChange={set("gs1_company_prefix")}
                maxLength="12"
              />
            </FormRow>
          </div>
        </Card>

        <Card
          title="Certifications"
          subtitle="Expiry dates are visible to reviewers and flagged in the registry."
          actions={
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => setAdding(true)}>
              Add certification
            </button>
          }
        >
          <DataTable
            rows={org.certifications}
            rowKey={(c) => c.id}
            empty="No certifications recorded yet."
            columns={[
              { key: "type", label: "Certification", value: (c) => c.cert_type },
              { key: "issuer", label: "Issuer", value: (c) => c.issuer || "—" },
              {
                key: "expiry",
                label: "Expires",
                value: (c) => c.expiry_date,
                render: (c) => <ExpiryChip iso={c.expiry_date} />,
              },
              {
                key: "actions",
                label: "",
                sortable: false,
                value: () => "",
                render: (c) => (
                  <button
                    type="button"
                    className="btn-link danger"
                    onClick={() =>
                      act(
                        () => supplyPost(`/supply/api/org/certifications/${c.id}/delete/`, {}),
                        "Certification removed."
                      )
                    }
                  >
                    Remove
                  </button>
                ),
              },
            ]}
          />
        </Card>
      </div>

      {adding ? <AddCertificationModal ctx={ctx} onClose={() => setAdding(false)} /> : null}
    </Page>
  );
}

function AddCertificationModal({ ctx, onClose }) {
  const [row, setRow] = useState({ cert_type: "", issuer: "", expiry_date: "", document_name: "" });
  const set = (key) => (e) => setRow({ ...row, [key]: e.target.value });

  const submit = async () => {
    const ok = await ctx.act(
      () => supplyPost("/supply/api/org/certifications/", row),
      "Certification added."
    );
    if (ok) onClose();
  };

  return (
    <Modal
      title="Add certification"
      onClose={onClose}
      footer={
        <React.Fragment>
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn" onClick={submit} disabled={ctx.busy || !row.cert_type}>
            Add
          </button>
        </React.Fragment>
      }
    >
      <FormRow label="Certification type" hint="e.g. ISO 22000, GMP, UNICEF RUTF approval">
        <input type="text" value={row.cert_type} onChange={set("cert_type")} autoFocus />
      </FormRow>
      <FormRow label="Issuer">
        <input type="text" value={row.issuer} onChange={set("issuer")} />
      </FormRow>
      <FormRow label="Expiry date">
        <input type="date" value={row.expiry_date} onChange={set("expiry_date")} />
      </FormRow>
      <FormRow label="Document reference" hint="Demonstration environment — no file is uploaded.">
        <input type="text" value={row.document_name} onChange={set("document_name")} />
      </FormRow>
    </Modal>
  );
}

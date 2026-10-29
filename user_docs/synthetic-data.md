# Synthetic Data

A **synthetic opportunity** is a fake, labs-only copy of a real one. It looks and behaves like the real thing — workers, cases, visits, photos, reports — but no beneficiary's data is in it.

!!! note "Who this is for"
    Anyone who needs realistic programme data to demo, build or practise with. You do not need to know anything about MCP to read this page, though you do need Claude connected to Labs to make a clone yourself.

---

## What It Is

Labs makes a synthetic opportunity in two moves, both on the server:

1. **It measures the real opportunity.** It reads the real visits once, inside Labs, and writes down only *patterns* — how many cases each worker carries, how a child's weight tends to change between visits, how often a danger sign is recorded, how far apart visits fall. This is called a **profile**, and it holds statistics, not rows.
2. **It generates new data from that profile.** Every case, name, date, answer and measurement in the clone is newly invented to match those patterns. No real case is copied or lightly disguised.

Real visit rows are read on the server during step 1 and never leave it. They never reach Claude, and they are never written into the clone.

Labs keeps a record of which opportunities hold generated data, and that record is what the restricted MCP address checks before it will read anything.

---

## When To Use It

- **Demos.** Walk a funder or a partner through the whole programme loop — reports, audit sessions, tasks, coaching — with nothing real on screen.
- **Building and iterating.** Develop a report, workflow, pipeline or audit setup against data that behaves like the real thing, without working over beneficiary records.
- **Grant and proposal prototyping.** Show what a programme's reporting would look like before any data has been collected.
- **Whenever you are on the restricted MCP address.** On `https://labs.connect.dimagi.com/mcp/no_user_visit/` — the default address, see [Connect MCP](connect-mcp.md) — the tools that read visits work **only** on synthetic opportunities whose data was generated. A clone is how you give Claude something it is allowed to read.

---

## How To Make One

### 1. Connect Claude to Labs

Use the default address, `https://labs.connect.dimagi.com/mcp/no_user_visit/` (the trailing slash matters). [Connect MCP](connect-mcp.md#first-time-setup) has the setup steps, and [Checking Which Address You Are On](connect-mcp.md#checking-which-address-you-are-on) tells you how to confirm which one you are using.

### 2. Ask for the clone

> _"Make a synthetic clone of opportunity 12345."_

Claude runs the `synthetic_clone_opp` tool. You need access to each opportunity you ask it to clone — ask Claude to list the ones you can reach if you are not sure. You can name several at once.

### 3. Wait for the job

Cloning runs as a background job, so it survives a Labs update and does not hold your session open. Claude gets a job reference back straight away and can check on it for you:

> _"How's that clone going?"_

A small opportunity takes a few minutes; a large one up to about twenty. Claude is checking with the `synthetic_job_status` tool, which reports in plain words where the job is ("Step 1 of 2: measuring the real opportunities").

### 4. Use the new ids

When the job finishes you get **new opportunity ids**, filed under a new programme and already visible in your Labs lists. Point your workflows, pipelines and reports at those ids and work normally.

---

## Good To Know

**Asking twice is safe.** If you repeat a request that is already running, Labs hands back the job already in flight rather than starting a second copy of the same work.

**There is no cap on how many opportunities you clone.** The old per-person limits were removed. Jobs run on their own machines, several at a time across the whole system, and the rest wait their turn — so a large batch is slower, not refused. A job that is still running after six hours is stopped and marked failed.

**Old clones may need remaking.** "Generated" is a judgement Labs makes about *how* the data was produced, and the generator has got stricter over time. Clones made before case timelines and numeric measurements were fully modelled still trace back to real cases, so they no longer count as generated — and the restricted address will refuse to read them. If Claude says an opportunity is not one it can read, clone it again. See [What the Restricted Address Blocks](connect-mcp.md#what-the-restricted-address-blocks).

**A synthetic opportunity is not automatically generated data.** Labs can also serve an opportunity from a folder of real production exports. Only data the generator wrote counts, which is why re-cloning is sometimes the answer.

---

## For Engineers

The step-by-step flow (profile and generate as separate calls), the power-user tools, fidelity scoring, demo environments and cohort clones from a YAML spec are documented in the repository, in `docs/SYNTHETIC_OPPS.md` and `docs/synthetic-kmc-clone-runbook.md`. Those tools stay on the full MCP address; the restricted address offers the single `synthetic_clone_opp` flow, which is enough for everything on this page.

---

## Related

- [Connect MCP](connect-mcp.md) — connecting Claude to Labs, the two addresses, and what each can do
- [Reports with Claude](reports-with-claude.md) — building and changing reports with Claude
- [Audit & QA Review](audit.md) — reviewing visits and photos, including on a synthetic opportunity

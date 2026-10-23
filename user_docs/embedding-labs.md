# Embedding Labs in Other Systems

Labs reports can appear **inside another system's pages**, in a frame, with no second sign-in. The first system this is for is CommCare Connect: a Connect page can show a labs workflow, such as a programme's stock review, to whoever is looking at it.

!!! note "Who this is for"
    - **Programme teams** who want a labs report to appear inside Connect: read [What viewers see](#what-viewers-see) and [Getting a report embedded](#getting-a-report-embedded).
    - **Dimagi staff** who register a system that may embed labs: read [Registering an embedding system](#registering-an-embedding-system).
    - **Engineers on the embedding system** (Connect, or another system later): the technical guide is in the developer section, [Embedding labs: integration guide](developers/embedding.md).

---

## What Viewers See

An embedded report is the **same report** people see in labs, drawn by the same code, without the labs menus and header around it.

| Question | Answer |
| --- | --- |
| **Whose data** | The person looking at it. Labs shows them exactly what they could see if they opened the report in labs themselves, no more. Two people looking at the same page can see different things. |
| **Which run** | The newest run of the workflow. A page (a workflow with no runs) shows itself. A workflow that has no runs yet says so. |
| **What they can do** | Read only. An embedded report cannot start a run, save changes, or run the workflow's actions (its action buttons are hidden). To change anything, open the report in labs. |
| **Who can be shown it** | Only people the embedding system signs in, and only reports Dimagi staff have registered for that system. |

!!! info "First-time viewers"
    For now, a person must have **signed in to labs at least once** before an embedded report can be shown to them. Someone who never has sees an error inside the frame, and the embedding system is told why. This limit goes away once Connect adds a small, standard piece on its side (see [What's next](#whats-next)).

---

## How It Works

The embedding system and labs never share a password or a secret key. The embedding system proves who it is with a **digital signature**, and labs checks it against the public key the system registered.

```mermaid
sequenceDiagram
    participant B as Person's browser
    participant C as Embedding system (e.g. Connect)
    participant L as Labs

    B->>C: Opens a Connect page
    C->>L: Signed statement: "this person, this report" (server to server)
    L->>L: Checks the signature, the system, the report, the person's access
    L-->>C: One-time token (valid 2 minutes)
    C-->>B: Page with a labs frame, carrying the token
    B->>L: Frame hands over the token
    L-->>B: Session for this one report, as this person
    B->>L: Reads the report's data, as this person
```

1. When someone opens the page, the embedding system's **server** signs a short statement: who the person is and which labs report to show. It sends the statement to labs.
2. Labs checks:
    - that the signature is genuine;
    - that the system is registered and switched on;
    - that the report is one this system may show;
    - that the person can open it.

    It then answers with a **one-time token** that expires in two minutes.

3. The page shows a labs **frame** and hands it the token. Labs exchanges the token for a session for **that one report**, for **that person**, inside that frame only.
4. Every read the report makes goes to Connect **as that person**, so Connect's own permissions decide what comes back.

---

## Security Model

Embedding is designed so that framing labs never weakens labs.

- **Nothing is embeddable until registered.** Dimagi staff register each embedding system, the exact websites (origins) allowed to frame labs, and the exact reports each may show. Every other labs page still refuses to be framed.
- **No shared secrets.** Labs holds only the embedding system's *public* keys. Anyone who reads labs' copy still cannot sign as that system.
- **The person's own permissions.** An embedded report runs as the person it was opened for, with their own Connect sign-in. Labs also checks, before issuing a token, that the person may open the report.
- **One report, read-only.** The embed session reaches only the reads of the one report it was issued for. It cannot reach any other report, any labs page, the admin, the MCP, or anything that writes. It is never a labs login.
- **Tied to the embedding page.** The session's cookie is *partitioned*: the browser keeps it only for the embedding website. Another website that frames labs has no session to use.
- **Fresh every time.** A token works once, for two minutes. Each frame load needs a new one. A frame never shows one person's view to someone else who opens the page later on the same computer.
- **Recorded.** Issuing a token, starting a session, every refusal, and every read the report makes are written to the labs audit trail, attributed to the person.
- **Can be switched off.** Turning a system off in admin ends its sessions on their next request. Dimagi engineers can also turn all embedding off at once.

---

## Getting a Report Embedded

Ask in **#engineering-connect** with:

1. The **report**: its labs address (for example `https://labs.connect.dimagi.com/labs/workflow/123/run/?opportunity_id=456`). The workflow number and its opportunity or programme come from that address.
2. **Where it should appear**: which system and which page.
3. **Who should see it.** Labs will show each person only what they can already see, so check that the intended viewers have access to the opportunity or programme in Connect.

Dimagi staff then add the report to that system's registration (below), and the embedding system's engineers place the frame on their page.

---

## Registering an Embedding System

*Dimagi staff only. Registering a system, and changing what it may show, needs a labs **superuser**.*

Go to **Django admin → Embedding labs in other systems → Embed clients → Add**.

| Field | What to enter |
| --- | --- |
| **Client id** | A short name with no spaces, such as `connect`. The embedding system puts this in its signed statements. |
| **Name** | A readable name, such as "CommCare Connect (production)". |
| **Active** | Ticked. Unticking ends every session this system issued, on its next request. |
| **Generated data only** | Ticked for demos and the reference example: labs then refuses, at token time, any report that reads anything but generated (synthetic) data, whatever the resources list says. |
| **Identity authority** | Ticked **only for a system labs already trusts to say who people are**. Today that is Connect, because labs accounts come from Connect sign-in. A system without it cannot get tokens yet (see [What's next](#whats-next)). |
| **JWKS URI** *or* **JWKS** | Where the system publishes its public keys (an `https://` address), **or** the keys themselves pasted in as `{"keys": [ ... ]}`. Fill exactly one. Public keys only: labs refuses a key with private parts. |
| **Allowed origins** | The websites allowed to frame labs, as a list of exact origins: `["https://connect.dimagi.com"]`. There is no path and no wildcard. |
| **Resources** | The reports it may show (see below). |
| **Session seconds** | How long a frame's session lasts once opened. Default 3600 (one hour). The minimum is 60 and the maximum is 28800 (eight hours). |

**Resources** is a list. Each entry names a **kind** of thing, its **id**, and the scope it lives in:

```json
[
  {"kind": "workflow", "id": 123, "opportunity_id": 456},
  {"kind": "workflow", "id": 789, "program_id": 25}
]
```

- `kind`: what is embedded. Today the only kind is `workflow`, which also covers pages (a page is a workflow).
- `id`: the workflow's number.
- Exactly one of `opportunity_id` or `program_id`: where the workflow lives. Use what its labs address names.

Labs checks every field when you save and tells you what is wrong.

### Ending sessions

**Django admin → Embed sessions** lists every embed session: who, which report, and when it was issued, started, drawn and expires. Select sessions and choose **End these embed sessions now** to end them immediately.

### Switching all embedding off

Setting the environment variable `LABS_EMBED_ENABLED=false` on the labs deployment switches off every embed endpoint and session at once. It is an emergency switch; the normal way to stop one system is to untick **Active**.

---

## Common Problems

| What someone sees | What it means |
| --- | --- |
| *"That person has not signed in to labs yet."* | Ask them to sign in to labs once at [labs.connect.dimagi.com](https://labs.connect.dimagi.com). |
| *"This person cannot open that workflow."* | They do not have access to the report's opportunity or programme. Check their Connect access, or check that the registration names the right opportunity or programme. |
| The report draws but says an opportunity "is not accessible to your account" | The person can open the report but not everything it reads. That is their real access, applied as it would be in labs. |
| A blank frame | Usually the page's website is not in the system's **Allowed origins**. Engineers: see [Troubleshooting](developers/embedding.md#troubleshooting). |

---

## What's Next

- **Viewers who have never opened labs.** Connect will accept a standard request from labs: "Connect has just told me this is the person; give me their access". After that, a first-time viewer needs no labs sign-in at all.
- **Systems other than Connect.** A system that does not decide who people are will be able to embed labs for people who have linked their labs account to it once (and can unlink it). Separately, a system will be able to show labs reports built on generated, non-personal data to its own users.
- **More kinds of things.** Embedding is built around kinds of resource, so other labs views (supply pages, microplans, Pulse boards) can be added one at a time without changing how embedding works.

The full design, including why it is built this way and how other products (Tableau, Power BI, Superset) do the same, is in the repository at `docs/superpowers/specs/2026-10-23-embedding-labs-design.md`.

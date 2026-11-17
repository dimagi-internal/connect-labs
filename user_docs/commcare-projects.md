# CommCare Projects

Work with the data in a CommCare project space directly, without a Connect login. Sign in with your
CommCare account at **`/hq/`**, add a project, keep its forms and cases up to date, and compute over them.

## Signing in

Choose **Sign in with CommCare**. Labs shows you the projects CommCare says you belong to, and nothing from
Connect. If you also have a Connect login you can use the same pages: connect your CommCare account once and
they open for you.

Your access to a project is always CommCare's word, not labs': it is read from your own CommCare account each
time you sign in, and a project you have left in CommCare stops appearing here.

## Adding a project

On **Your projects**, pick a project from the list and choose **Add**. CommCare must let your account read forms
in it (the *Edit Data* permission). The first person to add a project becomes its admin.

## Keeping the data up to date

Labs reads a project's forms and cases from CommCare and keeps what it read. After the first full read, each
sync asks CommCare only for what changed since the last one, so a large project is not read again from the
beginning. If a sync stops part way (a network problem, CommCare being down) the next one carries on from where
the last one got to.

* **Sync now** starts a background job that brings the project up to date. A labs update does not lose it: it
  carries on afterwards, and a large first read simply continues as further jobs until it is done.
* **Keep it and refresh on a schedule** (admins) refreshes it about every hour. A scheduled sync runs as the
  person who turned it on, using their CommCare sign-in, so the project page always says whose it is. If that
  sign-in stops working, the project says so and any member can attach their own.
* **Re-read everything** (admins) reads the whole project again and also marks anything CommCare has since
  deleted, which a normal sync cannot see. Labs does this by itself about once a week.

Which forms and cases are read can be narrowed to one app, one form or one case type under **What is pulled**.

If a project stops syncing, labs reports it to the team: once when a sync starts failing or needs its owner
to sign in again, and again if a project has not synced for several times its interval.

## Pipelines

A pipeline reads the rows it works on all at once, so one over a very large project (more than 500,000 rows) is
refused with a message asking you to narrow it to one app, form or case type.

A pipeline computes over a project's stored forms or cases, the same way a workflow's pipelines compute over
Connect data. Write the schema as JSON (field paths start `form.` for forms and `case.` for cases, for example
`case.properties.age`), choose what it reads, and **Run** it to see the rows. Results are kept until the project's
data next changes, so running it again is quick.

## Keeping a project's data in your own database

If your organisation needs a project's data to stay in a database you host, an admin can point the project at a
PostgreSQL database under **Where the data is kept**. Labs then writes the project's forms, cases and everything
it computes from them into that database, and **keeps none of it in labs**.

What labs needs from you:

* A database user that owns only the schema labs will use (not a superuser, and not able to create roles or
  databases). Labs creates a handful of tables there and nothing else.
* A server reachable from the internet on a public address, with a certificate labs can verify. Labs refuses
  loopback, link-local and private-network addresses unless Dimagi staff have allowed a private connection for you.
* The password is stored encrypted and is never shown again.

**Check the connection** runs everything labs relies on and tells you which step fails. Moving a project to your
database (or back) starts its read from CommCare again into the new place and removes what was in the old one.

## Workflows

**Open workflows** on a project's page, then **Open the project overview**. It is a dashboard drawn from the forms
the project has stored: the number of forms and workers, and forms per worker with each worker's latest submission.
Only people who belong to the project can open it, and it reads nothing from Connect.

### Writing your own

Any member of a project can make workflow pages of their own, or ask an AI assistant to. A page is a small React
component drawn in the same sandbox as the overview. It reads **pipelines** you save on the project (a pipeline is
a table computed from the project's forms or cases, for example forms per worker, or one row per case), and shows
them however you like: tiles, tables, charts.

With an access token (see below), the tools are `hq_pipeline_save` and `hq_pipeline_run` for the tables, and
`hq_workflow_create`, `hq_workflow_update`, `hq_workflow_get`, `hq_workflow_list`, `hq_workflow_data` and
`hq_workflow_delete` for the pages. `hq_workflow_authoring_guide` explains the shape and gives an example.
Anyone in the project can open and change a page; only its author or a project admin can delete it.

## Using a project from a script or an AI assistant

**Access tokens** (top right) makes a token that lets a script or an MCP client list your projects, read their
stored forms and cases, start a sync and run pipelines. It reaches only the projects you belong to, and nothing
from Connect. Shown once when you make it; revoke it any time.

## What is not here yet

* Workflow runs, saved snapshots and scheduled runs over a CommCare project, and workflows for a project that
  keeps its data in your own database.
* The Data page's SQL explorer over a project's forms and cases.
* A project's users and locations.

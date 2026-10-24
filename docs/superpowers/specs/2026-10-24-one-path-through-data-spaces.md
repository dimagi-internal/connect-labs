# One path through data spaces

Status: built (Jonathan, 2026-10-24: "having two different ways sounds dangerous right? Lets
unify this so everyone goes through dataspace but the dataspace can be generated and not saved
or we can save dataspaces"). Follows `2026-10-24-data-spaces-design.md`.

## The problem

After #2714 the Data page read visit data two ways. With a space open, every call sent
`space_id` and the server opened the space through `spaces.open`. With none open, every call
sent ids (programs, opportunities, a workflow) and `service.select` resolved them. Both
refused anything the person did not hold, but they were two code paths, each with its own
rules about mixing a space with a selection (the page refused any mix; the `data_*` tools
allowed the space's own sources), and every new endpoint or tool had to pick one and get it
right.

## The rule now

**Every read of visit data is over a data space, and only `labsdata/spaces.py` makes one.**

- `spaces.generate(caller, opportunity_ids, program_ids=, workflow=)` makes an **unsaved**
  space: each source resolved as the caller now, a program frozen to the opportunities of it
  they hold now, a workflow to the opportunities it reads now. It is an `OpenSpace` around an
  unsaved `DataSpace` row; nothing is written. `requested` keeps the selection as asked
  (programs as programs), so a link back to the page, or a personal saved query, asks again
  rather than for that call's freeze.
- `spaces.open(caller, space_id)` opens a **saved** space, as before.
- `spaces.from_request(caller, space_id=, opportunity_ids=, program_ids=, workflow=)` is the
  one door the Data page's endpoints and the `data_*` tools use: a `space_id` opens it (and a
  selection beside it must be the space's own sources, else `outside_space` on both
  surfaces); no `space_id` generates one.
- `spaces.save(caller, generated, name)` keeps a generated space; `update_sources(opened,
  generated)` re-freezes a saved one to a generated one's sources.
- `service.describe / query / drill_into / visits / workflows / validate_selected /
  open_on_page_url` take an `OpenSpace` and raise `TypeError` for anything else.
  `Selection` and the resolver (`_resolve`, was `service.select`) moved into `spaces.py`.

`tests/test_spaces_gate.py` pins it: no module on the read path calls `scope.resolve`,
`scope.expand` or `_resolve`, or makes an `OpenSpace`; `spaces.open` and `spaces.generate`
resolve through `_resolve`; every service read refuses ids.

## What a person sees

Nothing changes in what anyone can read. The switcher's no-space entry reads "Unsaved space:
this selection" and its button "Save as a space". On the plain page a program still means the
opportunities of it you hold when you run, since each call generates its space afresh; a
saved space keeps its freeze until someone updates its sources.

## Left as they were

`scope.resolve` still checks a kept result's opportunities when it is read back
(`mcp/kept_results.py`) and names a workflow's opportunities in `data_workflow_context`
(definitions only, no rows). Neither reads visits.

# De-identified exports from data spaces

Status: built (2026-10-26). The design proposal came from a review of a team proposal to use the semantic layer for repeatable de-identified exports. That proposal followed the first one-off export of KMC data to outside experts. Jonathan chose to build it on data spaces, with the app-change check as a manual operation. Builds on `2026-10-24-data-spaces-design.md` and `2026-10-24-one-path-through-data-spaces.md`.

## What it is for

A team wants to share a programme's visit data with outside experts. Nothing that identifies anyone should go, every column should say what it means, and the export should be repeatable rather than a one-off script. The first one-off export taught three lessons:

1. Matching fields by name merged two questions and picked up the wrong visit number. Key by full form path per app version instead.
2. A de-identified data file shipped with a field map whose sample values held real names, phone numbers and GPS points. Check every file, not only the data.
3. Being right on privacy is not enough: the export was wrong on meaning in four places. The dictionary needs plain meanings per app version.

## The rules

1. **One space, one dictionary, one gate.** The dictionary and exports belong to a saved data space and are reached only through `spaces.open`, so the space's rules decide everything: every source held now, then the audience. The routes live in `labsdata/export_views.py`, which `tests/test_spaces_gate.py` reads as a door.
2. **Keyed by form path and deliver unit, per app build.** An entry is one question. Its sources are the exact spellings (`form.danger.Lethargy` and `form.danger.lethargy` normalise to one key), each with its deliver unit, the app builds it appeared in and a visit count. A real rename is a person's call: **merge** two entries. The merged entry takes the stricter setting and goes back to unreviewed.
3. **Drop unless allowed.** An entry is exported only once a person has reviewed it (`dictionary.effective`). A scan suggests a setting from what kind of value the field holds. A reviewer accepts that suggestion or changes it.
4. **The export refuses untagged fields.** Its first pass collects every `(path, deliver unit)` the visits in its window carry. If any is unknown to the dictionary, the export is blocked and names those paths. Someone must scan and review them first.
5. **A scan is structure only, and a person runs it.** It reads the space's cached visits. It holds a bounded sample of values per field in memory, only to judge the kind. It stores paths, deliver units, builds, counts and kinds, never a value (`test_a_scan_stores_no_value`). Its flags flag:
   - new fields
   - new spellings of known fields
   - fields the newest build of a form stopped sending
   - values that now look more sensitive than the setting assumes

   It is the app-change check, and it is deliberately manual: labs has no scheduled job that reads visits without a person (see `CELERY_BEAT_SCHEDULE` on prior audits).
6. **Settings are a manager's.** Changing a setting, meaning, level or the quasi-identifier flag, merging, resolving a flag, and naming a registry are each checked with `spaces.require_manager`. Every change is a `DictionaryChange` row, with the value before and after. A field classified as an identifier or media can never be kept as is. No MCP tool and no page action changes a setting.
7. **Sign-off by someone else.** A built export that passed its checks waits for a manager of the space who did not ask for it. That manager writes a note saying who it is for, and acknowledges any warnings. Only then can the requester or a manager download it, while they can still open the space, for 7 days. After that, `sweep` (hourly) deletes the file. An export nobody signs off is withdrawn after 14 days.

## Settings (`labs/deid/transforms.py`)

| Setting | What it does |
| --- | --- |
| keep | as is |
| drop | absent from the export |
| round | to a step (weights to 25 g) |
| pseudonymise | keyed HMAC-SHA256 code, letters only, per namespace. Every case-id field shares `case`, so a case's visits link across forms and across exports of the same space, and a code cannot be turned back into the id. |
| month | a date cut to YYYY-MM (the default for dates of birth) |
| shift | a date moved by a keyed per-case number of days: intervals within a case survive, calendar dates do not |
| coarsen | a GPS point to 0–2 decimal places (2 is about 1 km); altitude and accuracy are dropped. Three decimals would be a home, and the leak scan fails it. |
| scrub | free text with emails, long numbers and every learned name word removed. Best effort, and the caveats say so. |

The key is `DataDictionary.secret`. It is 32 random bytes per dictionary, and it never leaves labs.

## Checks (all computed while writing, no value kept)

| Check | Result |
| --- | --- |
| row_count | rows written must equal the visits in the window, else **fail** |
| pii_scan | Every output cell is searched for the source's own identifying values: usernames, case names, the values of identifier and id fields, and name words from name fields, all learned in pass 1 and held in memory. It also looks for phone, email, UUID and GPS shapes. A hit fails, except a name word in a column of categories, numbers or dates, which warns (more likely a place). |
| ranges | how many values of each kept numeric column lie far outside its interquartile range (**warn**), and how many values a setting could not read. Counts only: a minimum or maximum is a real value. |
| duplicates | duplicate visit codes, or duplicate rows (**warn**) |
| orphans | visits with no case, when a case column is exported (**warn**) |
| small_groups | rows whose combination of quasi-identifying fields (dates cut to the month) is shared by fewer than 5 cases (**warn**). This is the "combinations of fields" risk, made into a number. |

## What a file holds

- `data.csv`, with formula-leading cells escaped
- `dictionary.csv`: column, meaning, level, setting, params, kind, and sources with builds. No sample value, which is the second lesson above.
- `README.md` with the caveats
- `checks.json`

One build runs at a time: only a queued export moves to building, under a row lock. A build that dies part-way is failed by `sweep` after 3 hours. A failed, blocked or rejected export never keeps a file. Download also requires that every opportunity the file was built from is still one of the space's sources, so taking an opportunity out of a space withdraws its exports.

## Registry lineage (`labsdata/lineage.py`)

For a workflow space, a registry property's lineage is walked backwards: property, then the columns its SQL reads, aggregates, visit columns, Layer 1 pipeline fields, and finally their fallback form paths. Its inherited setting is the strictest at the bottom. A property with any source that is unreviewed, unknown or that the walk cannot place is **unset**, which counts as sensitive. The registry is the workflow's own (`registry_source`), or the dictionary's `registry_id` to try a candidate. Everything is read as the person, through `workflow_context.read_workflow` and `read_pipeline`.

## Case fields (`labsdata/cases.py`)

For a workflow space, **Add as case fields** puts every aggregate and property of the registry in the dictionary as a case-level entry (`@case:<name>`). Each entry records what it inherits from its lineage. Its setting may be that or stricter, never looser (`dictionary.case_allowed`). Anything that inherits `unset` can only be dropped. Re-adding updates what each inherits. A reviewed field whose sources became stricter than its setting goes back to unreviewed, with a flag.

When an export includes any case field, the case statement is compiled at request time, as the person who asked (`cases.compile_sql`): it reads the workflow, its pipelines and its registry with their own token, uses `compile_props_sql` over the opportunities they hold, and is as of the export's end date. The statement is frozen into the export (`SpaceExport.case_query`). The build runs it and writes `cases.csv`: one row per case, keyed by `pseudonym(case id, "case")`, the same code the visit file's case columns carry, so the two files join. Values go through the same transforms and the same leak scan. Like every semantic read, the statement runs as the app over the visit cache. Its opportunities were fixed, as the person, when it was compiled.

## Shared with synthetic cloning (`labs/deid/`)

The identifier, free-text and envelope rules were the synthetic profiler's. They now live in `labs/deid/classify.py`, and the profiler imports them, so a field the cloner refuses to copy is a field an export drops by default. The Data page's field index uses the same form walk (`fieldscan`). The coach charts' peer-name check is in `deid/leaks.py`, next to the export's scanner.

Self-service synthetic data overlaid real forms on "synthetic" visits under the real opportunity's id. It was removed in the same series.

## Tools

These tools give structure and status only:
- `data_dictionary` and `data_exports`: in the `data:read` canopy scope and the no-user-visit-data tool set.
- `data_export_request`: a write. It is not in the canopy scope, so an agent through canopy cannot ask for an export.

There is no tool for settings, sign-off or download.

## Not built (yet)

- Declared value ranges per entry. The ranges check uses each column's own spread.

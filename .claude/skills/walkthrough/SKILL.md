---
name: walkthrough
description: |
  Execute a demo walkthrough spec against the live app and generate a stakeholder-ready
  HTML slideshow. Use when asked to "run the walkthrough", "generate demo slides",
  "walkthrough baobab-demo", or "demo prep".
allowed-tools:
  - Bash
  - Read
  - Write
  - Glob
  - Grep
  - AskUserQuestion
---

# /walkthrough: Demo Walkthrough Generator

Execute a demo spec against the live app using the browse tool. Generate a stakeholder-ready
HTML presentation with screenshots, narrative, and AI quality evaluations.

## Modes

- `/walkthrough <name>` — Execute a walkthrough spec from `docs/walkthroughs/<name>.yaml`
- `/walkthrough generate` — Interactively create a new walkthrough spec (ask what to show, write YAML)

## Setup

### 1. Find the browse binary

```bash
_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
B=""
[ -n "$_ROOT" ] && [ -x "$_ROOT/.claude/skills/gstack/browse/dist/browse" ] && B="$_ROOT/.claude/skills/gstack/browse/dist/browse"
[ -z "$B" ] && B=~/.claude/skills/gstack/browse/dist/browse
if [ -x "$B" ]; then
  echo "READY: $B"
else
  echo "NEEDS_SETUP: run 'cd ~/.claude/skills/browse && ./setup'"
fi
```

### 2. Read the walkthrough spec

```bash
cat docs/walkthroughs/<name>.yaml
```

Parse the YAML to extract: `name`, `narrative`, `base_url`, `auth_url`, `personas`, `scenes`.

### 3. Check for previous run

```bash
SIDECAR="screenshots/walkthroughs/<name>.json"
[ -f "$SIDECAR" ] && cat "$SIDECAR" || echo "NO_PREVIOUS_RUN"
```

If a previous run exists, keep its data for the summary slide comparison.

### 4. Create output directories

```bash
mkdir -p screenshots/walkthroughs
mkdir -p /tmp/walkthrough-screenshots
```

### 5. Authenticate

First check if the CLI token is valid. If expired or missing, ask the user to re-authenticate:

```bash
cd <repo_root>
python manage.py get_cli_token --list-profiles
```

If the desired profile shows `Expired: yes` or is missing, ask the user which profile to use
and have them run the OAuth login interactively (opens browser):

> "Your CLI token for profile `<name>` is expired. Please run this to re-authenticate
> (it will open your browser):"
>
> `! python manage.py get_cli_token --profile <name>`

Wait for the user to confirm they've logged in before proceeding.

Then inject the token into the browse session:

```bash
$B goto <base_url><auth_url>
$B text
```

Verify the response contains `"success": true`. If it shows `"error": "CLI token expired"`,
ask the user to re-run `get_cli_token`.

## Execution

For each scene in the spec, follow this process:

### Scene Execution Pattern

1. **Announce the scene** to the user:
   "Scene {n}/{total}: {title} (as {persona_name})"

2. **Navigate and interact.** Read the `show` field and use your knowledge of the app
   to navigate to the right page and perform the appropriate actions. Key pages:

   - Fund dashboard: `/funder/funds/<fund_id>/`
   - Create solicitation: `/solicitations/create/`
   - Respond to solicitation: `/solicitations/<id>/respond/`
   - Response list: `/solicitations/<id>/responses/`
   - Review form: click "Review" link on response list

3. **Wait for content.** If the page has SSE streaming (fund dashboard, AI features),
   wait for content to finish loading:

   ```bash
   $B wait --networkidle
   ```

4. **Take screenshots.** First neutralize fixed/sticky elements so they don't
   float over content in full-page captures, then screenshot:

   ```bash
   $B js "document.querySelectorAll('*').forEach(function(el){var s=getComputedStyle(el);if(s.position==='fixed'||s.position==='sticky')el.style.position='absolute'})"
   $B screenshot /tmp/walkthrough-screenshots/scene_{n}.png
   ```

5. **Show the screenshot to the user** using the Read tool on the PNG file.

6. **Evaluate EVERY scene.** Be an extremely tough judge. You are evaluating whether
   this is ready to project in front of a stakeholder deciding whether to use this product.

   Read the FULL page text carefully — every word, not just headings:

   ```bash
   $B text
   ```

   Score on 5 dimensions. The overall scene score is the LOWEST of all applicable
   dimensions (weakest link). ALL scenes get scored, not just AI ones.

   **A. Content Quality** (EVERY scene, not just AI):

   For AI scenes: You MUST read the AI output word by word. Do not skim.

   - **Quote the worst sentence** verbatim. If you can't find anything bad, score may be high.
   - **Check for demo data artifacts:** same person/org appearing multiple times as different
     applicants, "Unknown Organization", "None None", identical responses. Any = max 2.
   - **Verify factual claims:** numbers cited by AI must match the actual page data. Wrong = max 3.
   - **Stakeholder smell test:** read as the CEO of Baobab. What makes you raise an eyebrow?

   For non-AI scenes: Check the DATA on the page.

   - Are KPIs populated or showing "loading..."/"—"?
   - Do organization/user names look real or like test data?
   - Are charts populated with meaningful data or empty?
   - Do numbers make sense (e.g., $0 distributed, 0 families)?
   - Is there anything embarrassing a stakeholder would notice?

   Scoring:

   - **5** — All data/content accurate, specific, and impressive. Nothing embarrassing.
   - **4** — Mostly good but one item is slightly off or one field shows placeholder data
   - **3** — Noticeable issues a careful reader would catch (loading states, generic content)
   - **2** — Demo data artifacts, wrong facts, or embarrassing content
   - **1** — Would actively damage credibility

   **B. App Page Quality** — How does the CONNECT LABS PAGE look? (NOT the walkthrough slide)
   This evaluates the actual product being demoed, not the walkthrough HTML.

   - **5** — Professional, polished UI a designer would approve. Clear hierarchy, good spacing.
   - **4** — Good layout but one area feels cramped or unpolished
   - **3** — Functional but looks like a developer tool — dense text, no visual hierarchy
   - **2** — Messy layout, overlapping elements, broken styling
   - **1** — Broken or unusable

   **C. Screenshot Quality** — Is the capture clean and complete?

   - **5** — Clean, properly framed, content starts at top, nothing cut off
   - **4** — Good but slightly cropped or minor framing issue
   - **3** — Content visible but awkwardly framed — header overlap, too much whitespace
   - **2** — Important content missing or wrong scroll position
   - **1** — Wrong page, blank, or mostly empty

   **D. Walkthrough Slide Quality** — How does THIS SLIDE in the deck look?
   This evaluates the walkthrough presentation, not the app.

   - **5** — Screenshot is readable, narration tells the story, persona badge is clear
   - **4** — Good but narration could be more specific or screenshot needs scroll to see key part
   - **3** — Slide works but doesn't highlight the impressive thing about this scene
   - **2** — Screenshot dominates with no clear story, or narration is generic
   - **1** — Slide adds no value — just a raw screenshot dump

   **E. Demo Readiness** — Would you show this to Baobab without apologizing?

   - **5** — Yes, confidently. Clear story, polished look, accurate content.
   - **4** — Yes, with one minor caveat
   - **3** — Maybe, but you'd talk over the rough spots
   - **2** — You'd skip this slide or preface with "still a prototype"
   - **1** — Would hurt credibility

   Write commentary that:

   1. Quotes the worst thing you found (verbatim) — from CONTENT, not styling
   2. Names the WEAKEST dimension and why — be specific
   3. Suggests ONE concrete fix that would have the most impact

   **BLOCKING RULE:** If ANY scene scores 2 or below on Demo Readiness, STOP the
   walkthrough and tell the user:

   > "Scene {n} scored {score}/5 on Demo Readiness — this would hurt the demo.
   > The issue is: {quote the problem}. Recommended fix: {fix}.
   > Should I fix this now before continuing, or skip this scene?"

   Do NOT silently log a 2/5 and keep going. A 2/5 means the slide would embarrass
   you in a meeting — that's a blocker, not a warning. Either fix it or drop it.

7. **Record issues.** If anything goes wrong (element not found, page error, slow load,
   empty state), note it as an issue with severity (error/warning) and description.

8. **Handle failures gracefully.** If a scene can't complete:

   - Screenshot the error state
   - Log the issue
   - Skip to the next scene
   - Partial decks are better than no deck

9. **Flag test data problems.** Before taking a screenshot, check for signs that
   test/sample data doesn't look realistic:
   - Organization names like "Unknown Organization" or "None None"
   - Placeholder usernames like "test-user" or blank names
   - Empty states that should have data (charts with "no data", maps with no markers)
   - IDs or slugs showing instead of human-readable names
     If found, note it as an issue so the user knows the demo won't look right
     with this data.

### Data Collection

As you execute scenes, build a JSON data structure in memory. After all scenes complete,
write it to `/tmp/walkthrough-run-data.json`:

```json
{
  "name": "<from spec>",
  "narrative": "<from spec>",
  "generated_at": "<current ISO timestamp>",
  "duration_seconds": "<elapsed time>",
  "personas": "<from spec>",
  "slides": [
    { "type": "title" },
    { "type": "persona_intro", "persona_key": "<first persona>" },
    {
      "type": "scene",
      "scene_index": 1,
      "scene_total": "<total scenes>",
      "persona_key": "<persona>",
      "title": "<scene title>",
      "narration": "<impressive_because from spec>",
      "screenshot_b64": "<base64 encoded PNG>",
      "ai_evaluation": { "score": 4, "max_score": 5, "commentary": "..." }
    },
    {
      "type": "summary",
      "scenes_completed": "<count>",
      "scenes_total": "<total>",
      "ai_scores": [{ "feature": "<title>", "score": 4, "max_score": 5 }],
      "issues": [{ "scene": 1, "severity": "warning", "description": "..." }],
      "previous_run": "<previous sidecar JSON or null>"
    }
  ]
}
```

**Base64 encoding screenshots:**

```bash
base64 -i /tmp/walkthrough-screenshots/scene_{n}.png
```

**Persona intro slides:** Insert a `persona_intro` slide before the first scene of each persona.

## Generate Presentation

After collecting all data:

```bash
cd <repo_root>
python tools/walkthrough/generate_presentation.py \
  --input /tmp/walkthrough-run-data.json \
  --output screenshots/walkthroughs/<name>.html
```

Then open the result:

```bash
open screenshots/walkthroughs/<name>.html
```

Tell the user:
"Walkthrough complete. HTML deck saved to `screenshots/walkthroughs/<name>.html`.
Open it in your browser to review. Let me know if you want to fix anything and rerun."

## Generate Mode

When invoked as `/walkthrough generate`:

1. Ask the user what feature or demo they want to walk through.
2. Check for an existing design doc with a "Demo Narrative Arc" section:
   ```bash
   grep -rl "Demo Narrative" docs/plans/ docs/designs/ 2>/dev/null
   ```
3. If found, use it as the starting point. If not, ask the user to describe the scenes.
4. For each scene, ask: What persona? What should be shown? What makes it impressive?
5. Write the YAML to `docs/walkthroughs/<name>.yaml`.
6. Offer to execute it immediately.

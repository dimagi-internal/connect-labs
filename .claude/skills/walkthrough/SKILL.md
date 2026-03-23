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

```bash
$B goto <base_url><auth_url>
$B text
```

Verify the response contains `"success": true`.

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

4. **Take screenshots.** Capture the key state:

   ```bash
   $B screenshot /tmp/walkthrough-screenshots/scene_{n}.png
   ```

5. **Show the screenshot to the user** using the Read tool on the PNG file.

6. **Evaluate AI quality** (if the scene has `ai_quality`):

   - Read the AI-generated text from the page:
     ```bash
     $B text
     ```
   - Evaluate the text against the `ai_quality` rubric in the spec.
   - Score 1-5 using this calibration:
     - **5/5** — Specific to context, references concrete details, would impress a stakeholder
     - **4/5** — Good quality, relevant, but missing one specific detail or slightly generic
     - **3/5** — Correct but generic — could apply to any similar program
     - **2/5** — Partially relevant but contains irrelevant or confusing content
     - **1/5** — Wrong, empty, or completely generic boilerplate
   - Write a 1-3 sentence commentary explaining the score.

7. **Record issues.** If anything goes wrong (element not found, page error, slow load,
   empty state), note it as an issue with severity (error/warning) and description.

8. **Handle failures gracefully.** If a scene can't complete:
   - Screenshot the error state
   - Log the issue
   - Skip to the next scene
   - Partial decks are better than no deck

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

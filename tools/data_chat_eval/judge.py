"""An LLM judge per scenario, through the ``claude`` CLI headless (``claude -p``).

It reads the conversation, the cards' text, the ground truth and the deterministic checks,
may look at the screenshots (Read on the scenario's directory), and scores four things
1-5. Deterministic checks gate; the judge's scores inform and its notes become findings.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

RUBRIC = """\
Score each 1-5 (5 best) and be strict: a 5 is something you would show a programme manager unedited.
- correct: the numbers and claims match the ground truth (given below, computed by the harness from
  the same data); a wrong headline number caps this at 2. Not answering caps it at 2.
- clear: a programme manager understands the answer at a glance -- plain words, the takeaway first,
  says what was counted and from what (provenance), no jargon or SQL dumped on them unasked.
- used_page_well: used what the page offers -- read the page/run it is on when relevant, drew a
  results card instead of pasting rows, asked when the question was ambiguous, did not ask when it
  was not.
- card_readable: if a card was drawn, its labels and numbers are readable (plain-word headers,
  formatted numbers, a sensible chart). If no card was needed and none was drawn, score 5; if one
  was needed and none was drawn, score 1.
"""

PROMPT = """\
You are grading an AI data assistant embedded in a web page ("the panel") of Connect Labs, a
tool for running community health programmes. A test harness, acting as a user, asked it the
questions below on synthetic data and recorded what came back.

Scenario: {title}
What it covers: {covers}
Page: {surface} -- {url}
{rubric_extra}
Ground truth (computed by the harness through the same data engine, as the same user):
{truth}

Deterministic checks (pass/fail):
{checks}

The conversation:
{conversation}

Screenshots of the page, the panel and each card are in {shots_dir} (PNG files named
turnN-page.png, turnN-panel.png, turnN-cardM.png). Look at the panel and card screenshots of the
last turn before scoring card_readable.

{rubric}
Reply with ONLY a JSON object, no prose around it:
{{"correct": n, "clear": n, "used_page_well": n, "card_readable": n,
  "verdict": "one sentence on how the answer would land with a programme manager",
  "problems": ["each concrete problem, one sentence, most important first (max 4)"],
  "fix": "the single change most likely to improve this answer, naming where (tool description,
  labs guidance, the agent's skill, the results card, the page)"}}
"""


def available() -> bool:
    return shutil.which("claude") is not None


def judge(scenario, capture, results, shots_dir: Path, model: str | None = None, timeout: int = 300) -> dict:
    prompt = PROMPT.format(
        title=scenario.title,
        covers=scenario.covers or "-",
        surface=scenario.page.surface,
        url=capture.final_url or scenario.page.url,
        rubric_extra=f"Scenario-specific rubric: {scenario.rubric}\n" if scenario.rubric else "",
        truth=json.dumps(scenario.truth, indent=1, default=str) if scenario.truth else "(none)",
        checks="\n".join(
            f"- turn {r['turn']}: {'PASS' if r['passed'] else 'FAIL'} {r['name']} -- {r['detail']}" for r in results
        )
        or "(none)",
        conversation=_conversation(capture),
        shots_dir=shots_dir,
        rubric=RUBRIC,
    )
    cmd = ["claude", "-p", "--output-format", "json", "--allowedTools", "Read", "--add-dir", str(shots_dir)]
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=shots_dir)
    except subprocess.TimeoutExpired:
        return {"error": f"judge timed out after {timeout}s"}
    if proc.returncode != 0:
        return {"error": f"claude exited {proc.returncode}: {proc.stderr[:300]}"}
    try:
        text = json.loads(proc.stdout).get("result", "")
    except json.JSONDecodeError:
        text = proc.stdout
    return _parse(text)


def _parse(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        return {"error": "judge returned no JSON", "raw": text[:500]}
    try:
        out = json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        return {"error": f"judge JSON unreadable: {e}", "raw": text[:500]}
    scores = [out.get(k) for k in ("correct", "clear", "used_page_well", "card_readable")]
    if all(isinstance(s, int | float) for s in scores):
        out["mean"] = round(sum(scores) / len(scores), 2)
    return out


def _conversation(capture) -> str:
    parts = []
    for i, t in enumerate(capture.turns, 1):
        tools = ", ".join(c["short"] + (" (FAILED)" if c.get("is_error") else "") for c in t.tool_calls) or "none seen"
        cards = (
            "\n".join(f"  [card {j}] {c.get('text', '')[:1500]}" for j, c in enumerate(t.cards, 1)) or "  (no card)"
        )
        parts.append(
            f"--- turn {i} ---\nUSER: {t.say}\nTOOLS CALLED: {tools}\nCARDS DRAWN THIS TURN:\n{cards}\n"
            f"ASSISTANT REPLY:\n{t.reply or '(no reply)'}\n"
        )
    return "\n".join(parts)


def _card_text(card: dict) -> str:
    tabs = f"tabs {card.get('tabs')} (open: {card.get('active_tab') or '?'}); " if card.get("tabs") else ""
    chart = (
        "chart drawn; "
        if card.get("svg") or card.get("canvas")
        else ("chart only behind its Chart tab; " if card.get("chart_on_click") else "no chart; ")
    )
    cells = " | ".join(card.get("cells") or [])
    return (tabs + chart + (f"cells: {cells}" if cells else card.get("text", "")))[:1500]

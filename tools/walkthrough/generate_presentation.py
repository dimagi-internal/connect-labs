"""Generate a self-contained HTML slideshow from walkthrough run data.

Usage:
    python -m tools.walkthrough.generate_presentation --input run_data.json --output output.html

No external template engine required — HTML is built with Python string formatting.
"""

import argparse
import html
import json
import os


def _render_stars(score, max_score=5):
    """Render Unicode star rating."""
    filled = "\u2605" * score  # ★
    empty = "\u2606" * (max_score - score)  # ☆
    return f'<span class="stars">{filled}{empty}</span> ({score}/{max_score})'


def _render_score_bar(score, max_score=5):
    """Render a visual score bar with stars."""
    if score >= 5:
        bar_color = "#059669"
    elif score >= 4:
        bar_color = "#2563eb"
    elif score >= 3:
        bar_color = "#d97706"
    else:
        bar_color = "#dc2626"
    pct = int((score / max_score) * 100)
    stars = _render_stars(score, max_score)
    return f"""<div class="score-bar-row">
  <div class="score-bar-bg"><div class="score-bar-fill" style="width:{pct}%;background:{bar_color};"></div></div>
  {stars}
</div>"""


def _render_title_slide(run_data):
    """Render the title slide HTML."""
    name = html.escape(run_data["name"])
    narrative = html.escape(run_data["narrative"])
    generated = html.escape(run_data["generated_at"][:10])
    scene_count = sum(1 for s in run_data["slides"] if s["type"] == "scene")
    persona_count = len(run_data["personas"])
    ai_count = sum(1 for s in run_data["slides"] if s.get("type") == "scene" and s.get("ai_evaluation"))
    return f"""<div class="slide slide-title">
  <div class="title-accent-bar"></div>
  <div class="title-content">
    <h1>{name}</h1>
    <p class="narrative">&#8220;{narrative}&#8221;</p>
    <hr class="title-divider">
    <p class="meta">Generated: {generated} &middot; {scene_count} scenes &middot;
    {persona_count} personas &middot; {ai_count} AI features evaluated</p>
  </div>
</div>"""


def _render_persona_intro(persona_key, personas):
    """Render a persona introduction slide."""
    p = personas[persona_key]
    name = html.escape(p["name"])
    role = html.escape(p["role"])
    intro = html.escape(p["intro"])
    color = html.escape(p["color"])
    initials = "".join(w[0] for w in p["name"].split()[:2])
    return f"""<div class="slide slide-persona" style="background-color: {color}0d;">
  <div class="persona-card">
    <div class="persona-avatar" style="background-color: {color}">{initials}</div>
    <h2>{name}</h2>
    <p class="persona-role">{role}</p>
    <p class="persona-intro">&#8220;{intro}&#8221;</p>
  </div>
</div>"""


def _render_scene_slide(slide, personas, slide_index, total_slides):
    """Render a scene slide with screenshot and optional AI evaluation."""
    persona = personas[slide["persona_key"]]
    p_name = html.escape(persona["name"])
    p_color = html.escape(persona["color"])
    title = html.escape(slide["title"])
    narration = html.escape(slide.get("narration", ""))

    # Progress bar percentage
    scene_slides_total = max(total_slides, 1)
    progress_pct = int((slide_index / scene_slides_total) * 100)

    # Screenshot or placeholder
    if slide.get("screenshot_b64"):
        img_html = f'<img src="data:image/png;base64,{slide["screenshot_b64"]}" alt="{title}" class="screenshot">'
    else:
        error_msg = html.escape(slide.get("error", "Screenshot not captured"))
        img_html = f'<div class="screenshot-placeholder"><p>{error_msg}</p></div>'

    # AI evaluation card
    ai_html = ""
    if slide.get("ai_evaluation"):
        ai = slide["ai_evaluation"]
        stars = _render_stars(ai["score"], ai.get("max_score", 5))
        commentary = html.escape(ai["commentary"])
        ai_html = f"""<div class="ai-quality-card">
    <div class="ai-quality-header">&#10024; AI Quality {stars}</div>
    <p>{commentary}</p>
  </div>"""

    return f"""<div class="slide slide-scene" style="border-top: 3px solid {p_color};">
  <div class="slide-header">
    <span class="persona-badge" style="background-color: {p_color}">{p_name}</span>
    <div class="slide-progress-bar"><div class="slide-progress-fill"
      style="width:{progress_pct}%;background:{p_color};"></div></div>
  </div>
  <h2>{title}</h2>
  <div class="narration-box"><p class="narration">{narration}</p></div>
  {img_html}
  {ai_html}
</div>"""


def _render_summary_slide(slide, run_data):
    """Render the summary slide with scores, issues, and comparison."""
    duration = run_data["duration_seconds"]
    mins, secs = divmod(duration, 60)
    completed = slide["scenes_completed"]
    total = slide["scenes_total"]
    generated = html.escape(run_data["generated_at"][:16].replace("T", " "))

    # Verdict headline based on average AI score
    ai_scores_list = slide.get("ai_scores", [])
    verdict_html = ""
    if ai_scores_list:
        avg = sum(s["score"] for s in ai_scores_list) / len(ai_scores_list)
        if avg >= 4:
            verdict_html = '<p class="verdict verdict-green">Demo Ready &#10003;</p>'
        elif avg >= 3:
            verdict_html = '<p class="verdict verdict-amber">Needs Polish</p>'
        else:
            verdict_html = '<p class="verdict verdict-red">Needs Work</p>'

    # AI scores with visual bars
    scores_html = ""
    for ai in ai_scores_list:
        feature = html.escape(ai["feature"])
        bar = _render_score_bar(ai["score"], ai.get("max_score", 5))
        flag = ' <span class="focus-flag">&larr; needs work</span>' if ai["score"] <= 3 else ""
        scores_html += f"<li><span class='score-feature'>{feature}</span>{bar}{flag}</li>\n"

    # Issues
    issues_html = ""
    for issue in slide.get("issues", []):
        icon = "&#9888;" if issue["severity"] == "warning" else "&#10007;"
        desc = html.escape(issue["description"])
        issues_html += f'<li class="issue-{issue["severity"]}">{icon} Scene {issue["scene"]}: {desc}</li>\n'

    # Previous run comparison
    prev_html = ""
    prev = slide.get("previous_run")
    if prev:
        prev_date = html.escape(prev["generated_at"][:16].replace("T", " "))
        prev_html = f'<h3>Previous Run ({prev_date})</h3><ul class="comparison">'
        prev_scores = {s["feature"]: s["score"] for s in prev.get("ai_scores", [])}
        for ai in ai_scores_list:
            feat = ai["feature"]
            prev_score = prev_scores.get(feat)
            if prev_score is not None:
                if ai["score"] > prev_score:
                    arrow = f'<span class="arrow-up">&#8593;</span> {feat}: {prev_score}&rarr;{ai["score"]}'
                elif ai["score"] < prev_score:
                    arrow = f'<span class="arrow-down">&#8595;</span> {feat}: {prev_score}&rarr;{ai["score"]}'
                else:
                    arrow = f"= {feat}: unchanged"
                prev_html += f"<li>{arrow}</li>"
        prev_html += "</ul>"

    scenes_badge = f'<span class="scenes-badge">Scenes: {completed}/{total} completed</span>'

    return f"""<div class="slide slide-summary">
  <h2>Walkthrough Summary</h2>
  <p class="meta">Run: {generated} | Duration: {mins}m {secs:02d}s</p>
  {scenes_badge}
  {verdict_html}
  {"<h3>AI Quality Scores</h3><ul>" + scores_html + "</ul>" if scores_html else ""}
  {"<h3>Issues Found</h3><ul>" + issues_html + "</ul>" if issues_html else ""}
  {prev_html}
</div>"""


CSS_STYLES = """
/* Reset and base */
*, *::before, *::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 16px;
  line-height: 1.6;
  color: #1f2937;
  background: #f9fafb;
}

/* Slide system */
#presentation {
  width: 100%;
  min-height: 100vh;
}

.slide {
  display: none;
  max-width: 960px;
  margin: 0 auto;
  padding: 3rem 2rem;
  min-height: 100vh;
  background: #ffffff;
  border-left: 1px solid #e5e7eb;
  border-right: 1px solid #e5e7eb;
  opacity: 0;
  transition: opacity 0.2s ease;
}

.slide.active {
  display: block;
}

.slide.fade-in {
  opacity: 1;
}

/* Scene slides get more room for screenshots */
.slide-scene {
  max-width: 1040px;
}

/* Typography */
h1 {
  font-size: 2.5rem;
  font-weight: 700;
  color: #111827;
  margin-bottom: 1rem;
  line-height: 1.2;
}

h2 {
  font-size: 1.75rem;
  font-weight: 600;
  color: #111827;
  margin-bottom: 0.75rem;
}

h3 {
  font-size: 1.25rem;
  font-weight: 600;
  color: #374151;
  margin-top: 1.5rem;
  margin-bottom: 0.5rem;
}

p {
  margin-bottom: 0.75rem;
  color: #374151;
}

ul {
  list-style: none;
  padding-left: 0;
  margin-bottom: 1rem;
}

ul li {
  padding: 0.4rem 0;
  border-bottom: 1px solid #f3f4f6;
}

/* Title slide */
.slide-title {
  display: none;
  align-items: flex-start;
  justify-content: center;
  flex-direction: column;
  padding: 0;
  position: relative;
  overflow: hidden;
}

.slide-title.active {
  display: flex;
}

.slide-title.fade-in {
  opacity: 1;
}

.title-accent-bar {
  width: 100%;
  height: 6px;
  background: linear-gradient(90deg, #2563eb, #7c3aed);
  flex-shrink: 0;
}

.title-content {
  padding: 4rem 2rem 3rem;
  display: flex;
  flex-direction: column;
  justify-content: center;
  flex: 1;
}

.title-divider {
  border: none;
  border-top: 1px solid #e5e7eb;
  margin: 1.5rem 0 1rem;
  max-width: 640px;
}

.slide-title .narrative {
  font-size: 1.25rem;
  font-style: italic;
  color: #6b7280;
  margin: 1rem 0 0;
  max-width: 640px;
}

.slide-title .meta {
  font-size: 0.95rem;
  color: #9ca3af;
  margin-top: 0.25rem;
  margin-bottom: 0;
}

/* Persona slides */
.slide-persona {
  display: none;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  text-align: center;
}

.slide-persona.active {
  display: flex;
}

.slide-persona.fade-in {
  opacity: 1;
}

.persona-card {
  max-width: 560px;
  padding: 3rem;
  background: #ffffff;
  border-radius: 16px;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.08);
}

.persona-avatar {
  width: 100px;
  height: 100px;
  border-radius: 50%;
  color: #ffffff;
  font-size: 2rem;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 1rem;
}

.persona-role {
  font-size: 1rem;
  color: #6b7280;
  font-weight: 500;
  margin-bottom: 1rem;
}

.persona-intro {
  font-style: italic;
  color: #4b5563;
}

/* Scene slides */
.slide-header {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1rem;
  flex-wrap: wrap;
}

.persona-badge {
  display: inline-block;
  padding: 0.25rem 0.75rem;
  border-radius: 9999px;
  color: #ffffff;
  font-size: 0.875rem;
  font-weight: 600;
  flex-shrink: 0;
}

.slide-progress-bar {
  flex: 1;
  height: 3px;
  background: #f3f4f6;
  border-radius: 9999px;
  overflow: hidden;
  min-width: 60px;
}

.slide-progress-fill {
  height: 100%;
  border-radius: 9999px;
  transition: width 0.3s ease;
}

.narration-box {
  background: #f9fafb;
  border-radius: 8px;
  padding: 1rem;
  margin-bottom: 1.25rem;
}

.narration {
  font-size: 1.05rem;
  color: #4b5563;
  margin-bottom: 0;
  font-style: italic;
}

/* Screenshot */
.screenshot {
  max-width: 100%;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  border: 1px solid #e5e7eb;
  display: block;
  margin: 1rem 0;
}

.screenshot-placeholder {
  background: #f3f4f6;
  border: 2px dashed #d1d5db;
  border-radius: 8px;
  min-height: 200px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 1rem 0;
  padding: 2rem;
  text-align: center;
  color: #9ca3af;
  font-size: 0.95rem;
}

/* AI quality card */
.ai-quality-card {
  border-left: 4px solid #2563eb;
  background: #eff6ff;
  border-radius: 0 8px 8px 0;
  padding: 1rem 1.25rem;
  margin-top: 1.25rem;
}

.ai-quality-header {
  font-weight: 600;
  color: #1e40af;
  margin-bottom: 0.5rem;
}

/* Stars */
.stars {
  color: #d97706;
  font-size: 1.1em;
  letter-spacing: 0.05em;
}

/* Focus flag */
.focus-flag {
  color: #dc2626;
  font-size: 0.875rem;
  font-weight: 500;
}

/* Issues */
.issue-warning {
  color: #b45309;
}

.issue-error {
  color: #dc2626;
}

/* Summary slide */
.verdict {
  font-size: 1.5rem;
  font-weight: 700;
  margin: 0.75rem 0 1.25rem;
}

.verdict-green {
  color: #059669;
}

.verdict-amber {
  color: #d97706;
}

.verdict-red {
  color: #dc2626;
}

.scenes-badge {
  display: inline-block;
  background: #f3f4f6;
  border: 1px solid #e5e7eb;
  border-radius: 9999px;
  padding: 0.2rem 0.75rem;
  font-size: 0.875rem;
  color: #374151;
  font-weight: 500;
  margin-bottom: 0.75rem;
}

.score-feature {
  display: block;
  font-weight: 500;
  color: #374151;
  margin-bottom: 0.25rem;
}

.score-bar-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.25rem;
}

.score-bar-bg {
  flex: 1;
  height: 8px;
  background: #f3f4f6;
  border-radius: 4px;
  overflow: hidden;
  min-width: 80px;
  max-width: 200px;
}

.score-bar-fill {
  height: 100%;
  border-radius: 4px;
}

/* Summary slide comparison */
.comparison li {
  color: #374151;
}

.arrow-up {
  color: #059669;
  font-weight: 700;
}

.arrow-down {
  color: #dc2626;
  font-weight: 700;
}

/* Summary meta */
.slide-summary .meta {
  font-size: 0.9rem;
  color: #6b7280;
  margin-bottom: 0.5rem;
}

/* Navigation controls */
#nav-progress-bar {
  position: fixed;
  bottom: 0;
  left: 0;
  width: 100%;
  height: 3px;
  background: #f3f4f6;
  z-index: 99;
}

#nav-progress-bar-fill {
  height: 100%;
  background: #2563eb;
  transition: width 0.2s ease;
}

#nav-controls {
  position: fixed;
  bottom: 1.5rem;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 0.75rem;
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid #e5e7eb;
  border-radius: 9999px;
  padding: 0.5rem 1.25rem;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  z-index: 100;
}

#nav-controls button {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 1.25rem;
  color: #374151;
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  transition: background 0.15s;
}

#nav-controls button:hover {
  background: #f3f4f6;
}

#nav-progress {
  font-size: 0.875rem;
  color: #6b7280;
  min-width: 60px;
  text-align: center;
}

#nav-slide-title {
  font-size: 0.8rem;
  color: #6b7280;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Print styles */
@media print {
  body {
    background: white;
  }

  #nav-controls {
    display: none;
  }

  #nav-progress-bar {
    display: none;
  }

  .slide {
    display: block !important;
    opacity: 1 !important;
    page-break-after: always;
    min-height: auto;
    border: none;
    padding: 2rem;
  }

  .slide:last-child {
    page-break-after: avoid;
  }

  .screenshot {
    max-width: 100%;
    box-shadow: none;
    border: 1px solid #e5e7eb;
  }
}
"""

JS_NAVIGATION = """
(function () {
  var slides = document.querySelectorAll('.slide');
  var totalSlides = slides.length;
  var currentSlide = 0;

  function showSlide(n) {
    if (totalSlides === 0) return;
    // Clamp index
    if (n < 0) n = 0;
    if (n >= totalSlides) n = totalSlides - 1;

    // Deactivate all slides
    for (var i = 0; i < totalSlides; i++) {
      slides[i].classList.remove('active', 'fade-in');
    }

    // Activate target slide
    slides[n].classList.add('active');
    currentSlide = n;

    // Trigger fade-in on next frame
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        slides[n].classList.add('fade-in');
      });
    });

    // Update progress indicator
    var progress = document.getElementById('nav-progress');
    if (progress) {
      progress.textContent = (currentSlide + 1) + ' / ' + totalSlides;
    }

    // Update slide title in nav
    var titleEl = document.getElementById('nav-slide-title');
    if (titleEl) {
      var h2 = slides[n].querySelector('h2');
      var h1 = slides[n].querySelector('h1');
      titleEl.textContent = (h2 && h2.textContent) || (h1 && h1.textContent) || '';
    }

    // Update bottom progress bar
    var barFill = document.getElementById('nav-progress-bar-fill');
    if (barFill) {
      barFill.style.width = Math.round(((currentSlide + 1) / totalSlides) * 100) + '%';
    }

    // Scroll to top of slide
    window.scrollTo(0, 0);
  }

  function prevSlide() {
    showSlide(currentSlide - 1);
  }

  function nextSlide() {
    showSlide(currentSlide + 1);
  }

  // Keyboard navigation
  document.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault();
      nextSlide();
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault();
      prevSlide();
    }
  });

  // Build bottom progress bar
  var progressBar = document.createElement('div');
  progressBar.id = 'nav-progress-bar';
  var progressBarFill = document.createElement('div');
  progressBarFill.id = 'nav-progress-bar-fill';
  progressBar.appendChild(progressBarFill);
  document.body.appendChild(progressBar);

  // Build nav controls
  var nav = document.createElement('div');
  nav.id = 'nav-controls';

  var prevBtn = document.createElement('button');
  prevBtn.textContent = '\\u2190';
  prevBtn.title = 'Previous slide (ArrowLeft)';
  prevBtn.addEventListener('click', prevSlide);

  var progress = document.createElement('span');
  progress.id = 'nav-progress';

  var slideTitle = document.createElement('span');
  slideTitle.id = 'nav-slide-title';

  var nextBtn = document.createElement('button');
  nextBtn.textContent = '\\u2192';
  nextBtn.title = 'Next slide (ArrowRight)';
  nextBtn.addEventListener('click', nextSlide);

  nav.appendChild(prevBtn);
  nav.appendChild(progress);
  nav.appendChild(slideTitle);
  nav.appendChild(nextBtn);
  document.body.appendChild(nav);

  // Initialize
  showSlide(0);
})();
"""


def generate(run_data, output_path):
    """Generate HTML slideshow and JSON sidecar from run data."""
    slides_html_parts = []
    personas = run_data["personas"]
    total_slides = len(run_data["slides"])
    seen_personas = set()
    slide_index = 0

    for slide in run_data["slides"]:
        slide_index += 1
        if slide["type"] == "title":
            slides_html_parts.append(_render_title_slide(run_data))
        elif slide["type"] == "persona_intro":
            seen_personas.add(slide["persona_key"])
            slides_html_parts.append(_render_persona_intro(slide["persona_key"], personas))
        elif slide["type"] == "scene":
            slides_html_parts.append(_render_scene_slide(slide, personas, slide_index, total_slides))
        elif slide["type"] == "summary":
            slides_html_parts.append(_render_summary_slide(slide, run_data))

    slides_html = "\n".join(slides_html_parts)
    page_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(run_data["name"])}</title>
<style>{CSS_STYLES}</style>
</head>
<body>
<div id="presentation">{slides_html}</div>
<script>{JS_NAVIGATION}</script>
</body>
</html>"""

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(page_html)

    # Write JSON sidecar for run history comparison
    sidecar_path = os.path.splitext(output_path)[0] + ".json"
    summary_slide = next((s for s in run_data["slides"] if s["type"] == "summary"), {})
    sidecar = {
        "name": run_data["name"],
        "generated_at": run_data["generated_at"],
        "duration_seconds": run_data["duration_seconds"],
        "scenes_completed": summary_slide.get("scenes_completed", 0),
        "scenes_total": summary_slide.get("scenes_total", 0),
        "ai_scores": summary_slide.get("ai_scores", []),
        "issues": summary_slide.get("issues", []),
    }
    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump(sidecar, f, indent=2)


def main(argv=None):
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Generate walkthrough HTML presentation")
    parser.add_argument("--input", required=True, help="Path to JSON run data")
    parser.add_argument("--output", required=True, help="Path to write HTML")
    args = parser.parse_args(argv)

    with open(args.input, encoding="utf-8") as f:
        run_data = json.load(f)

    generate(run_data, args.output)


if __name__ == "__main__":
    main()

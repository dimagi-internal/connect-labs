"""Program Admin Report grid helpers.

The PAR detail page renders a `<div>`-based grid (not a `<table>`) with
one row per watched opportunity and one column per ISO week. Clicking a
cell expands an inline detail panel. The render code marks the label
cell with ``style.fontWeight === '600'`` and the clickable inner cell
with ``style.cursor: pointer``.

These helpers find a cell by its opp label + week index and click it
through ``page.mouse``, so the synthetic cursor overlay animates between
clicks (a ``locator.click()`` warps the underlying cursor but skips the
overlay's mousemove handler).

Lives in ``_lib/`` rather than per-walkthrough because the PAR grid
shape is shared by every demo that drills into a multi-opp report —
including future walkthroughs that watch the same grid.
"""

from __future__ import annotations

from playwright.sync_api import Page

from .recorder import slow_move


def find_cell_position(page: Page, opp_label: str, week_idx: int) -> dict | None:
    """Return ``{x, y}`` of the center of the (opp_label, week_idx) cell.

    Matches ``opp_label`` as a prefix of the cell's label text (so
    "Northern" finds "Northern Cluster" without the caller knowing the
    suffix). Returns None if the grid hasn't rendered yet.
    """
    return page.evaluate(
        """({opp_label, week_idx}) => {
            const labels = Array.from(document.querySelectorAll('div')).filter(d => {
                return d.style && d.style.fontWeight === '600'
                    && d.textContent.startsWith(opp_label);
            });
            if (labels.length === 0) return null;
            const labelCell = labels[0].closest('div[style*="border"]');
            const grid = labelCell ? labelCell.parentElement : null;
            if (!grid) return null;
            const cells = Array.from(grid.children);
            // cells[0] is the label cell; cells[1+] are weeks in order.
            const cell = cells[1 + week_idx];
            if (!cell) return null;
            const inner = cell.querySelector('[style*="cursor: pointer"]') || cell;
            const rect = inner.getBoundingClientRect();
            return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
        }""",
        {"opp_label": opp_label, "week_idx": week_idx},
    )


def click_cell(page: Page, opp_label: str, week_idx: int, *, pre_dwell_s: float = 0.4) -> bool:
    """Slow-move the cursor to a grid cell and click it.

    Returns True on success, False if the cell wasn't found (caller
    typically should scene-snap to log what was there instead).
    """
    pos = find_cell_position(page, opp_label, week_idx)
    if not pos:
        print(f"  ! no cell {opp_label} wk{week_idx}")
        return False
    slow_move(page, pos["x"], pos["y"])
    page.wait_for_timeout(int(pre_dwell_s * 1000))
    page.mouse.click(pos["x"], pos["y"])
    page.wait_for_timeout(500)
    return True

"""Shared scaffolding for synthetic-data walkthrough recordings.

A walkthrough is a three-stage pipeline:

    config (JSON)  →  synthetic generator  →  Playwright recorder  →  video/deck

This package owns the generic, walkthrough-agnostic pieces of stages 2-4
so a new walkthrough only has to provide its config + a thin recording
script that lists its scenes.

Modules:
    config       — env + .run_ids.json plumbing (session path, run ids).
    recorder     — RecorderSession context manager + Playwright primitives
                   (slow_move, click_text, wait_for_text, snap, …).
    grid         — PAR-style grid helpers (find_cell_position, click_cell).
    discovery    — PAR snapshot walker (find_drill_targets).
    verify       — pre-record smoke checks (no orphan audits, expected
                   archetype counts, …).
    concat       — ffmpeg wrapper for video concatenation.
"""

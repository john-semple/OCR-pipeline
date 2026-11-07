#!/usr/bin/env python3
"""OCR pipeline entry point.

Usage:
    python3 run.py [--dry-run] [--stage0-only] [--watch]

Default: runs the full pipeline (Stages 0→5) once and exits.

--dry-run      Hash and report, but don't move anything or call the API.
--stage0-only  Stop after Stage 0 (collect + dedup only, no API calls).
--watch        Run the full pipeline, then keep watching the inbox for
               new PDFs. Triggers a new run instantly when Syncthing
               drops a file (debounced 10s). Blocks forever — manage
               with systemd (Restart=always). Replaces the 10-min timer.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import load_config
from src.logging_setup import get_logger
from src.stage0 import collect
from src.stage1 import MistralOCREngine
from src.stage1_runner import process_pending
from src.stage2 import extract_meta
from src.stage3 import group_results, classify_groups
from src.stage4 import write_notes
from src.stage5 import archive_and_log
from src.llm_fallback import MistralLLMClassifier
from src.alias_readme import generate_alias_readme
from src.classes_md import ensure_class_folders, prune_class_folders
from src.watcher import Watcher
from src.resort import resort_unsorted

log = get_logger("run")


def run_pipeline(cfg) -> int:
    """Run the full pipeline once. Returns 0 on success."""

    # --- Stage 0: collect + dedup + move to Processing/ ---
    files = collect(cfg, dry_run=False)
    log.info("stage0 collected %d file(s)", len(files))
    for f in files:
        log.info("  %s  %d B  <- %s", f.sha256[:12], f.size, f.source.name)

    # Always regenerate the alias README (cheap, keeps it current).
    generate_alias_readme(cfg)

    # --- Stage 1: OCR every PDF in Processing/ ---
    try:
        engine = MistralOCREngine()
    except Exception as exc:
        log.error("cannot start OCR engine: %s", exc)
        return 1

    results = process_pending(cfg, engine)
    for r in results:
        log.info("  ocr ok  %s  %d page(s)  title=%r",
                 r.sha256[:12], r.num_pages, r.title[:60])

    if not results:
        log.info("no OCR results, pipeline complete")
        return 0

    # --- Stage 2: extract date + capture_time from each result ---
    metas = []
    for r in results:
        meta = extract_meta(r)
        metas.append(meta)
        log.info("  meta    %s  date=%s  source=%s  review=%s",
                 r.sha256[:12], meta.date, meta.date_source, meta.needs_review)

    # --- Stage 3a: group results into notes ---
    groups = group_results(results, metas, cfg)
    for g in groups:
        log.info("  group   %s  title=%r  pages=%d  multi=%s",
                 g.group_id[:8], g.normalized_title[:40], len(g.pages), g.is_multi_page)

    # --- Stage 3b: classify each group (alias-map + LLM fallback) ---
    llm = None
    try:
        llm = MistralLLMClassifier()
        log.info("LLM fallback enabled (mistral-small-latest)")
    except Exception as exc:
        log.warning("LLM fallback disabled: %s", exc)

    classified = classify_groups(groups, cfg, llm=llm)
    for g, c in classified:
        via = c.matched_via or "miss"
        log.info("  class   %s  %s -> %s%s",
                 g.group_id[:8], g.normalized_title[:40],
                 c.class_name,
                 f" ({via})" if c.is_hit else " [Unsorted]")

    # --- Stage 4: write notes + diagrams to the vault ---
    metas_map = {r.sha256: m for r, m in zip(results, metas)}
    ocr_map = {r.sha256: r for r in results}
    note_paths = write_notes(classified, metas_map, ocr_map, cfg)
    for p in note_paths:
        log.info("  note    %s", p.relative_to(cfg.paths.vault_root))

    # --- Stage 5: archive scans + OCR JSON, log spend, clean up ---
    archived = archive_and_log(classified, metas_map, ocr_map, cfg)
    for p in archived:
        log.info("  archive %s", p.name)

    log.info("pipeline complete: %d note(s) written, %d scan(s) archived",
             len(note_paths), len(archived))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR pipeline runner")
    parser.add_argument("--dry-run", action="store_true", help="Don't modify any files")
    parser.add_argument("--stage0-only", action="store_true", help="Stop after Stage 0")
    parser.add_argument("--watch", action="store_true", help="Watch inbox for new files (blocks forever)")
    parser.add_argument("--resort", action="store_true", help="Re-sort notes in Unsorted/ against current aliases")
    args = parser.parse_args()

    cfg = load_config()
    log.info("vault_root=%s", cfg.paths.vault_root)
    log.info("openscan_inbox=%s", cfg.paths.openscan_inbox)
    log.info("processing=%s", cfg.paths.processing)

    # Auto-create class folders from Classes.md (no-ops if they exist);
    # prune empty folders of removed classes.
    ensure_class_folders(cfg.classes)
    prune_class_folders(cfg.classes, cfg.paths.homework_root)

    # --- Resort mode: re-sort Unsorted/ and exit ---
    if args.resort:
        llm = None
        try:
            llm = MistralLLMClassifier()
        except Exception as exc:
            log.warning("LLM fallback disabled: %s", exc)
        moved = resort_unsorted(cfg, llm=llm)
        generate_alias_readme(cfg)
        for path, class_name in moved:
            log.info("  moved   %s -> %s", path.relative_to(cfg.paths.vault_root), class_name)
        log.info("resort complete: %d note(s) moved", len(moved))
        return 0

    # --- Stage 0 only (dry-run or --stage0-only) ---
    if args.dry_run or args.stage0_only:
        files = collect(cfg, dry_run=args.dry_run)
        log.info("stage0 collected %d file(s)", len(files))
        for f in files:
            log.info("  %s  %d B  <- %s", f.sha256[:12], f.size, f.source.name)
        if not args.dry_run:
            generate_alias_readme(cfg)
        return 0

    # --- Full pipeline run ---
    run_pipeline(cfg)

    # --- Watch mode: keep running on new files ---
    if args.watch:
        def watched_run():
            # Reload config each run so Classes.md edits (new classes,
            # new keywords) are picked up without restarting the watcher.
            cfg = load_config()
            ensure_class_folders(cfg.classes)
            prune_class_folders(cfg.classes, cfg.paths.homework_root)
            run_pipeline(cfg)

        def classes_changed():
            # Light action on Classes.md edits: reload classes, create
            # any new folders, prune empty folders of removed classes,
            # regenerate the Sorting Guide. No OCR, no pipeline run.
            cfg = load_config()
            log.info("classes updated: %d class(es) loaded", len(cfg.classes))
            ensure_class_folders(cfg.classes)
            prune_class_folders(cfg.classes, cfg.paths.homework_root)
            generate_alias_readme(cfg)

        watcher = Watcher(cfg, callback=watched_run, classes_callback=classes_changed)
        watcher.start()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
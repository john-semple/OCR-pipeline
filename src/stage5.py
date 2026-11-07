"""Stage 5 — Archive original scans + raw OCR JSON, log spend, clean up.

After Stage 4 writes the note to the vault, Stage 5:
  1. Moves the original scan PDF from Processing/ to Archive/.
  2. Saves the raw OCR JSON (the full Mistral response) to Archive/.
  3. Appends one line per page to `logs/spend.csv` for cost tracking.
  4. Removes the processed PDF and its `.name` sidecar from Processing/.
  5. Removes the group JSON file from Processing/groups/.

Failed OCR files (left in Processing/ by Stage 1) are NOT touched —
they stay for a retry on the next run.
"""

from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .logging_setup import get_logger
from .stage1 import OCRResult
from .stage2 import NoteMeta
from .stage3 import NoteGroup, Classification
from .stage4 import _slugify

log = get_logger("stage5")

# Mistral OCR cost: ~$4 / 1000 pages = $0.004 / page.
COST_PER_PAGE = 0.004

SPEND_CSV_HEADER = ["timestamp", "scan_hash", "class", "pages", "estimated_cost_usd"]


def archive_and_log(
    classified: list[tuple[NoteGroup, Classification]],
    metas: dict[str, NoteMeta],
    ocr_results: dict[str, OCRResult],
    cfg: Config,
) -> list[Path]:
    """Archive scans + OCR JSON, append to spend log, clean up Processing/.

    Returns the list of archived PDF paths.
    """
    cfg.paths.archive.mkdir(parents=True, exist_ok=True)
    # spend_log is relative to the repo root, not the vault.
    from .config import REPO_ROOT
    spend_log_path = REPO_ROOT / cfg.settings.get("spend_log", "logs/spend.csv")
    spend_log_path.parent.mkdir(parents=True, exist_ok=True)

    archived: list[Path] = []
    spend_rows: list[dict] = []

    for group, classification in classified:
        for page_ref in group.pages:
            sha = page_ref.sha256
            result = ocr_results.get(sha)
            if result is None:
                log.warning("stage5: no OCR result for %s, skipping archive", sha[:12])
                continue

            meta = metas.get(sha)
            if meta is None:
                log.warning("stage5: no meta for %s, skipping archive", sha[:12])
                continue

            # Build archive filenames: {date}-{slug}-{pN}.{ext}
            date_str = meta.date
            source_slug = _slugify(page_ref.source_name or sha)
            base_name = f"{date_str}-{source_slug}"

            # 1. Move the PDF to Archive/.
            pdf_src = result.pdf_path
            pdf_dest = cfg.paths.archive / f"{base_name}.pdf"
            if pdf_src.exists():
                shutil.move(str(pdf_src), str(pdf_dest))
                archived.append(pdf_dest)
                log.info("stage5: archived %s -> %s", pdf_src.name, pdf_dest.name)

            # 2. Save raw OCR JSON to Archive/.
            json_dest = cfg.paths.archive / f"{base_name}.ocr.json"
            json_dest.write_text(
                json.dumps(result.raw_json, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            log.info("stage5: saved OCR JSON %s", json_dest.name)

            # 3. Remove the .name sidecar from Processing/.
            name_sidecar = cfg.paths.processing / f"{sha}.name"
            if name_sidecar.exists():
                name_sidecar.unlink()

            # 4. Append to spend log.
            num_pages = result.num_pages
            cost = num_pages * COST_PER_PAGE
            spend_rows.append({
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "scan_hash": f"sha256:{sha}",
                "class": classification.class_name,
                "pages": num_pages,
                "estimated_cost_usd": f"{cost:.4f}",
            })

    # 5. Write spend log (append, create if new).
    _append_spend_log(spend_log_path, spend_rows)

    # 6. Remove group JSON files (they're no longer needed after archiving).
    for group, _ in classified:
        group_file = cfg.paths.groups / f"{group.group_id}.json"
        if group_file.exists():
            group_file.unlink()
            log.info("stage5: removed group file %s", group_file.name)

    log.info("stage5: archived %d scan(s), logged %d spend row(s)", len(archived), len(spend_rows))
    return archived


def _append_spend_log(path: Path, rows: list[dict]) -> None:
    """Append rows to the spend CSV. Writes a header if the file is new."""
    file_exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SPEND_CSV_HEADER)
        if not file_exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)
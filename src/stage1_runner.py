"""Stage 1 orchestrator — run OCR on every PDF in Processing/.

Takes the list of files collected by Stage 0 (already sitting in
Processing/ as `{sha256}.pdf`), sends each through an OCREngine, and
returns a list of OCRResults. Files that fail OCR are moved to the
Failed/ folder for manual retry — the user drags them into
Manual Retry/ to re-run them through the pipeline.

The original filename (e.g. "OpenScan 20260831 150515450040.pdf") is
read from a `{sha256}.name` sidecar file written by Stage 0, so Stage 2
can later parse the capture timestamp from the OpenScan filename.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import Config
from .logging_setup import get_logger
from .stage1 import OCREngine, OCRError, OCRResult

log = get_logger("stage1run")


def _read_source_name(processing: Path, sha: str) -> str:
    sidecar = processing / f"{sha}.name"
    if sidecar.exists():
        return sidecar.read_text(encoding="utf-8").strip()
    return ""


def _move_to_failed(pdf: Path, cfg: Config) -> None:
    """Move a failed PDF + its .name sidecar to the Failed/ folder."""
    cfg.paths.failed.mkdir(parents=True, exist_ok=True)
    sha = pdf.stem
    dest = cfg.paths.failed / pdf.name
    shutil.move(str(pdf), str(dest))
    sidecar = cfg.paths.processing / f"{sha}.name"
    if sidecar.exists():
        shutil.move(str(sidecar), str(cfg.paths.failed / sidecar.name))
    log.info("stage1: moved failed %s -> Failed/", pdf.name)


def process_pending(cfg: Config, engine: OCREngine) -> list[OCRResult]:
    """OCR every PDF in Processing/. Returns results in file-name order.

    Failed files are moved to Failed/ for manual retry.
    """
    pdfs = sorted(p for p in cfg.paths.processing.iterdir()
                  if p.is_file() and p.suffix.lower() == ".pdf")

    if not pdfs:
        log.info("stage1: no PDFs in Processing/")
        return []

    results: list[OCRResult] = []
    for pdf in pdfs:
        sha = pdf.stem  # filename is {sha256}.pdf
        source_name = _read_source_name(cfg.paths.processing, sha)
        try:
            result = engine.process(pdf, sha, source_name)
            results.append(result)
        except OCRError as exc:
            log.error("stage1: FAILED %s: %s (moved to Failed/)", pdf.name, exc)
            _move_to_failed(pdf, cfg)

    log.info("stage1: %d/%d succeeded", len(results), len(pdfs))
    return results
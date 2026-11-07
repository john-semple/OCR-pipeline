"""Stage 2 — Extract metadata from an OCRResult + its source file.

Produces a `NoteMeta` with:
  - `date`: YYYY-MM-DD, from the OpenScan filename timestamp first,
    then PDF creation metadata, then file ctime (with needs_review=True).
  - `capture_time`: epoch seconds (for multi-page grouping in Stage 3).
  - `needs_review`: True if the date source was the ctime fallback.

Priority order (per docs/decisions.md "Date source"):
  1. OpenScan filename (two formats, see OPENSCAN_RE / OPENSCAN_RE_V2 below)
  2. PDF creation metadata (via `pdfinfo` — no CreationDate for OpenScan,
     but kept for renamed/external PDFs)
  3. File ctime — last resort, sets needs_review=True
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .logging_setup import get_logger
from .stage1 import OCRResult

log = get_logger("stage2")

# OpenScan filename formats:
#   V1 (older app): "OpenScan 20260831 150515450040.pdf"
#     group 1 = YYYYMMDD, group 2 = HHMMSS, group 3 = microseconds (6 digits)
#   V2 (app update ~2026-09): "OpenScan 2026-09-16-1789585922072.pdf"
#     group 1 = YYYY-MM-DD, group 2 = epoch milliseconds (13 digits).
#     The epoch ms is capture_time directly (no tz conversion — OpenScan
#     uses the phone's local clock, and we treat it as naive UTC).
OPENSCAN_RE = re.compile(r"^OpenScan (\d{8}) (\d{6})(\d{6})\.pdf$", re.IGNORECASE)
OPENSCAN_RE_V2 = re.compile(
    r"^OpenScan (\d{4}-\d{2}-\d{2})-(\d{13})\.pdf$", re.IGNORECASE
)


@dataclass
class NoteMeta:
    """Metadata extracted for one OCR result, ready for frontmatter."""

    date: str             # YYYY-MM-DD
    capture_time: float   # epoch seconds (UTC — OpenScan uses local time on
                          # the phone; we treat it as naive, no tz conversion)
    date_source: str      # "filename" | "pdf_metadata" | "ctime"
    needs_review: bool


def extract_meta(result: OCRResult) -> NoteMeta:
    """Extract date + capture_time from an OCRResult.

    Tries the OpenScan filename first (via result.source_name), then PDF
    metadata, then file ctime.
    """
    # 1. OpenScan filename
    if result.source_name:
        meta = _try_filename(result.source_name)
        if meta:
            log.info("stage2: date=%s from filename (%s)", meta.date, result.source_name)
            return meta

    # 2. PDF metadata
    meta = _try_pdf_metadata(result.pdf_path)
    if meta:
        log.info("stage2: date=%s from PDF metadata (%s)", meta.date, result.pdf_path.name)
        return meta

    # 3. File ctime
    meta = _from_ctime(result.pdf_path)
    log.info("stage2: date=%s from ctime (%s) — needs_review=True", meta.date, result.pdf_path.name)
    return meta


def _try_filename(source_name: str) -> NoteMeta | None:
    # V1: "OpenScan 20260831 150515450040.pdf"
    m = OPENSCAN_RE.match(source_name)
    if m:
        date_part, time_part, micros = m.group(1), m.group(2), m.group(3)
        dt = datetime.strptime(date_part + time_part + micros, "%Y%m%d%H%M%S%f")
        return NoteMeta(
            date=dt.strftime("%Y-%m-%d"),
            capture_time=dt.timestamp(),
            date_source="filename",
            needs_review=False,
        )
    # V2: "OpenScan 2026-09-16-1789585922072.pdf"
    m = OPENSCAN_RE_V2.match(source_name)
    if m:
        date_part, epoch_ms = m.group(1), int(m.group(2))
        return NoteMeta(
            date=date_part,
            capture_time=epoch_ms / 1000.0,
            date_source="filename",
            needs_review=False,
        )
    return None


def _try_pdf_metadata(pdf_path: Path) -> NoteMeta | None:
    """Try `pdfinfo` for a CreationDate. Returns None if not found."""
    try:
        out = subprocess.run(
            ["pdfinfo", str(pdf_path)],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    for line in out.stdout.splitlines():
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip()
        if key == "creationdate" and val:
            # pdfinfo format: "D:YYYYMMDDHHmmSS" or similar
            dt = _parse_pdf_date(val)
            if dt:
                return NoteMeta(
                    date=dt.strftime("%Y-%m-%d"),
                    capture_time=dt.timestamp(),
                    date_source="pdf_metadata",
                    needs_review=False,
                )
    return None


def _parse_pdf_date(val: str) -> datetime | None:
    """Parse a PDF creation date string (e.g. D:20260831150515)."""
    val = val.removeprefix("D:")
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y%m%d"):
        try:
            return datetime.strptime(val[:len(fmt) + 2], fmt)
        except ValueError:
            continue
    return None


def _from_ctime(pdf_path: Path) -> NoteMeta:
    stat = pdf_path.stat()
    dt = datetime.fromtimestamp(stat.st_ctime)
    return NoteMeta(
        date=dt.strftime("%Y-%m-%d"),
        capture_time=dt.timestamp(),
        date_source="ctime",
        needs_review=True,
    )
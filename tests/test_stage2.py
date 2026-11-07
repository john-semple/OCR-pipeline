"""Tests for Stage 2 — date/capture_time extraction.

Tests the three priority levels:
  1. OpenScan filename timestamp
  2. PDF metadata (via pdfinfo subprocess)
  3. File ctime (fallback, sets needs_review=True)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.stage1 import OCRResult
from src.stage2 import extract_meta, _try_filename, OPENSCAN_RE, OPENSCAN_RE_V2


def make_result(pdf_path: Path, source_name: str = "") -> OCRResult:
    return OCRResult(
        pdf_path=pdf_path,
        sha256="abc123",
        source_name=source_name,
        pages=[],
        title="",
        raw_json={},
    )


def test_openscan_regex_matches_real_filenames():
    assert OPENSCAN_RE.match("OpenScan 20260831 150515450040.pdf")
    assert OPENSCAN_RE.match("OpenScan 20260831 154515171987.pdf")
    assert OPENSCAN_RE.match("openscan 20260831 150515450040.pdf")  # case insensitive


def test_openscan_v2_regex_matches_real_filenames():
    # New format from OpenScan app update (~2026-09): "OpenScan YYYY-MM-DD-<epoch_ms>.pdf"
    assert OPENSCAN_RE_V2.match("OpenScan 2026-09-16-1789585922072.pdf")
    assert OPENSCAN_RE_V2.match("OpenScan 2026-09-16-1789586015125.pdf")
    assert OPENSCAN_RE_V2.match("openscan 2026-09-16-1789585922072.pdf")  # case insensitive


def test_openscan_v2_regex_rejects_non_matching():
    assert not OPENSCAN_RE_V2.match("OpenScan 20260831 150515450040.pdf")  # V1
    assert not OPENSCAN_RE_V2.match("OpenScan 2026-09-16.pdf")             # no epoch
    assert not OPENSCAN_RE_V2.match("notes.pdf")


def test_try_filename_v2_extracts_date_and_time():
    meta = _try_filename("OpenScan 2026-09-16-1789585922072.pdf")
    assert meta is not None
    assert meta.date == "2026-09-16"
    assert meta.date_source == "filename"
    assert meta.needs_review is False
    # epoch ms 1789585922072 -> 1789585922.072 seconds
    assert meta.capture_time == 1789585922072 / 1000.0


def test_openscan_regex_rejects_non_matching():
    assert not OPENSCAN_RE.match("scan 20260831.pdf")
    assert not OPENSCAN_RE.match("OpenScan 20260831.pdf")
    assert not OPENSCAN_RE.match("notes.pdf")


def test_try_filename_extracts_date_and_time():
    meta = _try_filename("OpenScan 20260831 150515450040.pdf")
    assert meta is not None
    assert meta.date == "2026-08-31"
    assert meta.date_source == "filename"
    assert meta.needs_review is False
    assert meta.capture_time > 0


def test_extract_meta_prefers_filename(tmp_path):
    pdf = tmp_path / "hash.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    result = make_result(pdf, source_name="OpenScan 20260831 150515450040.pdf")

    meta = extract_meta(result)

    assert meta.date == "2026-08-31"
    assert meta.date_source == "filename"
    assert meta.needs_review is False


def test_extract_meta_falls_back_to_ctime(tmp_path):
    """No filename match + no PDF metadata -> ctime fallback."""
    pdf = tmp_path / "hash.pdf"
    pdf.write_bytes(b"%PDF-1.4 not a real pdf")
    result = make_result(pdf, source_name="renamed_file.pdf")

    meta = extract_meta(result)

    assert meta.date_source == "ctime"
    assert meta.needs_review is True
    assert meta.capture_time > 0


def test_extract_meta_empty_source_name_uses_ctime(tmp_path):
    pdf = tmp_path / "hash.pdf"
    pdf.write_bytes(b"fake pdf")
    result = make_result(pdf, source_name="")

    meta = extract_meta(result)

    assert meta.date_source == "ctime"
    assert meta.needs_review is True


def test_extract_meta_real_openscan_pdf(tmp_path):
    """A real OpenScan PDF (no CreationDate in metadata) should still
    extract the date from the filename."""
    pdf = tmp_path / "abc.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    result = make_result(pdf, source_name="OpenScan 20260831 154240356440.pdf")

    meta = extract_meta(result)

    assert meta.date == "2026-08-31"
    assert meta.date_source == "filename"
    assert meta.needs_review is False


def test_capture_time_ordering():
    """Earlier scans should have earlier capture times."""
    m1 = _try_filename("OpenScan 20260831 150515450040.pdf")
    m2 = _try_filename("OpenScan 20260831 154515171987.pdf")
    assert m2.capture_time > m1.capture_time


def test_capture_time_ordering_v2():
    """V2 filenames: earlier epoch_ms -> earlier capture_time."""
    m1 = _try_filename("OpenScan 2026-09-16-1789585922072.pdf")
    m2 = _try_filename("OpenScan 2026-09-16-1789586015125.pdf")
    assert m2.capture_time > m1.capture_time


def test_extract_meta_prefers_filename_v2(tmp_path):
    pdf = tmp_path / "hash.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    result = make_result(pdf, source_name="OpenScan 2026-09-16-1789585922072.pdf")

    meta = extract_meta(result)

    assert meta.date == "2026-09-16"
    assert meta.date_source == "filename"
    assert meta.needs_review is False
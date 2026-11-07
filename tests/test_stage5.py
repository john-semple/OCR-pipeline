"""Tests for Stage 5 — archive + spend log + cleanup.

Tests that scans are moved to Archive/, OCR JSON is saved, the spend CSV
is written/append, and Processing/ is cleaned up (PDF + sidecar + group
JSON removed).
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import ClassEntry, Config, Paths
from src.stage1 import OCRResult, OCRPage
from src.stage2 import NoteMeta
from src.stage3 import NoteGroup, PageRef, Classification
from src.stage5 import archive_and_log, COST_PER_PAGE, SPEND_CSV_HEADER


def make_cfg(tmp_path: Path) -> Config:
    inbox = tmp_path / "00 Inbox"
    pipeline = tmp_path / "00 Pipeline"
    processing = pipeline / "Processing"
    paths = Paths(
        vault_root=tmp_path, homework_root=tmp_path, inbox=inbox,
        pipeline_root=pipeline, processing=processing, groups=processing / "groups",
        archive=pipeline / "Archive", unsorted=pipeline / "Unsorted",
        manual_retry=pipeline / "Manual Retry", failed=pipeline / "Failed",
        openscan_inbox=tmp_path / "openscan", stability_seconds=0,
    )
    paths.ensure_dirs()
    return Config(
        paths=paths, classes=[],
        settings={"spend_log": str(tmp_path / "logs" / "spend.csv")},
        raw={},
    )


def make_result(tmp_path: Path, sha: str, source_name: str = "OpenScan 20260101 120000000000.pdf",
                num_pages: int = 1) -> OCRResult:
    pdf = tmp_path / "00 Pipeline" / "Processing" / f"{sha}.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4")
    # Write the .name sidecar too.
    (tmp_path / "00 Pipeline" / "Processing" / f"{sha}.name").write_text(source_name)
    pages = [OCRPage(index=i, markdown=f"page {i}", images=[]) for i in range(num_pages)]
    return OCRResult(
        pdf_path=pdf, sha256=sha, source_name=source_name,
        pages=pages, title="Title", raw_json={"model": "test", "pages": [{"index": 0}]},
    )


def make_group_and_meta(sha: str, source_name: str, capture_time: float = 1000.0,
                        class_name: str = "Thermo", folder: Path | None = None,
                        is_hit: bool = True, num_pages: int = 1):
    result = make_result(Path("/tmp"), sha, source_name, num_pages)
    # But we need the PDF in the right place. The caller should call
    # make_result with the real tmp_path. This helper is for the data.
    meta = NoteMeta(date="2026-09-01", capture_time=capture_time,
                    date_source="filename", needs_review=False)
    group = NoteGroup(group_id="g1", normalized_title="title", pages=[
        PageRef(sha, source_name, "Title", capture_time, num_pages, str(result.pdf_path))
    ])
    classification = Classification(
        class_name=class_name,
        folder=folder or Path("/tmp/class"),
        matched_alias="title" if is_hit else "",
        is_hit=is_hit,
        matched_via="alias" if is_hit else "",
    )
    return group, classification, meta


def test_archives_pdf_to_archive_dir(tmp_path):
    cfg = make_cfg(tmp_path)
    result = make_result(tmp_path, "abc", "OpenScan 20260101 120000000000.pdf")
    meta = NoteMeta(date="2026-09-01", capture_time=1000, date_source="filename", needs_review=False)
    group = NoteGroup(group_id="g1", normalized_title="title", pages=[
        PageRef("abc", "OpenScan 20260101 120000000000.pdf", "Title", 1000, 1, str(result.pdf_path))
    ])
    classification = Classification("Thermo", tmp_path / "Thermo", "title", True, "alias")
    ocr_results = {"abc": result}
    metas = {"abc": meta}

    archived = archive_and_log([(group, classification)], metas, ocr_results, cfg)

    assert len(archived) == 1
    assert not result.pdf_path.exists(), "PDF should be moved out of Processing/"
    # Archive should contain the PDF.
    pdf_files = list(cfg.paths.archive.glob("*.pdf"))
    assert len(pdf_files) == 1
    assert "2026-09-01" in pdf_files[0].name


def test_saves_ocr_json_to_archive(tmp_path):
    cfg = make_cfg(tmp_path)
    result = make_result(tmp_path, "abc", "OpenScan 20260101 120000000000.pdf")
    meta = NoteMeta(date="2026-09-01", capture_time=1000, date_source="filename", needs_review=False)
    group = NoteGroup(group_id="g1", normalized_title="title", pages=[
        PageRef("abc", "OpenScan 20260101 120000000000.pdf", "Title", 1000, 1, str(result.pdf_path))
    ])
    classification = Classification("Thermo", tmp_path / "Thermo", "title", True, "alias")

    archive_and_log([(group, classification)], {"abc": meta}, {"abc": result}, cfg)

    json_files = list(cfg.paths.archive.glob("*.ocr.json"))
    assert len(json_files) == 1
    data = json.loads(json_files[0].read_text())
    assert data["model"] == "test"


def test_removes_name_sidecar(tmp_path):
    cfg = make_cfg(tmp_path)
    result = make_result(tmp_path, "abc", "OpenScan 20260101 120000000000.pdf")
    sidecar = cfg.paths.processing / "abc.name"
    assert sidecar.exists()

    meta = NoteMeta(date="2026-09-01", capture_time=1000, date_source="filename", needs_review=False)
    group = NoteGroup(group_id="g1", normalized_title="title", pages=[
        PageRef("abc", "OpenScan 20260101 120000000000.pdf", "Title", 1000, 1, str(result.pdf_path))
    ])
    classification = Classification("Thermo", tmp_path / "Thermo", "title", True, "alias")

    archive_and_log([(group, classification)], {"abc": meta}, {"abc": result}, cfg)

    assert not sidecar.exists(), ".name sidecar should be removed"


def test_removes_group_json(tmp_path):
    cfg = make_cfg(tmp_path)
    result = make_result(tmp_path, "abc", "OpenScan 20260101 120000000000.pdf")
    meta = NoteMeta(date="2026-09-01", capture_time=1000, date_source="filename", needs_review=False)
    group = NoteGroup(group_id="g1", normalized_title="title", pages=[
        PageRef("abc", "OpenScan 20260101 120000000000.pdf", "Title", 1000, 1, str(result.pdf_path))
    ])
    # Write the group file.
    group_file = cfg.paths.groups / "g1.json"
    group_file.write_text(json.dumps(group.to_json()))

    classification = Classification("Thermo", tmp_path / "Thermo", "title", True, "alias")

    archive_and_log([(group, classification)], {"abc": meta}, {"abc": result}, cfg)

    assert not group_file.exists(), "group JSON should be removed"


def test_spend_log_creates_header(tmp_path):
    cfg = make_cfg(tmp_path)
    result = make_result(tmp_path, "abc", "OpenScan 20260101 120000000000.pdf")
    meta = NoteMeta(date="2026-09-01", capture_time=1000, date_source="filename", needs_review=False)
    group = NoteGroup(group_id="g1", normalized_title="title", pages=[
        PageRef("abc", "OpenScan 20260101 120000000000.pdf", "Title", 1000, 1, str(result.pdf_path))
    ])
    classification = Classification("Thermo", tmp_path / "Thermo", "title", True, "alias")

    archive_and_log([(group, classification)], {"abc": meta}, {"abc": result}, cfg)

    # Find the spend log at the temp path.
    spend_path = Path(cfg.settings["spend_log"])
    assert spend_path.exists()
    with spend_path.open() as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 1
    last = rows[-1]
    assert last["scan_hash"] == "sha256:abc"
    assert last["class"] == "Thermo"
    assert last["pages"] == "1"
    assert float(last["estimated_cost_usd"]) == COST_PER_PAGE


def test_spend_log_appends(tmp_path):
    """Two runs should produce two rows."""
    cfg = make_cfg(tmp_path)
    spend_path = Path(cfg.settings["spend_log"])

    for sha in ["aaa", "bbb"]:
        result = make_result(tmp_path, sha, "OpenScan 20260101 120000000000.pdf")
        meta = NoteMeta(date="2026-09-01", capture_time=1000, date_source="filename", needs_review=False)
        group = NoteGroup(group_id=f"g-{sha}", normalized_title="title", pages=[
            PageRef(sha, "OpenScan 20260101 120000000000.pdf", "Title", 1000, 1, str(result.pdf_path))
        ])
        classification = Classification("Thermo", tmp_path / "Thermo", "title", True, "alias")
        archive_and_log([(group, classification)], {sha: meta}, {sha: result}, cfg)

    with spend_path.open() as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 2
    assert {r["scan_hash"] for r in rows} == {"sha256:aaa", "sha256:bbb"}


def test_multi_page_pdf_cost(tmp_path):
    """A 3-page PDF should log cost = 3 * 0.004."""
    cfg = make_cfg(tmp_path)
    result = make_result(tmp_path, "abc", "OpenScan 20260101 120000000000.pdf", num_pages=3)
    meta = NoteMeta(date="2026-09-01", capture_time=1000, date_source="filename", needs_review=False)
    group = NoteGroup(group_id="g1", normalized_title="title", pages=[
        PageRef("abc", "OpenScan 20260101 120000000000.pdf", "Title", 1000, 3, str(result.pdf_path))
    ])
    classification = Classification("Thermo", tmp_path / "Thermo", "title", True, "alias")

    archive_and_log([(group, classification)], {"abc": meta}, {"abc": result}, cfg)

    spend_path = Path(cfg.settings["spend_log"])
    with spend_path.open() as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    last = rows[-1]
    assert last["pages"] == "3"
    assert float(last["estimated_cost_usd"]) == 3 * COST_PER_PAGE


def test_unsorted_class_in_spend_log(tmp_path):
    """Unsorted notes should log class='Unsorted'."""
    cfg = make_cfg(tmp_path)
    result = make_result(tmp_path, "abc", "OpenScan 20260101 120000000000.pdf")
    meta = NoteMeta(date="2026-09-01", capture_time=1000, date_source="filename", needs_review=False)
    group = NoteGroup(group_id="g1", normalized_title="random", pages=[
        PageRef("abc", "OpenScan 20260101 120000000000.pdf", "Title", 1000, 1, str(result.pdf_path))
    ])
    classification = Classification("Unsorted", cfg.paths.unsorted, "", False, "")

    archive_and_log([(group, classification)], {"abc": meta}, {"abc": result}, cfg)

    spend_path = Path(cfg.settings["spend_log"])
    with spend_path.open() as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    last = rows[-1]
    assert last["class"] == "Unsorted"


def test_processing_clean_after_run(tmp_path):
    """After Stage 5, Processing/ should have no PDFs or .name files."""
    cfg = make_cfg(tmp_path)
    result = make_result(tmp_path, "abc", "OpenScan 20260101 120000000000.pdf")
    meta = NoteMeta(date="2026-09-01", capture_time=1000, date_source="filename", needs_review=False)
    group = NoteGroup(group_id="g1", normalized_title="title", pages=[
        PageRef("abc", "OpenScan 20260101 120000000000.pdf", "Title", 1000, 1, str(result.pdf_path))
    ])
    classification = Classification("Thermo", tmp_path / "Thermo", "title", True, "alias")

    archive_and_log([(group, classification)], {"abc": meta}, {"abc": result}, cfg)

    pdfs = list(cfg.paths.processing.glob("*.pdf"))
    names = list(cfg.paths.processing.glob("*.name"))
    assert pdfs == []
    assert names == []
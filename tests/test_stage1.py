"""Tests for Stage 1 — the OCR engine interface + runner.

Uses a fake engine (no real API calls) to verify the interface contract,
the response parser, and the runner's loop/error handling.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Config, Paths
from src.stage1 import OCRImage, OCRPage, OCRResult, OCRError, _extract_title
from src.stage1_runner import process_pending


def make_cfg(tmp_path: Path) -> Config:
    inbox = tmp_path / "00 Inbox"
    pipeline = tmp_path / "00 Pipeline"
    processing = pipeline / "Processing"
    paths = Paths(
        vault_root=tmp_path,
        homework_root=tmp_path,
        inbox=inbox,
        pipeline_root=pipeline,
        processing=processing,
        groups=processing / "groups",
        archive=pipeline / "Archive",
        unsorted=pipeline / "Unsorted",
        manual_retry=pipeline / "Manual Retry",
        failed=pipeline / "Failed",
        openscan_inbox=tmp_path / "openscan",
        stability_seconds=0,
    )
    paths.ensure_dirs()
    return Config(paths=paths, classes=[], settings={}, raw={})


class FakeEngine:
    """A fake OCR engine for testing — no API calls."""

    def __init__(self, results: dict[str, OCRResult] | None = None,
                 fail_hashes: set[str] | None = None):
        self.results = results or {}
        self.fail_hashes = fail_hashes or set()
        self.calls: list[str] = []

    def process(self, pdf_path: Path, sha256: str, source_name: str = "") -> OCRResult:
        self.calls.append(sha256)
        if sha256 in self.fail_hashes:
            raise OCRError(f"forced failure for {sha256[:8]}")
        if sha256 in self.results:
            return self.results[sha256]
        # default empty result
        return OCRResult(
            pdf_path=pdf_path,
            sha256=sha256,
            source_name=source_name,
            pages=[OCRPage(index=0, markdown="Title\nbody", images=[])],
            title="Title",
            raw_json={},
        )


def test_extract_title_first_nonempty_line():
    assert _extract_title("\n\n  Thermo Notes\nbody") == "Thermo Notes"


def test_extract_title_empty_markdown():
    assert _extract_title("") == ""
    assert _extract_title("\n\n\n") == ""


def test_runner_processes_all_pdfs(tmp_path):
    cfg = make_cfg(tmp_path)
    (cfg.paths.processing / "aaa.pdf").write_bytes(b"pdf1")
    (cfg.paths.processing / "bbb.pdf").write_bytes(b"pdf2")
    engine = FakeEngine()

    results = process_pending(cfg, engine)

    assert len(results) == 2
    assert {r.sha256 for r in results} == {"aaa", "bbb"}
    assert engine.calls == ["aaa", "bbb"]


def test_runner_empty_processing(tmp_path):
    cfg = make_cfg(tmp_path)
    engine = FakeEngine()
    results = process_pending(cfg, engine)
    assert results == []


def test_runner_moves_failed_files_to_failed_folder(tmp_path):
    cfg = make_cfg(tmp_path)
    (cfg.paths.processing / "good.pdf").write_bytes(b"pdf")
    (cfg.paths.processing / "bad.pdf").write_bytes(b"pdf")
    engine = FakeEngine(fail_hashes={"bad"})

    results = process_pending(cfg, engine)

    assert len(results) == 1
    assert results[0].sha256 == "good"
    assert (cfg.paths.processing / "good.pdf").exists()
    assert not (cfg.paths.processing / "bad.pdf").exists(), "failed file must not stay in Processing/"
    assert (cfg.paths.failed / "bad.pdf").exists(), "failed file must be in Failed/"


def test_runner_moves_failed_sidecar_to_failed_folder(tmp_path):
    """The .name sidecar should also move to Failed/ alongside the PDF."""
    cfg = make_cfg(tmp_path)
    (cfg.paths.processing / "bad.pdf").write_bytes(b"pdf")
    (cfg.paths.processing / "bad.name").write_text("OpenScan 20260101 120000000000.pdf")
    engine = FakeEngine(fail_hashes={"bad"})

    results = process_pending(cfg, engine)

    assert len(results) == 0
    assert (cfg.paths.failed / "bad.pdf").exists()
    assert (cfg.paths.failed / "bad.name").exists(), ".name sidecar should move to Failed/"
    assert not (cfg.paths.processing / "bad.name").exists()


def test_runner_ignores_non_pdf_files(tmp_path):
    cfg = make_cfg(tmp_path)
    (cfg.paths.processing / "notes.txt").write_bytes(b"text")
    (cfg.paths.processing / ".gitkeep").write_bytes(b"")
    (cfg.paths.processing / "real.pdf").write_bytes(b"pdf")
    engine = FakeEngine()

    results = process_pending(cfg, engine)

    assert len(results) == 1
    assert results[0].sha256 == "real"


def test_ocr_result_fields():
    img = OCRImage(id="img-0.jpeg", image_base64=None,
                   top_left_x=10, top_left_y=20,
                   bottom_right_x=100, bottom_right_y=200)
    page = OCRPage(index=0, markdown="# Title\n$E=mc^2$", images=[img])
    result = OCRResult(
        pdf_path=Path("/tmp/x.pdf"),
        sha256="abc123",
        source_name="OpenScan 20260101 120000000000.pdf",
        pages=[page],
        title="Title",
        raw_json={"pages": [{"index": 0}]},
    )
    assert result.num_pages == 1
    assert result.title == "Title"
    assert result.pages[0].images[0].id == "img-0.jpeg"
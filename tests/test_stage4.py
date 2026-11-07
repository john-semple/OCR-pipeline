"""Tests for Stage 4 — note writer.

Tests frontmatter, body assembly, diagram saving/rewriting, filename
collision handling, and the single-page vs multi-page structure.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import ClassEntry, Config, Paths
from src.stage1 import OCRResult, OCRPage, OCRImage
from src.stage2 import NoteMeta
from src.stage3 import NoteGroup, PageRef, Classification
from src.stage4 import write_note, write_notes, _slugify, _unique_path, _unique_folder


def make_cfg(tmp_path: Path, classes: list[ClassEntry] | None = None) -> Config:
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
    if classes is None:
        classes = [
            ClassEntry(
                name="Thermodynamics & Lab",
                folder=tmp_path / "Thermodynamics & Lab",
                aliases=["thermo", "thermodynamics"],
            ),
            ClassEntry(
                name="Circuits",
                folder=tmp_path / "Circuits",
                aliases=["circuits"],
            ),
        ]
    return Config(paths=paths, classes=classes, settings={}, raw={})


def make_ocr_result(tmp_path: Path, sha: str, title: str, markdown: str,
                    images: list[OCRImage] | None = None,
                    source_name: str = "OpenScan 20260101 120000000000.pdf") -> OCRResult:
    pdf = tmp_path / "00 Pipeline" / "Processing" / f"{sha}.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4")
    return OCRResult(
        pdf_path=pdf, sha256=sha, source_name=source_name,
        pages=[OCRPage(index=0, markdown=markdown, images=images or [])],
        title=title, raw_json={},
    )


# --- _slugify --- #

def test_slugify_basic():
    assert _slugify("Thermodynamics") == "Thermodynamics"


def test_slugify_spaces_and_punct():
    assert _slugify("Thermo! Notes?") == "Thermo-Notes"
    assert _slugify("ME 301: Lecture 5") == "ME-301-Lecture-5"


def test_slugify_empty():
    assert _slugify("") == "untitled"


def test_slugify_long_title():
    long = "A" * 100
    assert len(_slugify(long)) <= 60


# --- _unique_path --- #

def test_unique_path_no_collision(tmp_path):
    p = tmp_path / "note.md"
    assert _unique_path(p) == p


def test_unique_path_collision(tmp_path):
    p = tmp_path / "note.md"
    p.write_text("existing")
    result = _unique_path(p)
    assert result.name == "note-2.md"


def test_unique_path_double_collision(tmp_path):
    (tmp_path / "note.md").write_text("a")
    (tmp_path / "note-2.md").write_text("b")
    result = _unique_path(tmp_path / "note.md")
    assert result.name == "note-3.md"


# --- _unique_folder --- #

def test_unique_folder_no_collision(tmp_path):
    f = tmp_path / "scan"
    assert _unique_folder(f) == f


def test_unique_folder_collision(tmp_path):
    (tmp_path / "scan").mkdir()
    result = _unique_folder(tmp_path / "scan")
    assert result.name == "scan-2"


def test_unique_folder_double_collision(tmp_path):
    (tmp_path / "scan").mkdir()
    (tmp_path / "scan-2").mkdir()
    result = _unique_folder(tmp_path / "scan")
    assert result.name == "scan-3"


# --- write_note: single page --- #

def test_write_note_single_page(tmp_path):
    cfg = make_cfg(tmp_path)
    result = make_ocr_result(tmp_path, "abc", "Thermo Notes", "Thermo Notes\n\n$E=mc^2$")
    meta = NoteMeta(date="2026-09-01", capture_time=1000, date_source="filename", needs_review=False)
    group = NoteGroup(group_id="g1", normalized_title="thermo notes", pages=[
        PageRef("abc", "OpenScan 20260101 120000000000.pdf", "Thermo Notes", 1000, 1, str(result.pdf_path))
    ])
    classification = Classification(
        class_name="Thermodynamics & Lab", folder=cfg.classes[0].folder,
        matched_alias="thermo", is_hit=True, matched_via="alias",
    )

    note_path = write_note(group, classification, meta, {"abc": result}, cfg)

    assert note_path.exists()
    content = note_path.read_text()
    assert 'class: "Thermodynamics & Lab"' in content
    assert "date: 2026-09-01" in content
    assert "needs_review: false" in content
    assert "$E=mc^2$" in content
    # The .md is inside a per-scan folder named "2026-09-01 Thermo-Notes".
    assert note_path.name == "2026-09-01 Thermo-Notes.md"
    assert note_path.parent.name == "2026-09-01 Thermo-Notes"
    assert note_path.parent.parent == cfg.classes[0].folder


def test_write_note_unsorted_sets_needs_review(tmp_path):
    cfg = make_cfg(tmp_path)
    result = make_ocr_result(tmp_path, "abc", "Random", "Random\nbody")
    meta = NoteMeta(date="2026-09-01", capture_time=1000, date_source="filename", needs_review=False)
    group = NoteGroup(group_id="g1", normalized_title="random", pages=[
        PageRef("abc", "OpenScan 20260101 120000000000.pdf", "Random", 1000, 1, str(result.pdf_path))
    ])
    classification = Classification(
        class_name="Unsorted", folder=cfg.paths.unsorted,
        matched_alias="", is_hit=False, matched_via="",
    )

    note_path = write_note(group, classification, meta, {"abc": result}, cfg)

    content = note_path.read_text()
    assert 'class: "Unsorted"' in content
    assert "needs_review: true" in content
    # Note is inside a per-scan folder within Unsorted.
    assert note_path.parent.parent == cfg.paths.unsorted


def test_write_note_with_diagram(tmp_path):
    """Diagram image is saved loose next to the .md and markdown uses wikilinks."""
    cfg = make_cfg(tmp_path)
    # 1x1 red pixel PNG
    png_b64 = base64.b64encode(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 50  # minimal fake PNG
    ).decode()
    img = OCRImage(id="img-0.jpeg", image_base64=png_b64,
                   top_left_x=10, top_left_y=20, bottom_right_x=100, bottom_right_y=200)
    result = make_ocr_result(
        tmp_path, "abc", "Thermo",
        "Thermo\n\n![img-0.jpeg](img-0.jpeg)\n\nbody",
        images=[img],
    )
    meta = NoteMeta(date="2026-09-01", capture_time=1000, date_source="filename", needs_review=False)
    group = NoteGroup(group_id="g1", normalized_title="thermo", pages=[
        PageRef("abc", "OpenScan 20260101 120000000000.pdf", "Thermo", 1000, 1, str(result.pdf_path))
    ])
    classification = Classification(
        class_name="Thermodynamics & Lab", folder=cfg.classes[0].folder,
        matched_alias="thermo", is_hit=True, matched_via="alias",
    )

    note_path = write_note(group, classification, meta, {"abc": result}, cfg)

    content = note_path.read_text()
    # The placeholder should be rewritten to a wikilink.
    assert "![img-0.jpeg](img-0.jpeg)" not in content
    assert "![[2026-09-01 Thermo-diag0.png]]" in content
    # The diagram file should exist loose in the scan folder (no attachments/).
    diag_files = list(note_path.parent.glob("*.png"))
    assert len(diag_files) == 1
    assert diag_files[0].name == "2026-09-01 Thermo-diag0.png"


def test_write_note_filename_collision(tmp_path):
    """Same date+title should produce -2 suffix on both folder and .md."""
    cfg = make_cfg(tmp_path)
    result = make_ocr_result(tmp_path, "abc", "Thermo", "Thermo\nbody")
    meta = NoteMeta(date="2026-09-01", capture_time=1000, date_source="filename", needs_review=False)
    group = NoteGroup(group_id="g1", normalized_title="thermo", pages=[
        PageRef("abc", "x.pdf", "Thermo", 1000, 1, str(result.pdf_path))
    ])
    classification = Classification(
        class_name="Thermodynamics & Lab", folder=cfg.classes[0].folder,
        matched_alias="thermo", is_hit=True, matched_via="alias",
    )

    p1 = write_note(group, classification, meta, {"abc": result}, cfg)
    p2 = write_note(group, classification, meta, {"abc": result}, cfg)

    assert p1.name == "2026-09-01 Thermo.md"
    assert p1.parent.name == "2026-09-01 Thermo"
    assert p2.name == "2026-09-01 Thermo-2.md"
    assert p2.parent.name == "2026-09-01 Thermo-2"


# --- write_note: multi-page --- #

def test_write_note_cross_file_multi_page(tmp_path):
    """Two single-page PDFs grouped into one note get ## Page 1 / ## Page 2."""
    cfg = make_cfg(tmp_path)
    r1 = make_ocr_result(tmp_path, "aaa", "Thermo", "Thermo\npage 1 body", source_name="a.pdf")
    r2 = make_ocr_result(tmp_path, "bbb", "Thermo cont", "Thermo cont\npage 2 body", source_name="b.pdf")
    meta = NoteMeta(date="2026-09-01", capture_time=1000, date_source="filename", needs_review=False)
    group = NoteGroup(group_id="g1", normalized_title="thermo", pages=[
        PageRef("aaa", "a.pdf", "Thermo", 1000, 1, str(r1.pdf_path)),
        PageRef("bbb", "b.pdf", "Thermo cont", 1010, 1, str(r2.pdf_path)),
    ])
    classification = Classification(
        class_name="Thermodynamics & Lab", folder=cfg.classes[0].folder,
        matched_alias="thermo", is_hit=True, matched_via="alias",
    )

    note_path = write_note(group, classification, meta, {"aaa": r1, "bbb": r2}, cfg)

    content = note_path.read_text()
    assert "## Page 1" in content
    assert "## Page 2" in content
    assert "page 1 body" in content
    assert "page 2 body" in content


def test_write_note_multi_page_pdf(tmp_path):
    """A single PDF with 3 internal pages gets ## Page 1/2/3."""
    cfg = make_cfg(tmp_path)
    pdf = tmp_path / "00 Pipeline" / "Processing" / "abc.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4")
    result = OCRResult(
        pdf_path=pdf, sha256="abc", source_name="multi.pdf",
        pages=[
            OCRPage(index=0, markdown="Thermo\np1", images=[]),
            OCRPage(index=1, markdown="p2", images=[]),
            OCRPage(index=2, markdown="p3", images=[]),
        ],
        title="Thermo", raw_json={},
    )
    meta = NoteMeta(date="2026-09-01", capture_time=1000, date_source="filename", needs_review=False)
    group = NoteGroup(group_id="g1", normalized_title="thermo", pages=[
        PageRef("abc", "multi.pdf", "Thermo", 1000, 3, str(pdf))
    ])
    classification = Classification(
        class_name="Thermodynamics & Lab", folder=cfg.classes[0].folder,
        matched_alias="thermo", is_hit=True, matched_via="alias",
    )

    note_path = write_note(group, classification, meta, {"abc": result}, cfg)

    content = note_path.read_text()
    assert content.count("## Page ") == 3
    assert "p1" in content
    assert "p2" in content
    assert "p3" in content


# --- write_notes batch --- #

def test_write_notes_batch(tmp_path):
    cfg = make_cfg(tmp_path)
    r1 = make_ocr_result(tmp_path, "aaa", "Thermo", "Thermo\nbody")
    r2 = make_ocr_result(tmp_path, "bbb", "Random", "Random\nbody")
    metas = {
        "aaa": NoteMeta(date="2026-09-01", capture_time=1000, date_source="filename", needs_review=False),
        "bbb": NoteMeta(date="2026-09-01", capture_time=2000, date_source="filename", needs_review=False),
    }
    g1 = NoteGroup(group_id="g1", normalized_title="thermo", pages=[
        PageRef("aaa", "a.pdf", "Thermo", 1000, 1, str(r1.pdf_path))])
    g2 = NoteGroup(group_id="g2", normalized_title="random", pages=[
        PageRef("bbb", "b.pdf", "Random", 2000, 1, str(r2.pdf_path))])
    classified = [
        (g1, Classification("Thermodynamics & Lab", cfg.classes[0].folder, "thermo", True, "alias")),
        (g2, Classification("Unsorted", cfg.paths.unsorted, "", False, "")),
    ]

    paths = write_notes(classified, metas, {"aaa": r1, "bbb": r2}, cfg)

    assert len(paths) == 2
    assert any("Thermo" in p.name for p in paths)
    assert any("Random" in p.name for p in paths)
    # Thermo note should be in a per-scan folder in the class folder,
    # Random in a per-scan folder in Unsorted.
    thermo_note = next(p for p in paths if "Thermo" in p.name)
    random_note = next(p for p in paths if "Random" in p.name)
    assert thermo_note.parent.parent == cfg.classes[0].folder
    assert random_note.parent.parent == cfg.paths.unsorted
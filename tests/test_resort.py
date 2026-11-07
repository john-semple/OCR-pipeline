"""Tests for resort Unsorted/ — re-classification of existing notes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import ClassEntry, Config, Paths
from src.resort import resort_unsorted, _extract_title_from_note, _update_frontmatter
from src.stage3 import Classification


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
            ClassEntry(name="Thermodynamics & Lab", folder=tmp_path / "Thermo",
                       aliases=["thermo", "thermodynamics"]),
            ClassEntry(name="Circuits", folder=tmp_path / "Circuits",
                       aliases=["circuits"]),
        ]
    return Config(paths=paths, classes=classes, settings={}, raw={})


def write_note(folder: Path, title: str, date: str = "2026-09-01",
               slug: str | None = None,
               class_name: str = "Unsorted",
               needs_review: bool = True, body: str = "",
               diagrams: list[str] | None = None) -> Path:
    """Write a note inside a per-scan folder. Returns the .md path.

    `folder` is the parent (e.g. Unsorted/ or a class folder). A per-scan
    subfolder `{date} {slug}` is created inside it. `slug` defaults to
    the slugified title. `diagrams` is a list of filenames to create as
    fake PNGs alongside the .md.
    """
    if slug is None:
        slug = title.replace(" ", "-")
    scan_folder = folder / f"{date} {slug}"
    scan_folder.mkdir(parents=True, exist_ok=True)
    note_path = scan_folder / f"{date} {slug}.md"
    fm = (
        "---\n"
        f'class: "{class_name}"\n'
        f"date: {date}\n"
        f"source_scan: 00 Pipeline/Archive/test.pdf\n"
        f"scan_hash: sha256:abc\n"
        f"ocr_confidence: null\n"
        f"tags: [needs-tagging]\n"
        f"needs_review: {str(needs_review).lower()}\n"
        f"pages: 1\n"
        "---\n\n"
    )
    note_path.write_text(fm + (body or f"{title}\n\nbody text\n"), encoding="utf-8")
    for diag in diagrams or []:
        (scan_folder / diag).write_bytes(b"fake png")
    return note_path


# --- _extract_title_from_note --- #

def test_extract_title_with_frontmatter():
    content = "---\nclass: Unsorted\n---\n\nThermo Notes\n\nbody"
    assert _extract_title_from_note(content) == "Thermo Notes"


def test_extract_title_without_frontmatter():
    content = "Thermo Notes\n\nbody"
    assert _extract_title_from_note(content) == "Thermo Notes"


def test_extract_title_empty():
    assert _extract_title_from_note("---\nclass: x\n---\n\n") == ""


# --- _update_frontmatter --- #

def test_update_frontmatter_class():
    content = '---\nclass: "Unsorted"\nneeds_review: true\n---\n\nbody'
    c = Classification("Thermodynamics & Lab", Path("/tmp"), "", True, "alias")
    updated = _update_frontmatter(content, c)
    assert 'class: "Thermodynamics & Lab"' in updated
    assert "needs_review: false" in updated


# --- resort_unsorted --- #

def test_resort_moves_matching_note(tmp_path):
    cfg = make_cfg(tmp_path)
    note = write_note(cfg.paths.unsorted, "Thermo Notes")

    moved = resort_unsorted(cfg)

    assert len(moved) == 1
    assert not note.exists(), "note should be moved out of Unsorted"
    assert not note.parent.exists(), "per-scan folder should be removed from Unsorted"
    dest = cfg.classes[0].folder / "2026-09-01 Thermo-Notes" / "2026-09-01 Thermo-Notes.md"
    assert dest.exists()
    content = dest.read_text()
    assert 'class: "Thermodynamics & Lab"' in content
    assert "needs_review: false" in content


def test_resort_leaves_non_matching_note(tmp_path):
    cfg = make_cfg(tmp_path)
    note = write_note(cfg.paths.unsorted, "Random Title")

    moved = resort_unsorted(cfg)

    assert len(moved) == 0
    assert note.exists(), "non-matching note should stay in Unsorted"


def test_resort_empty_unsorted(tmp_path):
    cfg = make_cfg(tmp_path)
    moved = resort_unsorted(cfg)
    assert moved == []


def test_resort_no_unsorted_dir(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.paths.unsorted.rmdir()
    moved = resort_unsorted(cfg)
    assert moved == []


def test_resort_moves_multiple_notes(tmp_path):
    cfg = make_cfg(tmp_path)
    write_note(cfg.paths.unsorted, "Thermo Notes")
    write_note(cfg.paths.unsorted, "Circuits HW")
    write_note(cfg.paths.unsorted, "Random")

    moved = resort_unsorted(cfg)

    assert len(moved) == 2
    moved_classes = {c for _, c in moved}
    assert "Thermodynamics & Lab" in moved_classes
    assert "Circuits" in moved_classes


def test_resort_moves_diagrams(tmp_path):
    """Diagram PNGs loose in the per-scan folder are moved with it."""
    cfg = make_cfg(tmp_path)
    note = write_note(cfg.paths.unsorted, "Thermo Notes",
                      body="Thermo Notes\n\n![[2026-09-01 Thermo-Notes-diag0.png]]\n\nbody",
                      diagrams=["2026-09-01 Thermo-Notes-diag0.png"])

    moved = resort_unsorted(cfg)

    assert len(moved) == 1
    dest_diag = cfg.classes[0].folder / "2026-09-01 Thermo-Notes" / "2026-09-01 Thermo-Notes-diag0.png"
    assert dest_diag.exists()
    assert dest_diag.read_bytes() == b"fake png"


def test_resort_folder_collision(tmp_path):
    """If the class folder already has a scan folder with the same name, append -2."""
    cfg = make_cfg(tmp_path)
    # Write a note in Unsorted.
    write_note(cfg.paths.unsorted, "Thermo Notes")
    # Write an existing scan in the class folder with the same folder name.
    write_note(cfg.classes[0].folder, "Thermo Notes",
               class_name="Thermodynamics & Lab", needs_review=False)

    moved = resort_unsorted(cfg)

    assert len(moved) == 1
    # Original stays, new one gets -2 suffix.
    assert (cfg.classes[0].folder / "2026-09-01 Thermo-Notes" / "2026-09-01 Thermo-Notes.md").exists()
    assert (cfg.classes[0].folder / "2026-09-01 Thermo-Notes-2" / "2026-09-01 Thermo-Notes-2.md").exists()


def test_resort_ignores_sorting_guide(tmp_path):
    """The Sorting Guide.md should not be re-sorted."""
    cfg = make_cfg(tmp_path)
    guide = cfg.paths.unsorted / "Sorting Guide.md"
    guide.write_text("# Sorting Guide\n\nauto-generated")

    moved = resort_unsorted(cfg)

    assert len(moved) == 0
    assert guide.exists()
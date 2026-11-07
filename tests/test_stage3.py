"""Tests for Stage 3a — multi-page grouping.

Tests title normalization, continuation markers, cross-file grouping,
multi-page PDFs, time window enforcement, and group persistence.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Config, Paths
from src.stage1 import OCRResult, OCRPage
from src.stage2 import NoteMeta
from src.stage3 import (
    NoteGroup,
    PageRef,
    normalize_title,
    has_continuation_marker,
    group_results,
)


def make_cfg(tmp_path: Path) -> Config:
    inbox = tmp_path / "00 Inbox"
    pipeline = tmp_path / "00 Pipeline"
    processing = pipeline / "Processing"
    groups = processing / "groups"
    paths = Paths(
        vault_root=tmp_path, homework_root=tmp_path, inbox=inbox,
        pipeline_root=pipeline, processing=processing, groups=groups,
        archive=pipeline / "Archive", unsorted=pipeline / "Unsorted",
        manual_retry=pipeline / "Manual Retry", failed=pipeline / "Failed",
        openscan_inbox=tmp_path / "openscan", stability_seconds=0,
    )
    paths.ensure_dirs()
    return Config(paths=paths, classes=[], settings={"grouping_window_minutes": 30}, raw={})


def make_result(tmp_path: Path, sha: str, title: str, source_name: str = "",
                capture_time: float = 1000.0, num_pages: int = 1) -> tuple[OCRResult, NoteMeta]:
    pdf = tmp_path / "00 Pipeline" / "Processing" / f"{sha}.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    pages = [OCRPage(index=i, markdown=f"{title}\nbody", images=[]) for i in range(num_pages)]
    result = OCRResult(
        pdf_path=pdf, sha256=sha, source_name=source_name,
        pages=pages, title=title, raw_json={},
    )
    meta = NoteMeta(
        date="2026-09-01", capture_time=capture_time,
        date_source="filename", needs_review=False,
    )
    return result, meta


# --- Title normalization --- #

def test_normalize_title_basic():
    assert normalize_title("Thermo Notes") == "thermo notes"


def test_normalize_title_strips_punctuation():
    assert normalize_title("Thermo! Notes?") == "thermo notes"
    assert normalize_title("ME-301: Lecture 5") == "me301 lecture 5"


def test_normalize_title_strips_continuation_markers():
    assert normalize_title("Thermo cont") == "thermo"
    assert normalize_title("Thermo continued") == "thermo"
    assert normalize_title("Thermo contd") == "thermo"
    assert normalize_title("Thermo page 2") == "thermo"
    assert normalize_title("Thermo pg 3") == "thermo"
    assert normalize_title("Thermo p. 2") == "thermo"
    assert normalize_title("Thermo...") == "thermo"


def test_normalize_title_lowercase():
    assert normalize_title("THERMODYNAMICS") == "thermodynamics"


# --- Continuation marker detection --- #

def test_has_continuation_marker():
    assert has_continuation_marker("Thermo cont")
    assert has_continuation_marker("Thermo continued")
    assert has_continuation_marker("Thermo page 2")
    assert has_continuation_marker("Thermo pg 3")


def test_no_continuation_marker():
    assert not has_continuation_marker("Thermo Notes")
    assert not has_continuation_marker("Ideas for CL")
    assert not has_continuation_marker("")


# --- Grouping --- #

def test_single_page_unique_titles_form_separate_groups(tmp_path):
    cfg = make_cfg(tmp_path)
    r1, m1 = make_result(tmp_path, "aaa", "Thermo Notes", capture_time=1000)
    r2, m2 = make_result(tmp_path, "bbb", "Circuits HW", capture_time=1010)

    groups = group_results([r1, r2], [m1, m2], cfg)

    assert len(groups) == 2
    assert groups[0].pages[0].sha256 == "aaa"
    assert groups[1].pages[0].sha256 == "bbb"


def test_same_title_within_window_groups_together(tmp_path):
    """Two single-page PDFs with the same title, 5 min apart -> one group."""
    cfg = make_cfg(tmp_path)
    r1, m1 = make_result(tmp_path, "aaa", "Thermo", capture_time=1000)
    r2, m2 = make_result(tmp_path, "bbb", "Thermo", capture_time=1030)  # 30s later

    groups = group_results([r1, r2], [m1, m2], cfg)

    assert len(groups) == 1
    assert len(groups[0].pages) == 2
    assert {p.sha256 for p in groups[0].pages} == {"aaa", "bbb"}


def test_same_title_outside_window_form_separate_groups(tmp_path):
    """Same title, but >30 min apart -> two groups."""
    cfg = make_cfg(tmp_path)
    r1, m1 = make_result(tmp_path, "aaa", "Thermo", capture_time=1000)
    r2, m2 = make_result(tmp_path, "bbb", "Thermo", capture_time=1000 + 31 * 60)  # 31 min later

    groups = group_results([r1, r2], [m1, m2], cfg)

    assert len(groups) == 2
    assert len(groups[0].pages) == 1
    assert len(groups[1].pages) == 1


def test_continuation_marker_groups_with_same_title(tmp_path):
    """Page 1 "Thermo", Page 2 "Thermo cont" -> one group."""
    cfg = make_cfg(tmp_path)
    r1, m1 = make_result(tmp_path, "aaa", "Thermo", capture_time=1000)
    r2, m2 = make_result(tmp_path, "bbb", "Thermo cont", capture_time=1000 + 60)

    groups = group_results([r1, r2], [m1, m2], cfg)

    assert len(groups) == 1
    assert len(groups[0].pages) == 2
    # normalized title should be "thermo" (marker stripped)
    assert groups[0].normalized_title == "thermo"


def test_multi_page_pdf_is_own_group(tmp_path):
    """A PDF with 3 internal pages forms its own group of one file."""
    cfg = make_cfg(tmp_path)
    r1, m1 = make_result(tmp_path, "aaa", "Thermo", capture_time=1000, num_pages=3)
    r2, m2 = make_result(tmp_path, "bbb", "Thermo", capture_time=1010)

    groups = group_results([r1, r2], [m1, m2], cfg)

    # The multi-page PDF should NOT group with the single-page one.
    assert len(groups) == 2
    multi = next(g for g in groups if g.pages[0].num_pages > 1)
    assert multi.is_multi_page
    assert len(multi.pages) == 1  # one file, 3 internal pages


def test_different_titles_not_grouped(tmp_path):
    cfg = make_cfg(tmp_path)
    r1, m1 = make_result(tmp_path, "aaa", "Thermo", capture_time=1000)
    r2, m2 = make_result(tmp_path, "bbb", "Circuits", capture_time=1000)

    groups = group_results([r1, r2], [m1, m2], cfg)
    assert len(groups) == 2


def test_group_persistence(tmp_path):
    """Groups are written to Processing/groups/{group_id}.json."""
    cfg = make_cfg(tmp_path)
    r1, m1 = make_result(tmp_path, "aaa", "Thermo", capture_time=1000)
    r2, m2 = make_result(tmp_path, "bbb", "Thermo", capture_time=1000)

    groups = group_results([r1, r2], [m1, m2], cfg)

    assert len(groups) == 1
    group_file = cfg.paths.groups / f"{groups[0].group_id}.json"
    assert group_file.exists()

    data = json.loads(group_file.read_text())
    assert data["normalized_title"] == "thermo"
    assert len(data["pages"]) == 2


def test_group_persistence_clears_old_files(tmp_path):
    """A previous run's group files should be cleared."""
    cfg = make_cfg(tmp_path)
    # Write a stale group file.
    (cfg.paths.groups / "stale.json").write_text('{"old": true}')
    assert (cfg.paths.groups / "stale.json").exists()

    r1, m1 = make_result(tmp_path, "aaa", "Thermo", capture_time=1000)
    group_results([r1], [m1], cfg)

    assert not (cfg.paths.groups / "stale.json").exists()


def test_three_same_title_within_window_one_group(tmp_path):
    """Three scans, same title, within 30 min -> one group of 3."""
    cfg = make_cfg(tmp_path)
    r1, m1 = make_result(tmp_path, "aaa", "Thermo", capture_time=1000)
    r2, m2 = make_result(tmp_path, "bbb", "Thermo", capture_time=1000 + 5 * 60)
    r3, m3 = make_result(tmp_path, "ccc", "Thermo", capture_time=1000 + 10 * 60)

    groups = group_results([r1, r2, r3], [m1, m2, m3], cfg)

    assert len(groups) == 1
    assert len(groups[0].pages) == 3
    # Pages should be in capture-time order
    assert [p.sha256 for p in groups[0].pages] == ["aaa", "bbb", "ccc"]


def test_note_group_is_multi_page():
    group = NoteGroup(group_id="x", normalized_title="thermo", pages=[
        PageRef("aaa", "a.pdf", "Thermo", 1000, 1, "/tmp/aaa.pdf"),
        PageRef("bbb", "b.pdf", "Thermo", 2000, 1, "/tmp/bbb.pdf"),
    ])
    assert group.is_multi_page

    single = NoteGroup(group_id="y", normalized_title="thermo", pages=[
        PageRef("aaa", "a.pdf", "Thermo", 1000, 1, "/tmp/aaa.pdf"),
    ])
    assert not single.is_multi_page

    multipage_pdf = NoteGroup(group_id="z", normalized_title="thermo", pages=[
        PageRef("aaa", "a.pdf", "Thermo", 1000, 3, "/tmp/aaa.pdf"),
    ])
    assert multipage_pdf.is_multi_page
"""Tests for Stage 3b — alias-map classification.

Tests exact match, substring match for short aliases, no-match → Unsorted,
and the first-match-wins ordering.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import ClassEntry, Config, Paths
from src.stage3 import (
    NoteGroup,
    Classification,
    classify,
    classify_groups,
    _alias_matches,
)


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
                aliases=["thermo", "thermodynamics", "me 301", "me301"],
            ),
            ClassEntry(
                name="Circuits",
                folder=tmp_path / "Circuits",
                aliases=["circuits", "circuit", "circuit analysis"],
            ),
            ClassEntry(
                name="Structures and Properties of Materials",
                folder=tmp_path / "Materials",
                aliases=["structures and properties of materials", "materials", "structures"],
            ),
        ]
    return Config(paths=paths, classes=classes, settings={}, raw={})


def make_group(title: str) -> NoteGroup:
    return NoteGroup(group_id="test-uuid", normalized_title=title, pages=[])


# --- _alias_matches --- #

def test_alias_matches_exact_long():
    assert _alias_matches("thermodynamics", "thermodynamics")


def test_alias_matches_exact_short():
    assert _alias_matches("thermo", "thermo")


def test_alias_matches_substring_short():
    """Short alias 'me 301' matches 'me 301 lecture 5' as substring."""
    assert _alias_matches("me 301", "me 301 lecture 5")


def test_alias_no_match_long_alias_substring():
    """Long alias must match exactly, not as substring."""
    assert not _alias_matches("thermodynamics", "thermodynamics lecture 5")


def test_alias_no_match_different():
    assert not _alias_matches("circuits", "thermo")


# --- classify --- #

def test_classify_exact_match(tmp_path):
    cfg = make_cfg(tmp_path)
    g = make_group("thermodynamics")
    c = classify(g, cfg)
    assert c.is_hit
    assert c.class_name == "Thermodynamics & Lab"
    assert c.matched_alias == "thermodynamics"


def test_classify_substring_match(tmp_path):
    """'hw test thermodynamics' should match alias 'thermodynamics'?
    No — 'thermodynamics' is 13 chars (long), so exact match only.
    But 'thermo' is short, so it matches as substring."""
    cfg = make_cfg(tmp_path)
    g = make_group("hw test thermo")
    c = classify(g, cfg)
    assert c.is_hit
    assert c.class_name == "Thermodynamics & Lab"
    assert c.matched_alias == "thermo"


def test_classify_miss_goes_to_unsorted(tmp_path):
    cfg = make_cfg(tmp_path)
    g = make_group("ideas for cl")
    c = classify(g, cfg)
    assert not c.is_hit
    assert c.class_name == "Unsorted"
    assert c.folder == cfg.paths.unsorted
    assert c.matched_alias == ""


def test_classify_me301_substring(tmp_path):
    """'me 301 lecture 5' should match alias 'me 301' (short, substring)."""
    cfg = make_cfg(tmp_path)
    g = make_group("me 301 lecture 5")
    c = classify(g, cfg)
    assert c.is_hit
    assert c.class_name == "Thermodynamics & Lab"


def test_classify_first_match_wins(tmp_path):
    """If two classes have overlapping aliases, the first in YAML order wins."""
    classes = [
        ClassEntry(name="First", folder=tmp_path / "First", aliases=["thermo"]),
        ClassEntry(name="Second", folder=tmp_path / "Second", aliases=["thermo"]),
    ]
    cfg = make_cfg(tmp_path, classes=classes)
    g = make_group("thermo")
    c = classify(g, cfg)
    assert c.class_name == "First"


def test_classify_circuits(tmp_path):
    cfg = make_cfg(tmp_path)
    g = make_group("circuits hw 3")
    c = classify(g, cfg)
    assert c.is_hit
    assert c.class_name == "Circuits"
    assert c.matched_alias == "circuits"


def test_classify_structures_exact(tmp_path):
    cfg = make_cfg(tmp_path)
    g = make_group("structures and properties of materials")
    c = classify(g, cfg)
    assert c.is_hit
    assert c.class_name == "Structures and Properties of Materials"


def test_classify_structures_short_alias(tmp_path):
    """'materials' is short -> substring match. 'structures and properties
    of materials' contains 'materials'."""
    cfg = make_cfg(tmp_path)
    g = make_group("materials and properties lecture notes")
    c = classify(g, cfg)
    assert c.is_hit
    assert c.class_name == "Structures and Properties of Materials"


def test_classify_empty_title_miss(tmp_path):
    cfg = make_cfg(tmp_path)
    g = make_group("")
    c = classify(g, cfg)
    assert not c.is_hit
    assert c.class_name == "Unsorted"


def test_classify_groups_batch(tmp_path):
    cfg = make_cfg(tmp_path)
    groups = [
        make_group("thermo"),
        make_group("circuits hw"),
        make_group("random notes"),
    ]
    results = classify_groups(groups, cfg)
    assert len(results) == 3
    assert results[0][1].is_hit
    assert results[1][1].is_hit
    assert not results[2][1].is_hit


def test_classify_misspelled_alias(tmp_path):
    """'thermodynmics' is a configured alias for common OCR misspelling."""
    classes = [
        ClassEntry(
            name="Thermodynamics & Lab",
            folder=tmp_path / "Thermo",
            aliases=["thermo", "thermodynamics", "thermodynmics"],
        ),
    ]
    cfg = make_cfg(tmp_path, classes=classes)
    g = make_group("thermodynmics")
    c = classify(g, cfg)
    assert c.is_hit
    assert c.class_name == "Thermodynamics & Lab"
    assert c.matched_alias == "thermodynmics"
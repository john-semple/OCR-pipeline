"""Tests for LLM fallback — MistralLLMClassifier + Stage 3b integration.

Uses a fake LLM classifier (no real API calls) to verify the interface
contract, response resolution, and the Stage 3b fallback path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import ClassEntry, Config, Paths
from src.llm_fallback import LLMError
from src.stage3 import NoteGroup, PageRef, classify, classify_groups, Classification


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
            ClassEntry(name="Structures and Properties of Materials",
                       folder=tmp_path / "Materials", aliases=["materials"]),
        ]
    return Config(paths=paths, classes=classes, settings={}, raw={})


class FakeLLM:
    """Fake LLM classifier for testing — no API calls."""

    def __init__(self, answer: str = "NONE"):
        self.answer = answer
        self.calls: list[str] = []

    def classify(self, title: str, classes: list[ClassEntry]) -> str:
        self.calls.append(title)
        return self.answer


def make_group(title: str, raw_title: str = "") -> NoteGroup:
    return NoteGroup(
        group_id="test", normalized_title=title,
        pages=[PageRef("sha", "x.pdf", raw_title or title, 1000, 1, "/tmp/x.pdf")],
    )


# --- LLM resolution --- #

def test_llm_exact_class_name():
    """LLM returns the exact class name -> resolved."""
    from src.llm_fallback import MistralLLMClassifier
    # We test _resolve_class directly (no API call needed).
    classifier = object.__new__(MistralLLMClassifier)
    classes = [
        ClassEntry(name="Thermodynamics & Lab", folder=Path("/tmp"), aliases=[]),
        ClassEntry(name="Circuits", folder=Path("/tmp"), aliases=[]),
    ]
    assert classifier._resolve_class("Thermodynamics & Lab", classes) == "Thermodynamics & Lab"
    assert classifier._resolve_class("Circuits", classes) == "Circuits"


def test_llm_partial_class_name():
    """LLM says 'Thermodynamics' but class is 'Thermodynamics & Lab' -> resolved."""
    from src.llm_fallback import MistralLLMClassifier
    classifier = object.__new__(MistralLLMClassifier)
    classes = [
        ClassEntry(name="Thermodynamics & Lab", folder=Path("/tmp"), aliases=[]),
    ]
    assert classifier._resolve_class("Thermodynamics", classes) == "Thermodynamics & Lab"


def test_llm_none_response():
    from src.llm_fallback import MistralLLMClassifier
    classifier = object.__new__(MistralLLMClassifier)
    classes = [ClassEntry(name="Thermo", folder=Path("/tmp"), aliases=[])]
    assert classifier._resolve_class("NONE", classes) == "NONE"
    assert classifier._resolve_class("none", classes) == "NONE"


def test_llm_hallucinated_class_name():
    """LLM returns a class that doesn't exist -> NONE."""
    from src.llm_fallback import MistralLLMClassifier
    classifier = object.__new__(MistralLLMClassifier)
    classes = [ClassEntry(name="Thermodynamics & Lab", folder=Path("/tmp"), aliases=[])]
    assert classifier._resolve_class("Quantum Physics", classes) == "NONE"


def test_llm_quoted_response():
    """LLM wraps the answer in quotes -> still resolves."""
    from src.llm_fallback import MistralLLMClassifier
    classifier = object.__new__(MistralLLMClassifier)
    classes = [ClassEntry(name="Circuits", folder=Path("/tmp"), aliases=[])]
    assert classifier._resolve_class('"Circuits"', classes) == "Circuits"


# --- Stage 3b fallback integration --- #

def test_alias_hit_no_llm_call(tmp_path):
    """When the alias map hits, the LLM is never called."""
    cfg = make_cfg(tmp_path)
    llm = FakeLLM(answer="Circuits")  # would be wrong if called
    g = make_group("thermo", "Thermo Notes")

    c = classify(g, cfg, llm=llm)

    assert c.is_hit
    assert c.matched_via == "alias"
    assert c.class_name == "Thermodynamics & Lab"
    assert llm.calls == [], "LLM must not be called on alias hit"


def test_llm_fallback_resolves(tmp_path):
    """Alias miss + LLM resolves to a class -> hit via LLM."""
    cfg = make_cfg(tmp_path)
    llm = FakeLLM(answer="Circuits")
    g = make_group("random notes", "Random Notes")

    c = classify(g, cfg, llm=llm)

    assert c.is_hit
    assert c.matched_via == "llm"
    assert c.class_name == "Circuits"
    assert llm.calls == ["Random Notes"]


def test_llm_fallback_none(tmp_path):
    """Alias miss + LLM says NONE -> Unsorted."""
    cfg = make_cfg(tmp_path)
    llm = FakeLLM(answer="NONE")
    g = make_group("random notes", "Random Notes")

    c = classify(g, cfg, llm=llm)

    assert not c.is_hit
    assert c.class_name == "Unsorted"
    assert c.matched_via == ""


def test_llm_fallback_hallucination(tmp_path):
    """Alias miss + LLM returns unknown class -> Unsorted (fail safe)."""
    cfg = make_cfg(tmp_path)
    llm = FakeLLM(answer="Quantum Physics")
    g = make_group("random notes", "Random Notes")

    c = classify(g, cfg, llm=llm)

    assert not c.is_hit
    assert c.class_name == "Unsorted"


def test_no_llm_falls_to_unsorted(tmp_path):
    """Alias miss + no LLM provided -> Unsorted (same as before)."""
    cfg = make_cfg(tmp_path)
    g = make_group("random notes", "Random Notes")

    c = classify(g, cfg, llm=None)

    assert not c.is_hit
    assert c.class_name == "Unsorted"


def test_llm_error_falls_to_unsorted(tmp_path):
    """Alias miss + LLM raises error -> Unsorted (graceful degradation)."""
    cfg = make_cfg(tmp_path)

    class FailingLLM:
        def classify(self, title, classes):
            raise LLMError("API down")

    g = make_group("random notes", "Random Notes")

    c = classify(g, cfg, llm=FailingLLM())

    assert not c.is_hit
    assert c.class_name == "Unsorted"


def test_classify_groups_with_llm(tmp_path):
    """Batch classification with LLM fallback."""
    cfg = make_cfg(tmp_path)
    llm = FakeLLM(answer="Circuits")
    groups = [
        make_group("thermo", "Thermo"),          # alias hit
        make_group("random", "Random"),          # LLM fallback
        make_group("circuits", "Circuits"),      # alias hit
    ]

    results = classify_groups(groups, cfg, llm=llm)

    assert len(results) == 3
    assert results[0][1].matched_via == "alias"
    assert results[1][1].matched_via == "llm"
    assert results[2][1].matched_via == "alias"
    assert llm.calls == ["Random"], "LLM should only be called on the miss"
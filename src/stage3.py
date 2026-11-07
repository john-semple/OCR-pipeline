"""Stage 3a — Multi-page grouping.
Stage 3b — Alias-map classification.

Groups OCR results into "notes". A group becomes a single Obsidian note
in Stage 4 (with `## Page N` sections if multi-page).

Two grouping scenarios:
  1. **Multi-page PDF** — a single PDF with multiple pages is already a
     grouped unit (Stage 1 returns one OCRResult with N pages). No
     cross-file heuristic needed; it's a group of one file.
  2. **Cross-file grouping** — separate single-page PDFs shot in quick
     succession with matching titles. Heuristic:
     - Normalize title (lowercase, strip punctuation/whitespace, strip
       continuation markers).
     - Pages join an existing group if ALL of:
       (a) normalized title matches the group's title,
       (b) capture_time is within `grouping_window_minutes` of the
           group's earliest capture_time,
       (c) it's a continuation (raw title has a marker) OR the first
           page (no marker needed).
     - No-marker same-title pages within the window are treated as
       continuations (handles "Thermo" shot 5 times in 5 minutes).

Group state is persisted to `Processing/groups/{group_id}.json` so a
crash mid-group is recoverable.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .logging_setup import get_logger
from .stage1 import OCRResult
from .stage2 import NoteMeta
from .llm_fallback import LLMClassifier

log = get_logger("stage3a")

# Continuation markers to strip from titles during normalization.
_CONTINUATION_PATTERNS = [
    r"\bcont(?:inued|d)?\b",
    r"\bpage\s+\d+\b",
    r"\bpg\.?\s*\d+\b",
    r"\bp\.?\s*\d+\b",
    r"\.\.\.+$",
]
_CONTINUATION_RE = re.compile("|".join(_CONTINUATION_PATTERNS), re.IGNORECASE)

# Marker detection (for the "is this a continuation" check).
_MARKER_RE = re.compile(
    r"\b(cont(?:inued|d)?|page\s+\d+|pg\.?\s*\d+|p\.?\s*\d+)\b",
    re.IGNORECASE,
)

_PUNCT_RE = re.compile(r"[^\w\s]")


@dataclass
class PageRef:
    """Reference to one OCR result within a group."""

    sha256: str
    source_name: str
    title: str           # raw OCR title
    capture_time: float
    num_pages: int       # pages within this PDF
    pdf_path: str        # path to the PDF in Processing/


@dataclass
class NoteGroup:
    """A group of one or more OCR results that become a single note."""

    group_id: str
    normalized_title: str
    pages: list[PageRef] = field(default_factory=list)

    @property
    def min_capture_time(self) -> float:
        return min(p.capture_time for p in self.pages) if self.pages else 0.0

    @property
    def is_multi_page(self) -> bool:
        """True if this group spans multiple PDFs or a multi-page PDF."""
        return len(self.pages) > 1 or (self.pages and self.pages[0].num_pages > 1)

    def to_json(self) -> dict:
        return {
            "group_id": self.group_id,
            "normalized_title": self.normalized_title,
            "pages": [
                {
                    "sha256": p.sha256,
                    "source_name": p.source_name,
                    "title": p.title,
                    "capture_time": p.capture_time,
                    "num_pages": p.num_pages,
                    "pdf_path": str(p.pdf_path),
                }
                for p in self.pages
            ],
        }

    @classmethod
    def from_json(cls, data: dict) -> NoteGroup:
        pages = [
            PageRef(
                sha256=p["sha256"],
                source_name=p["source_name"],
                title=p["title"],
                capture_time=p["capture_time"],
                num_pages=p["num_pages"],
                pdf_path=p["pdf_path"],
            )
            for p in data.get("pages", [])
        ]
        return cls(
            group_id=data["group_id"],
            normalized_title=data["normalized_title"],
            pages=pages,
        )


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation/whitespace, strip continuation markers."""
    t = _CONTINUATION_RE.sub("", title)
    t = t.lower()
    t = _PUNCT_RE.sub("", t)
    t = " ".join(t.split())
    return t


def has_continuation_marker(title: str) -> bool:
    """True if the raw title contains a continuation marker."""
    return bool(_MARKER_RE.search(title))


def group_results(
    results: list[OCRResult],
    metas: list[NoteMeta],
    cfg: Config,
) -> list[NoteGroup]:
    """Group OCR results into NoteGroups.

    Each result is paired with its meta (parallel lists, same order).
    Multi-page PDFs (num_pages > 1) form their own single-file group.
    Single-page PDFs are grouped cross-file by title + time + marker.

    Groups are persisted to `Processing/groups/{group_id}.json`.
    """
    assert len(results) == len(metas)
    window = cfg.settings.get("grouping_window_minutes", 30) * 60  # to seconds

    groups: list[NoteGroup] = []
    active: list[NoteGroup] = []  # groups still accepting new pages

    for result, meta in zip(results, metas):
        ref = PageRef(
            sha256=result.sha256,
            source_name=result.source_name,
            title=result.title,
            capture_time=meta.capture_time,
            num_pages=result.num_pages,
            pdf_path=str(result.pdf_path),
        )
        norm = normalize_title(result.title)

        # Multi-page PDFs are their own group (no cross-file grouping).
        if result.num_pages > 1:
            group = NoteGroup(
                group_id=str(uuid.uuid4()),
                normalized_title=norm,
                pages=[ref],
            )
            groups.append(group)
            log.info("stage3a: multi-page PDF %s -> group %s (%d internal pages)",
                     result.sha256[:12], group.group_id[:8], result.num_pages)
            continue

        # Cross-file grouping: try to join an active group.
        joined = False
        for g in active:
            if _can_join(ref, g, window):
                g.pages.append(ref)
                joined = True
                log.info("stage3a: %s joined group %s (now %d page(s))",
                         result.sha256[:12], g.group_id[:8], len(g.pages))
                break

        if not joined:
            # Start a new group.
            group = NoteGroup(
                group_id=str(uuid.uuid4()),
                normalized_title=norm,
                pages=[ref],
            )
            groups.append(group)
            active.append(group)
            log.info("stage3a: %s -> new group %s (title=%r)",
                     result.sha256[:12], group.group_id[:8], norm[:40])

    # Prune active groups whose window has expired (cleanup).
    active.clear()

    # Persist groups.
    _persist_groups(groups, cfg.paths.groups)

    log.info("stage3a: %d result(s) -> %d group(s)", len(results), len(groups))
    return groups


def _can_join(ref: PageRef, group: NoteGroup, window_seconds: float) -> bool:
    """Check if a page can join an existing group."""
    # (a) normalized title must match
    if normalize_title(ref.title) != group.normalized_title:
        return False
    # (b) within the time window
    if abs(ref.capture_time - group.min_capture_time) > window_seconds:
        return False
    # (c) continuation marker OR first page (always allowed)
    if len(group.pages) == 0:
        return True
    # No-marker same-title pages within the window are treated as
    # continuations (per the spec: "handles the user's inconsistent naming").
    return True


def _persist_groups(groups: list[NoteGroup], groups_dir: Path) -> None:
    """Write each group to `groups/{group_id}.json`."""
    groups_dir.mkdir(parents=True, exist_ok=True)
    # Clear old group files from a previous run.
    for old in groups_dir.glob("*.json"):
        old.unlink()
    for g in groups:
        path = groups_dir / f"{g.group_id}.json"
        path.write_text(json.dumps(g.to_json(), indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Stage 3b — Classification
# --------------------------------------------------------------------------- #

SHORT_ALIAS_MAX_LEN = 12  # aliases this short or shorter match as substrings

@dataclass
class Classification:
    """The result of classifying one group."""

    class_name: str        # matched class name, or "Unsorted"
    folder: Path           # destination folder for the note
    matched_alias: str     # which alias hit, or "" for a miss
    is_hit: bool           # True if a class was matched, False for Unsorted
    matched_via: str       # "alias" | "llm" | "" (miss)


def classify(group: NoteGroup, cfg: Config,
             llm: LLMClassifier | None = None) -> Classification:
    """Match a group's normalized title against the alias map in classes.yaml.

    Matching rules (per docs/pipeline.md Stage 3b):
      - Two passes: exact match first, then substring for short aliases.
        This ensures "thermodynamics" matches the exact alias
        "thermodynamics" before the short alias "thermo" matches it as
        a substring.
      - Substring match for short aliases (<= 12 chars): handles titles
        like "ME 301 - Lecture 5" matching alias "me 301". Only short
        aliases use substring to avoid false positives from long aliases.
      - First match wins within each pass (classes in YAML order).

    On a miss: if an LLM classifier is provided, ask it to pick a class.
    If the LLM resolves to a known class, use it (matched_via="llm").
    Otherwise: class_name="Unsorted", folder=Unsorted/, is_hit=False.
    The caller should set needs_review=True on the note's frontmatter.
    """
    title = group.normalized_title

    # Pass 1: exact match (any alias length).
    for entry in cfg.classes:
        for alias in entry.aliases:
            norm_alias = normalize_title(alias)
            if not norm_alias:
                continue
            if norm_alias == title:
                log.info("stage3b: HIT  %r -> %s (exact alias=%r)",
                         title[:40], entry.name, alias)
                return Classification(
                    class_name=entry.name,
                    folder=entry.folder,
                    matched_alias=alias,
                    is_hit=True,
                    matched_via="alias",
                )

    # Pass 2: substring match for short aliases only.
    for entry in cfg.classes:
        for alias in entry.aliases:
            norm_alias = normalize_title(alias)
            if not norm_alias:
                continue
            if len(norm_alias) <= SHORT_ALIAS_MAX_LEN and norm_alias in title:
                log.info("stage3b: HIT  %r -> %s (substring alias=%r)",
                         title[:40], entry.name, alias)
                return Classification(
                    class_name=entry.name,
                    folder=entry.folder,
                    matched_alias=alias,
                    is_hit=True,
                    matched_via="alias",
                )

    # Pass 3: LLM fallback (only if a classifier was provided).
    if llm is not None:
        try:
            raw_title = group.pages[0].title if group.pages else ""
            answer = llm.classify(raw_title, cfg.classes)
            if answer != "NONE":
                entry = next((c for c in cfg.classes if c.name == answer), None)
                if entry:
                    log.info("stage3b: LLM HIT  %r -> %s",
                             title[:40], entry.name)
                    return Classification(
                        class_name=entry.name,
                        folder=entry.folder,
                        matched_alias="",
                        is_hit=True,
                        matched_via="llm",
                    )
            log.info("stage3b: LLM said NONE for %r", title[:40])
        except Exception as exc:
            log.warning("stage3b: LLM fallback failed for %r: %s", title[:40], exc)

    log.info("stage3b: MISS %r -> Unsorted", title[:40])
    return Classification(
        class_name="Unsorted",
        folder=cfg.paths.unsorted,
        matched_alias="",
        is_hit=False,
        matched_via="",
    )


def _alias_matches(norm_alias: str, title: str) -> bool:
    """Check if a normalized alias matches a normalized title.

    Short aliases (<= SHORT_ALIAS_MAX_LEN chars) match as substrings
    to handle "me 301 - lecture 5" matching "me 301".
    Longer aliases match only exactly, to avoid false positives.
    """
    if len(norm_alias) <= SHORT_ALIAS_MAX_LEN:
        return norm_alias in title
    return norm_alias == title


def classify_groups(groups: list[NoteGroup], cfg: Config,
                    llm: LLMClassifier | None = None) -> list[tuple[NoteGroup, Classification]]:
    """Classify every group. Returns parallel list of (group, classification).

    If `llm` is provided, it's used as a fallback for groups that miss
    the alias map.
    """
    results = []
    for g in groups:
        c = classify(g, cfg, llm=llm)
        results.append((g, c))
    hits = sum(1 for _, c in results if c.is_hit)
    llm_hits = sum(1 for _, c in results if c.matched_via == "llm")
    log.info("stage3b: %d/%d groups classified (%d alias, %d llm), %d to Unsorted",
             hits, len(groups), hits - llm_hits, llm_hits, len(groups) - hits)
    return results
"""Classes.md parser — read the editable class list from the vault.

The vault contains a markdown file (`Classes.md`, synced to the phone so
it's editable in Obsidian) where each line defines a class:

    Class Name: alias, alias, alias

The text before the colon is the class name AND the folder name (a
folder of the same name is auto-created under the homework root on each
run). The comma-separated list after the colon is the keywords used to
sort scanned pages. Matching is case-insensitive (aliases are lowercased
on load, same as classes.yaml aliases).

Lines that are blank, headings, bullets, or inside fenced code
blocks are ignored, as are prose lines that happen to contain colons
(a class line needs at least one keyword, and its name can't look
like a sentence). The pipeline regenerates `Sorting Guide.md` from
this file on each run.
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import ClassEntry
from .logging_setup import get_logger

log = get_logger("classes_md")

CLASSES_MD_NAME = "Classes.md"

# A class line: non-colon text, colon, comma-separated keywords.
# An optional leading markdown heading ('#') is stripped, so both
# 'Dynamics: dyno' and '### Dynamics: dyno' parse.
_CLASS_LINE_RE = re.compile(r"^\s*(?:#+\s*)?([^:]+?)\s*:\s*(.*)$")

# Names matching these are prose, not class lines: leading list/quote
# markers, sentence punctuation anywhere, digits+dot list numbering,
# inline code (backticks), or over-long text. Class names shouldn't
# contain any of those.
_NOT_A_CLASS_RE = re.compile(r"^[-*>]|\d+[.)]\s|[.?!\`]")


def _is_prose(name: str) -> bool:
    """Heuristics for ignoring prose lines that happen to contain colons."""
    if len(name) > 80:
        return True
    return bool(_NOT_A_CLASS_RE.search(name))


def parse_classes_md(path: Path, homework_root: Path) -> list[ClassEntry]:
    """Parse a Classes.md file into ClassEntry objects.

    Returns entries with `folder` = homework_root / {class name}.
    Lines that don't match the `Name: alias, alias` pattern are ignored.
    Duplicate class names are merged (aliases unioned, first occurrence
    wins for order).
    """
    if not path.exists():
        log.info("classes_md: %s doesn't exist yet, no classes loaded", path.name)
        return []

    entries: dict[str, ClassEntry] = {}
    order: list[str] = []

    in_code_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence or not stripped:
            continue

        match = _CLASS_LINE_RE.match(stripped)
        if not match:
            continue
        name = match.group(1).strip()
        if not name or _is_prose(name):
            continue
        aliases_raw = [a.strip().lower() for a in match.group(2).split(",") if a.strip()]
        if not aliases_raw:
            # A "Name:" line with no keywords is prose (e.g. a lead-in
            # sentence like "Below are your current classes:").
            continue

        if name in entries:
            # Merge: union of aliases, keep first-seen order.
            existing = entries[name]
            merged = list(dict.fromkeys(existing.aliases + aliases_raw))
            entries[name] = ClassEntry(
                name=name, folder=existing.folder, aliases=merged
            )
            log.debug("classes_md: merged duplicate class %r", name)
        else:
            entries[name] = ClassEntry(
                name=name,
                folder=homework_root / name,
                aliases=aliases_raw,
            )
            order.append(name)

    result = [entries[name] for name in order]
    if result:
        log.info("classes_md: loaded %d class(es) from %s", len(result), path.name)
    else:
        log.warning("classes_md: no class lines found in %s", path.name)
    return result


def ensure_class_folders(classes: list[ClassEntry]) -> None:
    """Create each class's folder under the homework root if missing.

    Called on every pipeline run so a class added to Classes.md gets its
    folder immediately, before any scan needs it.
    """
    for entry in classes:
        entry.folder.mkdir(parents=True, exist_ok=True)


def prune_class_folders(classes: list[ClassEntry], homework_root: Path) -> None:
    """Delete empty class folders for classes removed from Classes.md.

    Safe by design: a folder is only deleted if it is a direct child of
    the homework root, is NOT a current class folder, is NOT one of the
    `00`-prefixed pipeline/scaffold dirs (or Classes.md / Sorting
    Guide.md), and contains NOTHING (no .md, no PNG, no hidden files).
    If a removed class still has notes, its folder is kept and a
    warning is logged — you clean it up by hand.
    """
    current = {entry.folder for entry in classes}
    protected_names = {CLASSES_MD_NAME, "Sorting Guide.md"}
    protected_prefixes = ("00", ".")

    for child in homework_root.iterdir():
        if not child.is_dir():
            continue
        if child in current:
            continue
        if child.name.startswith(protected_prefixes) or child.name in protected_names:
            continue
        if any(child.iterdir()):
            log.warning(
                "classes_md: %r was removed from Classes.md but its folder "
                "still has content — leaving it in place", child.name,
            )
            continue
        child.rmdir()
        log.info("classes_md: removed empty folder %r (class no longer in Classes.md)",
                 child.name)
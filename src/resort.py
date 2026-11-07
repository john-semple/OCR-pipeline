"""resort Unsorted/ — re-classify notes after editing classes.yaml.

After you add new aliases (or the LLM fallback is enabled), re-sort
everything sitting in Unsorted/ against the updated classification.

Usage:
    python3 run.py --resort

What it does:
  1. Reads every per-scan folder in Unsorted/ (each contains a .md +
     loose diagram PNGs).
  2. Extracts the title from the .md (first non-frontmatter, non-empty
     line).
  3. Re-classifies using the alias map + LLM fallback (same as Stage 3b).
  4. On a hit: moves the entire per-scan folder to the class folder,
     updates frontmatter (class, needs_review -> false).
  5. On a miss: leaves the per-scan folder in Unsorted (nothing changed).

Diagrams: since images are loose next to the .md inside the per-scan
folder, moving the whole folder moves them too. Wikilinks
`![[diag.png]]` don't need rewriting because the .md and PNGs remain
siblings.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .config import Config
from .logging_setup import get_logger
from .stage3 import normalize_title, classify, NoteGroup, PageRef

log = get_logger("resort")

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def resort_unsorted(cfg: Config, llm=None) -> list[tuple[Path, str]]:
    """Re-sort all notes in Unsorted/. Returns list of (note_path, new_class).

    Only notes that are successfully re-classified and moved are returned.
    Misses stay in Unsorted and are not in the returned list.
    """
    unsorted_dir = cfg.paths.unsorted
    if not unsorted_dir.exists():
        log.info("resort: Unsorted/ doesn't exist, nothing to do")
        return []

    # Each scan is a per-scan folder containing a .md + loose PNGs.
    scan_folders = sorted(d for d in unsorted_dir.iterdir() if d.is_dir())
    scan_folders = [d for d in scan_folders if d.name != "Sorting Guide.md"]

    if not scan_folders:
        log.info("resort: no scan folders in Unsorted/")
        return []

    log.info("resort: found %d scan folder(s) in Unsorted/", len(scan_folders))
    moved: list[tuple[Path, str]] = []

    for scan_folder in scan_folders:
        # Find the .md inside the per-scan folder.
        md_files = list(scan_folder.glob("*.md"))
        if not md_files:
            log.warning("resort: no .md in %s, skipping", scan_folder.name)
            continue
        note_path = md_files[0]

        content = note_path.read_text(encoding="utf-8")
        title = _extract_title_from_note(content)
        if not title:
            log.warning("resort: could not extract title from %s, skipping", note_path.name)
            continue

        norm_title = normalize_title(title)
        group = NoteGroup(
            group_id="resort",
            normalized_title=norm_title,
            pages=[PageRef("resort", note_path.name, title, 0, 1, str(note_path))],
        )

        classification = classify(group, cfg, llm=llm)

        if not classification.is_hit:
            log.info("resort: MISS %r stays in Unsorted", title[:40])
            continue

        # Move the entire per-scan folder to the class folder.
        dest_folder = classification.folder
        dest_folder.mkdir(parents=True, exist_ok=True)

        # Handle folder collision in the class folder.
        dest_scan = dest_folder / scan_folder.name
        if dest_scan.exists():
            stem = scan_folder.name
            n = 2
            while (dest_folder / f"{stem}-{n}").exists():
                n += 1
            dest_scan = dest_folder / f"{stem}-{n}"

        # Read the .md (may have been renamed for collision avoidance).
        dest_note = dest_scan / note_path.name
        if dest_scan.name != scan_folder.name:
            # Folder was suffixed; rename the .md to match.
            dest_note = dest_scan / f"{dest_scan.name}.md"

        # Update frontmatter in the content before writing.
        new_content = _update_frontmatter(content, classification)

        # Move the folder (brings the .md + all PNGs along).
        dest_scan.mkdir(parents=True, exist_ok=True)
        for item in scan_folder.iterdir():
            if item.name == note_path.name and dest_note.name != note_path.name:
                # Rename .md to match the suffixed folder name.
                shutil.move(str(item), str(dest_note))
            else:
                shutil.move(str(item), str(dest_scan / item.name))
        scan_folder.rmdir()

        # Write the updated content to the destination .md.
        dest_note.write_text(new_content, encoding="utf-8")

        log.info("resort: MOVED %s -> %s (%s)",
                 scan_folder.name, dest_scan.relative_to(cfg.paths.vault_root),
                 classification.class_name)

        moved.append((dest_note, classification.class_name))

    log.info("resort: moved %d/%d scan folder(s)", len(moved), len(scan_folders))
    return moved


def _extract_title_from_note(content: str) -> str:
    """Extract the title from a note — the first non-frontmatter, non-empty line."""
    # Strip frontmatter if present.
    body = _FRONTMATTER_RE.sub("", content, count=1)
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _update_frontmatter(content: str, classification) -> str:
    """Update the class and needs_review fields in frontmatter."""
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return content

    fm = match.group(1)
    # Replace class: line.
    fm = re.sub(r'^class: .*$', f'class: "{classification.class_name}"',
                fm, count=1, flags=re.MULTILINE)
    # Replace needs_review: line.
    fm = re.sub(r'^needs_review: .*$', f'needs_review: false',
                fm, count=1, flags=re.MULTILINE)

    return f"---\n{fm}\n---\n" + content[match.end():]
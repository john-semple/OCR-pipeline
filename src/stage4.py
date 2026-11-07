"""Stage 4 — Write note + inline diagram attachments + frontmatter.

Takes a classified NoteGroup and writes it as a single Obsidian markdown
note into a per-scan folder inside the class folder (or Unsorted/).
Diagram images extracted by Mistral OCR are saved as PNG files loose in
the same per-scan folder (alongside the .md), and the OCR markdown's
image placeholders are rewritten to Obsidian wikilinks pointing at them.

Per-scan folder layout:
  {Class}/{date} {slug}/
    {date} {slug}.md
    {date} {slug}-diag0.png
    {date} {slug}-diag1.png
    ...

Note structure:
  - Frontmatter: class, date, source_scan, scan_hash, ocr_confidence,
    tags, needs_review (per docs/decisions.md).
  - Body: for single-page groups, the page's markdown. For multi-page
    groups, a `## Page N` section per page in capture-time order.
  - Diagrams: saved as `{date} {slug}[-p{page}]-diag{n}.png` next to
    the .md, referenced as `![[{diag_name}]]` (Obsidian wikilink).
  - Filename: `{date} {title-slug}.md` inside a folder of the same
    name. On folder collision, both folder and .md get `-2`, `-3`
    suffixes.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from .config import Config
from .logging_setup import get_logger
from .stage1 import OCRResult, OCRPage
from .stage2 import NoteMeta
from .stage3 import NoteGroup, Classification

log = get_logger("stage4")

# Regex to find Mistral's image placeholders: ![img-0.jpeg](img-0.jpeg)
_IMAGE_PLACEHOLDER_RE = re.compile(r"!\[([^\]]+)\]\(([^)]+)\)")
# Keep filename-safe: alphanumerics, hyphens, spaces. Replace rest with -.
_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def _slugify(text: str, max_len: int = 60) -> str:
    """Make a filesystem-safe slug from a title."""
    slug = _SLUG_RE.sub("-", text.strip()).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug or "untitled"


def _unique_path(path: Path) -> Path:
    """If path exists, append -2, -3, etc. to the stem."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    n = 2
    while True:
        candidate = parent / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _unique_folder(folder: Path) -> Path:
    """If folder exists, append -2, -3, etc. to the directory name."""
    if not folder.exists():
        return folder
    name = folder.name
    parent = folder.parent
    n = 2
    while True:
        candidate = parent / f"{name}-{n}"
        if not candidate.exists():
            return candidate
        n += 1


def _save_diagram(img_base64: str | None, dest: Path) -> bool:
    """Decode base64 image data and save to dest. Returns False if no data."""
    if not img_base64:
        return False
    try:
        raw = base64.b64decode(img_base64)
        dest.write_bytes(raw)
        return True
    except Exception as exc:
        log.warning("stage4: failed to decode/save diagram %s: %s", dest.name, exc)
        return False


def _rewrite_images(page: OCRPage, scan_folder: Path, filename_prefix: str,
                    is_multi: bool, page_num: int) -> str:
    """Rewrite image placeholders in page markdown to point at saved files.

    Saves each diagram as a PNG loose in the scan folder (next to the
    .md) and replaces the placeholder with an Obsidian wikilink.
    """
    markdown = page.markdown
    if not page.images:
        return markdown

    diag_n = 0
    for img in page.images:
        # Mistral placeholder: ![img-0.jpeg](img-0.jpeg)
        placeholder_pattern = f"![{img.id}]({img.id})"
        if placeholder_pattern not in markdown:
            # Try a looser regex match for this image id
            pattern = re.compile(r"!\[" + re.escape(img.id) + r"\]\([^)]+\)")
        else:
            pattern = None

        suffix = ".png"
        if is_multi:
            diag_name = f"{filename_prefix} - p{page_num}-diag{diag_n}{suffix}"
        else:
            diag_name = f"{filename_prefix}-diag{diag_n}{suffix}"
        diag_path = scan_folder / diag_name

        if _save_diagram(img.image_base64, diag_path):
            replacement = f"![[{diag_name}]]"
            if pattern:
                markdown = pattern.sub(replacement, markdown, count=1)
            else:
                markdown = markdown.replace(placeholder_pattern, replacement, 1)
            log.info("stage4: saved diagram %s", diag_name)
            diag_n += 1
        # If save failed, leave the placeholder as-is.

    return markdown


def _build_frontmatter(classification: Classification, meta: NoteMeta,
                       source_name: str, sha256: str, num_pages: int) -> str:
    """Build YAML frontmatter for a note."""
    tags = "[needs-tagging]"  # Sprint 1 placeholder
    # Quote the class name so special chars (&, etc.) don't cause YAML
    # or Dataview parsing issues. "Unsorted" gets quoted too for consistency.
    class_name = classification.class_name
    lines = [
        "---",
        f'class: "{class_name}"',
        f"date: {meta.date}",
        f"source_scan: 00 Pipeline/Archive/{meta.date}-{_slugify(source_name)}.pdf",
        f"scan_hash: sha256:{sha256}",
        f"ocr_confidence: null",
        f"tags: {tags}",
        f"needs_review: {str(meta.needs_review or not classification.is_hit).lower()}",
        f"pages: {num_pages}",
        "---",
    ]
    return "\n".join(lines) + "\n"


def write_note(
    group: NoteGroup,
    classification: Classification,
    meta: NoteMeta,
    ocr_results: dict[str, OCRResult],
    cfg: Config,
) -> Path:
    """Write a single note for a NoteGroup. Returns the path to the .md file.

    `ocr_results` maps sha256 -> OCRResult, so we can look up the OCR
    pages for each PageRef in the group.
    """
    # Ensure destination folder exists.
    dest_folder = classification.folder
    dest_folder.mkdir(parents=True, exist_ok=True)

    # Build the note title from the group's first page's raw title
    # (the normalized title is for matching; the raw title is human-readable).
    first_result = ocr_results[group.pages[0].sha256]
    raw_title = first_result.title or "Untitled"
    slug = _slugify(raw_title)
    date_str = meta.date

    # Build filename prefix for diagrams.
    filename_prefix = f"{date_str} {slug}"

    is_multi = group.is_multi_page

    # Create the per-scan folder: {Class}/{date} {slug}/
    # On collision, the folder gets -2, -3 suffixes. The .md inside
    # matches the folder name.
    scan_folder_name = f"{date_str} {slug}"
    scan_folder = _unique_folder(dest_folder / scan_folder_name)
    scan_folder.mkdir(parents=True, exist_ok=True)

    # Build the note body.
    body_parts: list[str] = []

    # Sort pages by capture_time for multi-page groups.
    sorted_pages = sorted(group.pages, key=lambda p: p.capture_time)

    for idx, page_ref in enumerate(sorted_pages):
        result = ocr_results[page_ref.sha256]

        if is_multi and len(result.pages) == 1:
            # Cross-file multi-page: one page per PDF, add ## Page N header.
            page = result.pages[0]
            page_num = idx + 1
            rewritten = _rewrite_images(
                page, scan_folder, filename_prefix, is_multi, page_num
            )
            body_parts.append(f"## Page {page_num}\n\n{rewritten}")
        elif is_multi and len(result.pages) > 1:
            # Multi-page PDF: each internal page gets a ## Page N header.
            for page in result.pages:
                page_num = page.index + 1
                rewritten = _rewrite_images(
                    page, scan_folder, filename_prefix, is_multi, page_num
                )
                body_parts.append(f"## Page {page_num}\n\n{rewritten}")
        else:
            # Single-page note.
            page = result.pages[0]
            rewritten = _rewrite_images(
                page, scan_folder, filename_prefix, is_multi, 1
            )
            body_parts.append(rewritten)

    body = "\n\n".join(body_parts)

    # Build frontmatter.
    # For the scan_hash, use the first page's hash.
    primary_sha = group.pages[0].sha256
    source_name = group.pages[0].source_name or f"{primary_sha}.pdf"
    num_pages = sum(p.num_pages for p in group.pages)

    frontmatter = _build_frontmatter(
        classification, meta, source_name, primary_sha, num_pages
    )

    # Assemble the note.
    note_content = frontmatter + "\n" + body + "\n"

    # Write the file. The .md goes inside the per-scan folder, with a
    # name matching the folder name.
    note_filename = f"{scan_folder.name}.md"
    note_path = scan_folder / note_filename
    note_path.write_text(note_content, encoding="utf-8")

    log.info("stage4: wrote %s (%d chars, %d page(s))",
             note_path.relative_to(cfg.paths.vault_root),
             len(note_content), num_pages)

    return note_path


def write_notes(
    classified: list[tuple[NoteGroup, Classification]],
    metas: dict[str, NoteMeta],
    ocr_results: dict[str, OCRResult],
    cfg: Config,
) -> list[Path]:
    """Write notes for all classified groups. Returns paths to the .md files.

    `metas` maps sha256 -> NoteMeta.
    `ocr_results` maps sha256 -> OCRResult.
    """
    paths = []
    for group, classification in classified:
        # Use the first page's meta (date/capture_time are per-group).
        first_sha = group.pages[0].sha256
        if first_sha not in metas:
            log.warning("stage4: no meta for %s, skipping", first_sha[:12])
            continue
        meta = metas[first_sha]

        note_path = write_note(group, classification, meta, ocr_results, cfg)
        paths.append(note_path)

    log.info("stage4: wrote %d note(s)", len(paths))
    return paths
"""Alias README generator — writes a human-readable keyword reference to the vault.

Reads the loaded class config and writes a markdown file to the vault
showing which keywords route to which class. So you can look at one
place and see: "thermo, thermodynamics, me 301 -> Thermodynamics & Lab".

Classes are defined in `Classes.md` (in the vault, editable in
Obsidian). Editing `Classes.md` and re-running the pipeline regenerates
this file. The file lives at `Homework and Class Notes/Sorting Guide.md`
in the vault so it's visible in Obsidian on your phone.
"""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .logging_setup import get_logger

log = get_logger("alias_readme")

README_NAME = "Sorting Guide.md"


def generate_alias_readme(cfg: Config) -> Path:
    """Write the sorting guide markdown to the vault. Returns the path."""
    dest = cfg.paths.homework_root / README_NAME

    lines: list[str] = [
        "# Sorting Guide",
        "",
        "This file is **auto-generated** by the OCR pipeline on each run.",
        "Edit `Classes.md` (next to this file) to add or change classes and",
        "keywords, then re-run the pipeline to update this file.",
        "",
        "## Keywords → Class",
        "",
        "The pipeline reads only the **first line** of each scanned page",
        "(the title you write at the top). If the title contains any of a",
        "class's keywords, the note goes to that class folder. If no",
        "keyword matches, an AI fallback tries to guess — and if that",
        "fails too, the note goes to `Unsorted/` for manual review.",
        "",
    ]

    if not cfg.classes:
        lines.append("*(No classes configured yet — edit `Classes.md`)*")
        lines.append("")
    else:
        for entry in cfg.classes:
            folder_rel = entry.folder.relative_to(cfg.paths.vault_root) if entry.folder.is_relative_to(cfg.paths.vault_root) else entry.folder
            lines.append(f"### {entry.name}")
            lines.append(f"**Folder:** `{folder_rel}`")
            lines.append("")
            if entry.aliases:
                lines.append("Keywords (title matches any of these):")
                lines.append("")
                for alias in entry.aliases:
                    lines.append(f"- `{alias}`")
            else:
                lines.append("*(No keywords configured — add aliases in `Classes.md`)*")
            lines.append("")

    lines.extend([
        "## How matching works",
        "",
        "1. **Exact match** — the title exactly equals a keyword (after",
        "   lowercasing + stripping punctuation). Always checked first.",
        "2. **Substring match** — for short keywords (≤12 characters),",
        "   the title just needs to *contain* the keyword. So `thermo`",
        "   matches `\"hw 3 thermo lecture notes\"`. Longer keywords",
        "   only match exactly, to avoid false positives.",
        "3. **AI fallback** — if no keyword matches, the pipeline asks",
        "   `mistral-small-latest` to pick the best class from the list",
        "   above. If the AI can't decide, the note goes to Unsorted.",
        "",
        "## Adding a new keyword",
        "",
        "1. Open `Classes.md` (in this folder, or on your phone).",
        "2. Add the keyword to the class's line after the colon,",
        "   comma-separated.",
        "3. Re-run the pipeline (`python3 run.py`) or wait for the next",
        "   auto-run. This file updates automatically.",
        "",
        "## Adding a new class",
        "",
        "1. Open `Classes.md` and add a line:",
        "   `New Class: keyword1, keyword2`.",
        "2. Re-run the pipeline — the class folder is created",
        "   automatically in this directory, and this file updates.",
        "",
    ])

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines), encoding="utf-8")
    log.info("alias_readme: wrote %s", dest.relative_to(cfg.paths.vault_root))
    return dest
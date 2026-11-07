"""Tests for Classes.md parsing — the vault-editable class list."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.classes_md import (
    parse_classes_md, ensure_class_folders, prune_class_folders,
    CLASSES_MD_NAME,
)
from src.config import ClassEntry


def write_md(tmp_path: Path, content: str) -> Path:
    p = tmp_path / CLASSES_MD_NAME
    p.write_text(content, encoding="utf-8")
    return p


# --- parsing --- #

def test_basic_line(tmp_path):
    p = write_md(tmp_path, "Dynamics: dyno, dynamics, esg36\n")
    entries = parse_classes_md(p, tmp_path)
    assert len(entries) == 1
    assert entries[0].name == "Dynamics"
    assert entries[0].folder == tmp_path / "Dynamics"
    assert entries[0].aliases == ["dyno", "dynamics", "esg36"]


def test_aliases_lowercased(tmp_path):
    p = write_md(tmp_path, "Dynamics: Dyno, DYNAMICS, ESG36\n")
    entries = parse_classes_md(p, tmp_path)
    assert entries[0].aliases == ["dyno", "dynamics", "esg36"]


def test_prose_and_headings_ignored(tmp_path):
    p = write_md(tmp_path, (
        "# Classes\n"
        "\n"
        "Some intro prose. Edit freely.\n"
        "\n"
        "Dynamics: dyno, dynamics\n"
        "\n"
        "## Notes\n"
        "- a bullet that shouldn't parse\n"
        "\n"
        "Here's how to add a class. Edit this file to manage your classes: one per line\n"
        "Below are your current classes:\n"
    ))
    entries = parse_classes_md(p, tmp_path)
    # Prose lines with colons are ignored; only the real class line parses.
    assert [e.name for e in entries] == ["Dynamics"]


def test_bullet_lines_ignored(tmp_path):
    p = write_md(tmp_path, (
        "- To add a new class: add a line like dynamics\n"
        "* Star bullet: with colon\n"
        "> Quote: with colon\n"
        "Real Class: real\n"
    ))
    entries = parse_classes_md(p, tmp_path)
    assert [e.name for e in entries] == ["Real Class"]


def test_inline_code_ignored(tmp_path):
    # Prose using inline code formatting for examples must not parse.
    p = write_md(tmp_path, (
        "To add one, write `Dynamics: dyno, dynamics` and save.\n"
        "Real Class: real\n"
    ))
    entries = parse_classes_md(p, tmp_path)
    assert [e.name for e in entries] == ["Real Class"]


def test_sentence_punctuation_ignored(tmp_path):
    p = write_md(tmp_path, (
        "This sentence ends with a period. So this part: is prose\n"
        "Does a question parse? No: it doesn't\n"
        "Real Class: real\n"
    ))
    entries = parse_classes_md(p, tmp_path)
    assert [e.name for e in entries] == ["Real Class"]


def test_missing_file_returns_empty(tmp_path):
    entries = parse_classes_md(tmp_path / "nope.md", tmp_path)
    assert entries == []


def test_code_fence_ignored(tmp_path):
    p = write_md(tmp_path, (
        "Here's how to add a class.\n"
        "```\n"
        "Fake Class: fake\n"
        "```\n"
        "Real Class: real\n"
    ))
    entries = parse_classes_md(p, tmp_path)
    # Prose line without a colon is ignored; the fenced 'Fake Class'
    # is ignored; 'Real Class' parses.
    assert [e.name for e in entries] == ["Real Class"]


def test_duplicate_class_merged(tmp_path):
    p = write_md(tmp_path, (
        "Dynamics: dyno, dynamics\n"
        "Dynamics: esg36\n"
    ))
    entries = parse_classes_md(p, tmp_path)
    assert len(entries) == 1
    assert entries[0].aliases == ["dyno", "dynamics", "esg36"]


def test_heading_prefix_stripped(tmp_path):
    # A class line written as a markdown heading still parses.
    p = write_md(tmp_path, "### Dynamics: dyno, dynamics\n")
    entries = parse_classes_md(p, tmp_path)
    assert entries[0].name == "Dynamics"


def test_no_keywords_is_prose(tmp_path):
    # "Name:" with nothing after the colon is treated as prose
    # (e.g. "Below are your current classes:").
    p = write_md(tmp_path, "Dynamics:\n")
    entries = parse_classes_md(p, tmp_path)
    assert entries == []


# --- config integration --- #

def test_classes_md_wins_over_yaml(tmp_path, monkeypatch):
    # Build a fake classes.yaml + Classes.md; the md must win.
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    vault = tmp_path / "vault"
    hw = vault / "Resources/Homework and Class Notes"
    hw.mkdir(parents=True)
    yaml_content = (
        "paths:\n"
        f"  vault_root: {vault}\n"
        "classes:\n"
        "  - name: YamlClass\n"
        "    folder: Resources/Homework and Class Notes/YamlClass\n"
        "    aliases: [yamlalias]\n"
    )
    (repo / "config" / "classes.yaml").write_text(yaml_content)
    (hw / CLASSES_MD_NAME).write_text("MdClass: mdalias\n")

    from src.config import load_config
    monkeypatch.setattr("src.config.CONFIG_PATH", repo / "config" / "classes.yaml")
    cfg = load_config()

    assert len(cfg.classes) == 1
    assert cfg.classes[0].name == "MdClass"
    assert cfg.classes[0].folder == hw / "MdClass"
    assert cfg.classes[0].aliases == ["mdalias"]


def test_yaml_fallback_when_no_md(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    yaml_content = (
        "paths:\n"
        f"  vault_root: {tmp_path / 'vault'}\n"
        "classes:\n"
        "  - name: YamlClass\n"
        "    folder: Resources/Homework and Class Notes/YamlClass\n"
        "    aliases: [YamlAlias]\n"
    )
    (repo / "config" / "classes.yaml").write_text(yaml_content)

    vault = tmp_path / "vault"
    hw = vault / "Resources/Homework and Class Notes"
    hw.mkdir(parents=True)
    # No Classes.md — yaml fallback should kick in.

    from src.config import load_config
    monkeypatch.setattr("src.config.CONFIG_PATH", repo / "config" / "classes.yaml")
    cfg = load_config()

    assert len(cfg.classes) == 1
    assert cfg.classes[0].name == "YamlClass"
    assert cfg.classes[0].aliases == ["yamlalias"]


# --- folder creation --- #

def test_ensure_class_folders_creates(tmp_path):
    entries = [
        ClassEntry(name="Dynamics", folder=tmp_path / "Dynamics", aliases=["dyno"]),
        ClassEntry(name="Circuits", folder=tmp_path / "Circuits", aliases=["circ"]),
    ]
    ensure_class_folders(entries)
    assert (tmp_path / "Dynamics").is_dir()
    assert (tmp_path / "Circuits").is_dir()


def test_ensure_class_folders_idempotent(tmp_path):
    entries = [ClassEntry(name="Dynamics", folder=tmp_path / "Dynamics", aliases=[])]
    ensure_class_folders(entries)
    ensure_class_folders(entries)  # no error on second call
    assert (tmp_path / "Dynamics").is_dir()


# --- prune_class_folders --- #

def _hw_scaffold(tmp_path: Path) -> Path:
    """Homework root with the pipeline scaffold + Classes.md, like the vault."""
    hw = tmp_path / "Homework and Class Notes"
    (hw / "00 Pipeline" / "Processing").mkdir(parents=True)
    (hw / "00 Inbox").mkdir()
    (hw / "00 Pipeline" / "Unsorted").mkdir()
    (hw / CLASSES_MD_NAME).write_text("Thermo: thermo\n")
    return hw


def test_prune_removes_empty_folder_of_removed_class(tmp_path):
    hw = _hw_scaffold(tmp_path)
    # Dynamics was a class; its folder is now empty after removal.
    (hw / "Dynamics").mkdir()
    classes = [ClassEntry(name="Thermo", folder=hw / "Thermo", aliases=["thermo"])]

    prune_class_folders(classes, hw)

    assert not (hw / "Dynamics").exists()


def test_prune_keeps_folder_with_content(tmp_path):
    hw = _hw_scaffold(tmp_path)
    # Removed class, but the folder still has a note in it.
    (hw / "Dynamics" / "2026-09-01 notes").mkdir(parents=True)
    (hw / "Dynamics" / "2026-09-01 notes" / "2026-09-01 notes.md").write_text("x")
    classes = [ClassEntry(name="Thermo", folder=hw / "Thermo", aliases=["thermo"])]

    prune_class_folders(classes, hw)

    assert (hw / "Dynamics").exists(), "folder with notes must never be deleted"


def test_prune_keeps_hidden_files_only_folder(tmp_path):
    # Even a .gitkeep counts as content — don't delete.
    hw = _hw_scaffold(tmp_path)
    (hw / "Dynamics").mkdir()
    (hw / "Dynamics" / ".gitkeep").write_text("")
    classes = [ClassEntry(name="Thermo", folder=hw / "Thermo", aliases=["thermo"])]

    prune_class_folders(classes, hw)

    assert (hw / "Dynamics").exists()


def test_prune_never_touches_current_class_folders(tmp_path):
    hw = _hw_scaffold(tmp_path)
    classes = [
        ClassEntry(name="Thermo", folder=hw / "Thermo", aliases=["thermo"]),
        ClassEntry(name="Circuits", folder=hw / "Circuits", aliases=["circuits"]),
    ]
    ensure_class_folders(classes)
    (hw / "Circuits").rmdir()  # make it empty-able... recreate empty instead
    (hw / "Circuits").mkdir()

    prune_class_folders(classes, hw)

    assert (hw / "Thermo").exists()
    assert (hw / "Circuits").exists()


def test_prune_never_touches_pipeline_dirs(tmp_path):
    hw = _hw_scaffold(tmp_path)
    classes = [ClassEntry(name="Thermo", folder=hw / "Thermo", aliases=["thermo"])]

    prune_class_folders(classes, hw)

    assert (hw / "00 Pipeline").exists()
    assert (hw / "00 Inbox").exists()
    assert (hw / CLASSES_MD_NAME).exists()


def test_prune_ignores_files_in_homework_root(tmp_path):
    # Random .md files in the root (Sorting Guide etc.) are not dirs — ignored.
    hw = _hw_scaffold(tmp_path)
    (hw / "Sorting Guide.md").write_text("generated")
    (hw / "My personal note.md").write_text("dont delete me")
    classes = [ClassEntry(name="Thermo", folder=hw / "Thermo", aliases=["thermo"])]

    prune_class_folders(classes, hw)

    assert (hw / "Sorting Guide.md").exists()
    assert (hw / "My personal note.md").exists()
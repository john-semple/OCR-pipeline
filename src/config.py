"""Configuration loading + path resolution for the OCR pipeline.

Reads `config/classes.yaml` from the repo root, then resolves every
pipeline path against a single vault root. Any path key in `classes.yaml`
under `paths:` can be overridden by an environment variable of the same
name (uppercased, `OCR_` prefix optional) — e.g. `OCR_VAULT_ROOT` or
`VAULT_ROOT` overrides `vault_root`.

Classes come from the vault's `Classes.md` (one `Class Name: alias,
alias` line per class, editable in Obsidian / synced to the phone). If
that file is missing or has no class lines, the `classes:` section in
`classes.yaml` is used as a fallback.

The resolved Paths object is the single source of truth for every stage.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "classes.yaml"


@dataclass(frozen=True)
class Paths:
    vault_root: Path
    homework_root: Path
    inbox: Path
    pipeline_root: Path
    processing: Path
    groups: Path
    archive: Path
    unsorted: Path
    manual_retry: Path
    failed: Path
    openscan_inbox: Path
    stability_seconds: int

    def ensure_dirs(self) -> None:
        for p in (
            self.homework_root,
            self.inbox,
            self.pipeline_root,
            self.processing,
            self.groups,
            self.archive,
            self.unsorted,
            self.manual_retry,
            self.failed,
        ):
            p.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class ClassEntry:
    name: str
    folder: Path
    aliases: list[str]


@dataclass(frozen=True)
class Config:
    paths: Paths
    classes: list[ClassEntry]
    settings: dict
    raw: dict


def _env_override(key: str, default: str) -> str:
    for env_name in (f"OCR_{key.upper()}", key.upper()):
        val = os.environ.get(env_name)
        if val:
            return val
    return default


def load_config(config_path: Path | None = None) -> Config:
    config_path = config_path or CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    paths_raw = raw.get("paths", {}) or {}
    vault_root = Path(_env_override("vault_root", paths_raw.get("vault_root", "/home/ubuntu/Vault")))
    homework_subdir = _env_override("homework_subdir", paths_raw.get("homework_subdir", "Resources/Homework and Class Notes"))

    homework_root = vault_root / homework_subdir
    inbox = homework_root / _env_override("inbox_subdir", paths_raw.get("inbox_subdir", "00 Inbox"))
    pipeline_root = homework_root / _env_override("pipeline_subdir", paths_raw.get("pipeline_subdir", "00 Pipeline"))
    processing = pipeline_root / _env_override("processing_subdir", paths_raw.get("processing_subdir", "Processing"))
    groups = processing / _env_override("groups_subdir", paths_raw.get("groups_subdir", "groups"))
    archive = pipeline_root / _env_override("archive_subdir", paths_raw.get("archive_subdir", "Archive"))
    unsorted = pipeline_root / _env_override("unsorted_subdir", paths_raw.get("unsorted_subdir", "Unsorted"))
    manual_retry = pipeline_root / _env_override("manual_retry_subdir", paths_raw.get("manual_retry_subdir", "Manual Retry"))
    failed = pipeline_root / _env_override("failed_subdir", paths_raw.get("failed_subdir", "Failed"))
    openscan_inbox = Path(_env_override("openscan_inbox", paths_raw.get("openscan_inbox", "/mnt/openscan-inbox")))
    stability_seconds = int(_env_override("stability_seconds", str(paths_raw.get("stability_seconds", "5"))))

    paths = Paths(
        vault_root=vault_root,
        homework_root=homework_root,
        inbox=inbox,
        pipeline_root=pipeline_root,
        processing=processing,
        groups=groups,
        archive=archive,
        unsorted=unsorted,
        manual_retry=manual_retry,
        failed=failed,
        openscan_inbox=openscan_inbox,
        stability_seconds=stability_seconds,
    )

    # Classes: vault Classes.md wins; yaml classes: is the fallback.
    # Imported here (not at module top) to avoid a circular import —
    # classes_md imports ClassEntry from this module.
    from .classes_md import parse_classes_md, CLASSES_MD_NAME

    classes: list[ClassEntry] = parse_classes_md(
        homework_root / CLASSES_MD_NAME, homework_root
    )
    if not classes:
        for c in raw.get("classes", []) or []:
            classes.append(
                ClassEntry(
                    name=c["name"],
                    folder=vault_root / c["folder"],
                    aliases=[a.lower() for a in (c.get("aliases", []) or [])],
                )
            )

    return Config(
        paths=paths,
        classes=classes,
        settings=raw.get("settings", {}) or {},
        raw=raw,
    )
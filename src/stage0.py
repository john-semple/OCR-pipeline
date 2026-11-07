"""Stage 0 — Collect + dedup + move to Processing/.

Input:  OpenScan PDFs in the syncthing inbox (`paths.openscan_inbox`) or
        the vault's `00 Inbox/` if the syncthing mount is empty.
Output: deduplicated PDFs renamed to `{sha256}.pdf` in `Processing/`.

Guarantees:
- Only `.pdf` files are picked up (OpenScan exports PDF).
- A file must be stable (size unchanged for `stability_seconds`) before
  it's collected, so half-synced files are skipped.
- Syncthing temp/lock files (`.sync`, `.tmp`, `.stfolder`, hidden files)
  are ignored.
- SHA-256 of file contents is the dedup key. If a hash already exists in
  Processing/, the source file is skipped (idempotent re-sync).
- The original file is *moved* (not copied) out of the inbox once it has
  been hashed and placed in Processing/. The inbox stays empty after a
  successful run.

This stage does not OCR anything — it just prepares a clean, deduped
work queue for Stage 1.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .logging_setup import get_logger

log = get_logger("stage0")

CHUNK = 1 << 16  # 64 KiB read chunks for hashing

# Suffixes we will never treat as a scan.
_SKIP_SUFFIXES = {".sync", ".tmp", ".lock"}
_SKIP_NAMES = {".stfolder", ".stignore", ".gitkeep"}


@dataclass
class CollectedFile:
    """A PDF that has passed stability + dedup and is ready for Stage 1."""

    source: Path       # original inbox path (already moved away)
    dest: Path          # Processing/{hash}.pdf
    sha256: str
    size: int
    source_name: str   # original filename (e.g. "OpenScan 20260831 ...pdf") — for date extraction


def _is_skip_name(name: str) -> bool:
    if name in _SKIP_NAMES:
        return True
    if name.startswith("."):
        return True
    low = name.lower()
    return any(low.endswith(s) for s in _SKIP_SUFFIXES)


def _sha256_of(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _stable(path: Path, stability_seconds: int) -> bool:
    """Return True if the file size has not changed for `stability_seconds`."""
    if not path.exists():
        return False
    size1 = path.stat().st_size
    time.sleep(stability_seconds)
    if not path.exists():
        return False
    size2 = path.stat().st_size
    return size1 == size2 and size1 > 0


def _candidate_dirs(cfg: Config) -> list[Path]:
    dirs: list[Path] = []
    if cfg.paths.openscan_inbox and cfg.paths.openscan_inbox.exists():
        dirs.append(cfg.paths.openscan_inbox)
    # Also walk the vault's 00 Inbox/ — useful when the syncthing mount
    # and the vault inbox are the same tree, or for manual drops.
    if cfg.paths.inbox != cfg.paths.openscan_inbox and cfg.paths.inbox.exists():
        dirs.append(cfg.paths.inbox)
    # Manual Retry/ — files the user dragged here from Failed/ to re-run.
    # They already have {sha256}.pdf names + .name sidecars from the
    # original Stage 0 run, but treating them like fresh inbox files
    # (re-hash, re-dedup, re-move) is the simplest correct path.
    if cfg.paths.manual_retry.exists():
        dirs.append(cfg.paths.manual_retry)
    return dirs


def _collect_pdfs(cfg: Config) -> list[Path]:
    """Return all `.pdf` files in candidate dirs (recursively, skipping hidden)."""
    out: list[Path] = []
    for d in _candidate_dirs(cfg):
        for p in d.rglob("*"):
            if not p.is_file():
                continue
            if _is_skip_name(p.name):
                continue
            if p.suffix.lower() == ".pdf":
                out.append(p)
    return out


def _existing_hashes(processing: Path) -> set[str]:
    """Hashes already present in Processing/ as `{hash}.pdf` filenames."""
    if not processing.exists():
        return set()
    out: set[str] = set()
    for p in processing.iterdir():
        if p.is_file() and p.suffix.lower() == ".pdf":
            out.add(p.stem)
    return out


def collect(cfg: Config, *, dry_run: bool = False) -> list[CollectedFile]:
    """Run Stage 0. Returns the list of files ready for Stage 1.

    In dry-run mode, files are hashed and reported but NOT moved — the
    inbox is left untouched and Processing/ is not modified.
    """
    cfg.paths.ensure_dirs()
    candidates = _collect_pdfs(cfg)
    if not candidates:
        log.info("stage0: no PDFs found in inbox")
        return []

    existing = _existing_hashes(cfg.paths.processing)
    ready: list[CollectedFile] = []

    for src in candidates:
        rel = src.relative_to(src.parent.parent) if src.parent.parent != src.parent else src
        log.info("stage0: considering %s", src)

        # Files in Manual Retry/ already have {sha256}.pdf names + .name
        # sidecars from the original run — move directly to Processing/
        # without re-hashing.
        if src.parent == cfg.paths.manual_retry:
            sha = src.stem
            if sha in existing:
                log.info("stage0: dedup hit in Processing, removing %s", src.name)
                if not dry_run:
                    src.unlink()
                continue
            source_name = _read_manual_retry_source_name(cfg.paths.manual_retry, sha)
            size = src.stat().st_size
            dest = cfg.paths.processing / src.name
            if dry_run:
                log.info("stage0: dry-run, would move %s -> %s", src, dest)
            else:
                shutil.move(str(src), str(dest))
                sidecar = cfg.paths.manual_retry / f"{sha}.name"
                if sidecar.exists():
                    shutil.move(str(sidecar), str(cfg.paths.processing / sidecar.name))
                existing.add(sha)
                log.info("stage0: moved %s -> %s (manual retry)", src, dest)
            ready.append(CollectedFile(source=src, dest=dest, sha256=sha,
                                       size=size, source_name=source_name))
            continue

        if not _stable(src, cfg.paths.stability_seconds):
            log.info("stage0: skipping (not stable) %s", src)
            continue

        digest, size = _sha256_of(src)
        log.info("stage0: hashed %s -> sha256:%s (%d bytes)", src.name, digest[:12], size)

        if digest in existing:
            log.info("stage0: dedup hit, removing source %s", src)
            if not dry_run:
                src.unlink()
            continue

        dest = cfg.paths.processing / f"{digest}.pdf"
        if dry_run:
            log.info("stage0: dry-run, would move %s -> %s", src, dest)
        else:
            # Cross-device: /mnt/openscan-inbox and the vault may be on
            # different filesystems, so os.replace fails with EXDEV.
            # shutil.move falls back to copy+delete automatically.
            shutil.move(str(src), str(dest))
            # Sidecar: record the original filename so Stage 2 can parse
            # the OpenScan timestamp even though the PDF was renamed to
            # {hash}.pdf. One line, no JSON overhead.
            (cfg.paths.processing / f"{digest}.name").write_text(src.name, encoding="utf-8")
            existing.add(digest)
            log.info("stage0: moved %s -> %s", src, dest)

        ready.append(CollectedFile(source=src, dest=dest, sha256=digest, size=size, source_name=src.name))

    return ready


def _read_manual_retry_source_name(manual_retry: Path, sha: str) -> str:
    """Read the .name sidecar from Manual Retry/ if it exists."""
    sidecar = manual_retry / f"{sha}.name"
    if sidecar.exists():
        return sidecar.read_text(encoding="utf-8").strip()
    return ""


if __name__ == "__main__":
    from .config import load_config

    cfg = load_config()
    files = collect(cfg, dry_run=True)
    print(f"Collected {len(files)} file(s):")
    for f in files:
        print(f"  {f.sha256[:12]}  {f.size:>8} B  <- {f.source.name}")
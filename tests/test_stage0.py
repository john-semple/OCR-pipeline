"""Tests for src/stage0.py — stability, dedup, skip, move.

Run: python3 -m pytest tests/ -v
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Config, Paths
from src.stage0 import collect, _sha256_of, _is_skip_name


def make_cfg(tmp_path: Path) -> Config:
    """Build a Config pointing at tmp_path as the vault root."""
    inbox = tmp_path / "00 Inbox"
    pipeline = tmp_path / "00 Pipeline"
    processing = pipeline / "Processing"
    paths = Paths(
        vault_root=tmp_path,
        homework_root=tmp_path,
        inbox=inbox,
        pipeline_root=pipeline,
        processing=processing,
        groups=processing / "groups",
        archive=pipeline / "Archive",
        unsorted=pipeline / "Unsorted",
        manual_retry=pipeline / "Manual Retry",
        failed=pipeline / "Failed",
        openscan_inbox=tmp_path / "openscan",
        stability_seconds=0,
    )
    paths.ensure_dirs()
    return Config(paths=paths, classes=[], settings={}, raw={})


def write_pdf(path: Path, content: bytes = b"%PDF-1.4 fake pdf") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_collects_pdf_and_moves_to_processing(tmp_path):
    cfg = make_cfg(tmp_path)
    src = cfg.paths.openscan_inbox / "OpenScan 20260101 120000000000.pdf"
    write_pdf(src)

    files = collect(cfg)

    assert len(files) == 1
    assert not src.exists(), "source should be moved out of inbox"
    digest = hashlib.sha256(b"%PDF-1.4 fake pdf").hexdigest()
    dest = cfg.paths.processing / f"{digest}.pdf"
    assert files[0].dest == dest
    assert dest.exists(), "file should land in Processing/"


def test_dedup_skips_known_hash(tmp_path):
    cfg = make_cfg(tmp_path)
    src = cfg.paths.openscan_inbox / "a.pdf"
    write_pdf(src)
    digest = hashlib.sha256(b"%PDF-1.4 fake pdf").hexdigest()
    (cfg.paths.processing / f"{digest}.pdf").write_bytes(b"%PDF-1.4 fake pdf")

    files = collect(cfg)

    assert files == [], "dedup hit should produce no new CollectedFile"
    assert not src.exists(), "source should be removed on dedup hit"


def test_dedup_across_runs(tmp_path):
    """Second run over the same inbox yields no new files."""
    cfg = make_cfg(tmp_path)
    src = cfg.paths.openscan_inbox / "a.pdf"
    write_pdf(src, b"page one")

    first = collect(cfg)
    assert len(first) == 1
    assert not src.exists()

    second = collect(cfg)
    assert second == [], "inbox is empty, nothing to collect"


def test_skips_non_pdf(tmp_path):
    cfg = make_cfg(tmp_path)
    write_pdf(cfg.paths.openscan_inbox / "notes.txt", b"hello")
    write_pdf(cfg.paths.openscan_inbox / "image.jpg", b"jpg")

    files = collect(cfg)
    assert files == []


def test_skips_hidden_and_syncthing_files(tmp_path):
    cfg = make_cfg(tmp_path)
    write_pdf(cfg.paths.openscan_inbox / ".hidden.pdf")
    write_pdf(cfg.paths.openscan_inbox / ".stfolder")
    write_pdf(cfg.paths.openscan_inbox / "x.pdf.tmp")
    write_pdf(cfg.paths.openscan_inbox / ".sync.lock")

    assert collect(cfg) == []


def test_is_skip_name():
    assert _is_skip_name(".stfolder")
    assert _is_skip_name(".gitkeep")
    assert _is_skip_name("x.pdf.tmp")
    assert _is_skip_name(".hidden.pdf")
    assert not _is_skip_name("OpenScan 20260101 120000000000.pdf")


def test_dry_run_does_not_move(tmp_path):
    cfg = make_cfg(tmp_path)
    src = cfg.paths.openscan_inbox / "a.pdf"
    write_pdf(src)

    files = collect(cfg, dry_run=True)

    assert len(files) == 1
    assert src.exists(), "dry-run must not move the source"
    assert not files[0].dest.exists(), "dry-run must not create the dest"


def test_finds_pdfs_recursively(tmp_path):
    """OpenScan drops files under a `PDF/` subfolder; Stage 0 must find them."""
    cfg = make_cfg(tmp_path)
    src = cfg.paths.openscan_inbox / "PDF" / "OpenScan 20260101 120000000000.pdf"
    write_pdf(src)

    files = collect(cfg)
    assert len(files) == 1
    assert not src.exists()


def test_sha256_of(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"abc")
    digest, size = _sha256_of(p)
    assert digest == hashlib.sha256(b"abc").hexdigest()
    assert size == 3


def test_ignores_empty_file(tmp_path):
    """A 0-byte file is treated as not-stable and skipped."""
    cfg = make_cfg(tmp_path)
    src = cfg.paths.openscan_inbox / "empty.pdf"
    write_pdf(src, b"")
    files = collect(cfg)
    assert files == []


def test_manual_retry_moves_to_processing(tmp_path):
    """A PDF in Manual Retry/ is moved directly to Processing/ without re-hashing."""
    cfg = make_cfg(tmp_path)
    sha = "abc123def456"
    src = cfg.paths.manual_retry / f"{sha}.pdf"
    write_pdf(src, b"%PDF-1.4 retry me")
    # Sidecar with original filename
    (cfg.paths.manual_retry / f"{sha}.name").write_text("OpenScan 20260101 120000000000.pdf")

    files = collect(cfg)

    assert len(files) == 1
    assert files[0].sha256 == sha
    assert files[0].source_name == "OpenScan 20260101 120000000000.pdf"
    assert not src.exists(), "source should be moved out of Manual Retry/"
    assert (cfg.paths.processing / f"{sha}.pdf").exists()
    assert (cfg.paths.processing / f"{sha}.name").exists(), ".name sidecar should move too"


def test_manual_retry_dedup(tmp_path):
    """If the hash is already in Processing/, the Manual Retry file is removed."""
    cfg = make_cfg(tmp_path)
    sha = "abc123def456"
    src = cfg.paths.manual_retry / f"{sha}.pdf"
    write_pdf(src, b"%PDF-1.4 retry")
    (cfg.paths.processing / f"{sha}.pdf").write_bytes(b"already here")

    files = collect(cfg)

    assert files == [], "dedup hit should produce no new CollectedFile"
    assert not src.exists(), "source should be removed on dedup"


def test_manual_retry_without_sidecar(tmp_path):
    """A PDF in Manual Retry/ without a .name sidecar still works (empty source_name)."""
    cfg = make_cfg(tmp_path)
    sha = "abc123def456"
    src = cfg.paths.manual_retry / f"{sha}.pdf"
    write_pdf(src, b"%PDF-1.4 no sidecar")

    files = collect(cfg)

    assert len(files) == 1
    assert files[0].source_name == ""
    assert (cfg.paths.processing / f"{sha}.pdf").exists()


def test_manual_retry_dry_run(tmp_path):
    """Dry run does not move files from Manual Retry/."""
    cfg = make_cfg(tmp_path)
    sha = "abc123def456"
    src = cfg.paths.manual_retry / f"{sha}.pdf"
    write_pdf(src, b"%PDF-1.4 retry")

    files = collect(cfg, dry_run=True)

    assert len(files) == 1
    assert src.exists(), "dry-run must not move the source"
    assert not (cfg.paths.processing / f"{sha}.pdf").exists()
"""Tests for alias README generator and file watcher."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import ClassEntry, Config, Paths
from src.alias_readme import generate_alias_readme, README_NAME


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
                       aliases=["thermo", "thermodynamics", "me 301"]),
            ClassEntry(name="Circuits", folder=tmp_path / "Circuits",
                       aliases=["circuits", "circuit analysis"]),
        ]
    return Config(paths=paths, classes=classes, settings={}, raw={})


# --- Alias README --- #

def test_readme_generated(tmp_path):
    cfg = make_cfg(tmp_path)
    path = generate_alias_readme(cfg)
    assert path.exists()
    assert path.name == README_NAME
    assert path.parent == cfg.paths.homework_root


def test_readme_contains_classes(tmp_path):
    cfg = make_cfg(tmp_path)
    path = generate_alias_readme(cfg)
    content = path.read_text()
    assert "Thermodynamics & Lab" in content
    assert "Circuits" in content


def test_readme_contains_aliases(tmp_path):
    cfg = make_cfg(tmp_path)
    path = generate_alias_readme(cfg)
    content = path.read_text()
    assert "`thermo`" in content
    assert "`me 301`" in content
    assert "`circuit analysis`" in content


def test_readme_contains_matching_explanation(tmp_path):
    cfg = make_cfg(tmp_path)
    path = generate_alias_readme(cfg)
    content = path.read_text()
    assert "Exact match" in content
    assert "Substring match" in content
    assert "AI fallback" in content
    assert "first line" in content


def test_readme_contains_add_keyword_instructions(tmp_path):
    cfg = make_cfg(tmp_path)
    path = generate_alias_readme(cfg)
    content = path.read_text()
    assert "Classes.md" in content
    assert "auto-generated" in content


def test_readme_empty_classes(tmp_path):
    cfg = make_cfg(tmp_path, classes=[])
    path = generate_alias_readme(cfg)
    content = path.read_text()
    assert "No classes configured" in content


def test_readme_regenerates(tmp_path):
    """Running twice should update the file, not crash."""
    cfg = make_cfg(tmp_path)
    generate_alias_readme(cfg)
    generate_alias_readme(cfg)
    # Should still exist and be valid.
    assert (cfg.paths.homework_root / README_NAME).exists()


# --- File watcher --- #

def test_watcher_debounce(tmp_path):
    """Events should be debounced — callback fires once after quiet period."""
    from src.watcher import Watcher

    cfg = make_cfg(tmp_path)
    call_count = [0]

    def callback():
        call_count[0] += 1

    watcher = Watcher(cfg, callback, debounce_seconds=1)

    # Simulate 3 rapid events.
    class FakeEvent:
        def __init__(self, path, is_directory=False):
            self.src_path = path
            self.is_directory = is_directory

    for _ in range(3):
        watcher._on_event(FakeEvent(str(tmp_path / "openscan" / "scan.pdf")))

    # Callback should NOT have fired yet (debounce).
    assert call_count[0] == 0

    # Wait for debounce to expire.
    time.sleep(2)

    # Should have fired exactly once.
    assert call_count[0] == 1


def test_watcher_ignores_non_pdf(tmp_path):
    from src.watcher import Watcher

    cfg = make_cfg(tmp_path)
    call_count = [0]

    watcher = Watcher(cfg, lambda: call_count.__setitem__(0, call_count[0] + 1), debounce_seconds=1)

    class FakeEvent:
        def __init__(self, path, is_directory=False):
            self.src_path = path
            self.is_directory = is_directory

    watcher._on_event(FakeEvent(str(tmp_path / "notes.txt")))
    time.sleep(2)
    assert call_count[0] == 0


def test_watcher_ignores_directories(tmp_path):
    from src.watcher import Watcher

    cfg = make_cfg(tmp_path)
    call_count = [0]

    watcher = Watcher(cfg, lambda: call_count.__setitem__(0, call_count[0] + 1), debounce_seconds=1)

    class FakeEvent:
        def __init__(self, path, is_directory=True):
            self.src_path = path
            self.is_directory = is_directory

    watcher._on_event(FakeEvent(str(tmp_path / "openscan" / "subdir"), is_directory=True))
    time.sleep(2)
    assert call_count[0] == 0


def test_watcher_ignores_hidden_and_temp(tmp_path):
    from src.watcher import Watcher

    cfg = make_cfg(tmp_path)
    call_count = [0]

    watcher = Watcher(cfg, lambda: call_count.__setitem__(0, call_count[0] + 1), debounce_seconds=1)

    class FakeEvent:
        def __init__(self, path, is_directory=False):
            self.src_path = path
            self.is_directory = is_directory

    for name in [".hidden.pdf", "scan.pdf.tmp", "scan.pdf.sync", ".stfolder"]:
        watcher._on_event(FakeEvent(str(tmp_path / "openscan" / name)))
    time.sleep(2)
    assert call_count[0] == 0


# --- File watcher: Classes.md --- #

class FakeEvent:
    def __init__(self, path, is_directory=False):
        self.src_path = path
        self.is_directory = is_directory


def test_watcher_classes_event_triggers_classes_callback(tmp_path):
    """A Classes.md event fires the classes callback, not the pipeline."""
    from src.watcher import Watcher

    cfg = make_cfg(tmp_path)
    pipeline_calls = [0]
    classes_calls = [0]

    watcher = Watcher(
        cfg,
        callback=lambda: pipeline_calls.__setitem__(0, pipeline_calls[0] + 1),
        classes_callback=lambda: classes_calls.__setitem__(0, classes_calls[0] + 1),
        debounce_seconds=1,
    )

    watcher._on_classes_event(FakeEvent(str(tmp_path / "Classes.md")))
    time.sleep(2)

    assert classes_calls[0] == 1
    assert pipeline_calls[0] == 0, "pipeline callback must not fire for Classes.md"


def test_watcher_ignores_non_classes_md_files(tmp_path):
    """Events for other .md files in the homework root are ignored."""
    from src.watcher import Watcher

    cfg = make_cfg(tmp_path)
    classes_calls = [0]

    watcher = Watcher(
        cfg,
        callback=lambda: None,
        classes_callback=lambda: classes_calls.__setitem__(0, classes_calls[0] + 1),
        debounce_seconds=1,
    )

    watcher._on_classes_event(FakeEvent(str(tmp_path / "Sorting Guide.md")))
    watcher._on_classes_event(FakeEvent(str(tmp_path / "notes" / "anything.md")))
    time.sleep(2)

    assert classes_calls[0] == 0


def test_watcher_classes_callback_uses_content_hash_poll(tmp_path):
    """The poll loop detects Classes.md changes by hash, not just events."""
    from src.watcher import Watcher

    cfg = make_cfg(tmp_path)
    classes_calls = [0]

    # Write an initial Classes.md so the watcher hashes it at init.
    (tmp_path / "Classes.md").write_text("Thermo: thermo\n")

    watcher = Watcher(
        cfg,
        callback=lambda: None,
        classes_callback=lambda: classes_calls.__setitem__(0, classes_calls[0] + 1),
        debounce_seconds=1,
    )

    # Simulate the poll backstop: change the file, then run one poll
    # iteration of the classes check.
    (tmp_path / "Classes.md").write_text("Thermo: thermo\nCircuits: circuits\n")
    watcher._poll_classes_md()
    time.sleep(2)

    assert classes_calls[0] == 1

    # Second poll with no change: no re-trigger.
    watcher._poll_classes_md()
    time.sleep(2)
    assert classes_calls[0] == 1


def test_watcher_classes_events_debounced(tmp_path):
    """A burst of Classes.md events fires the callback once."""
    from src.watcher import Watcher

    cfg = make_cfg(tmp_path)
    classes_calls = [0]

    watcher = Watcher(
        cfg,
        callback=lambda: None,
        classes_callback=lambda: classes_calls.__setitem__(0, classes_calls[0] + 1),
        debounce_seconds=1,
    )

    for _ in range(3):
        watcher._on_classes_event(FakeEvent(str(tmp_path / "Classes.md")))
    time.sleep(2)

    assert classes_calls[0] == 1


def test_watcher_no_classes_callback_no_crash(tmp_path):
    """Without a classes_callback, Classes.md events are harmless no-ops."""
    from src.watcher import Watcher

    cfg = make_cfg(tmp_path)
    watcher = Watcher(cfg, callback=lambda: None, debounce_seconds=1)
    watcher._on_classes_event(FakeEvent(str(tmp_path / "Classes.md")))
    watcher._poll_classes_md()  # poll path also no-ops
    time.sleep(2)


def test_watcher_new_events_reset_debounce(tmp_path):
    """A new event during the debounce window should reset the timer."""
    from src.watcher import Watcher

    cfg = make_cfg(tmp_path)
    call_count = [0]

    watcher = Watcher(cfg, lambda: call_count.__setitem__(0, call_count[0] + 1), debounce_seconds=2)

    class FakeEvent:
        def __init__(self, path, is_directory=False):
            self.src_path = path
            self.is_directory = is_directory

    watcher._on_event(FakeEvent(str(tmp_path / "openscan" / "a.pdf")))
    time.sleep(1)
    # Second event should reset the timer.
    watcher._on_event(FakeEvent(str(tmp_path / "openscan" / "b.pdf")))
    time.sleep(1)
    # Only 1s since last event, debounce is 2s — should NOT have fired.
    assert call_count[0] == 0
    time.sleep(2)
    # Now it should have fired.
    assert call_count[0] == 1
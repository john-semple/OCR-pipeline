"""File watcher — inotify-based instant triggering via watchdog, with a
polling backstop for bind-mount blind spots.

Replaces the 10-minute systemd timer with instant triggering when
Syncthing drops a new PDF into the inbox. Uses `watchdog` (already
installed in the image) which wraps inotify on Linux.

**Why a polling backstop:** the inbox is a Syncthing bind-mount, and
inotify events inside a container don't always fire for host-side
writes. Historically a re-synced PDF sat in the inbox for 40+ minutes
with no event (later traced, at least partly, to a broken event handler
class that crashed the observer thread on its first dispatch — fixed
2026-09-04). The poll backstop covers both that bug class and genuine
bind-mount blind spots: a background thread polls the watched dirs
every `poll_seconds` (default 60s) for PDFs that are stable and
triggers a run if any are found. The poll coalesces with the inotify
debounce so they never double-fire.

Debouncing: Syncthing writes files in stages (create → write → close),
so multiple events fire for one file. The watcher collects events and
only triggers a pipeline run after `debounce_seconds` of quiet (default
10s — long enough for Syncthing to finish writing a typical scan, short
enough to feel instant).

Classes.md watching: the vault's homework root is also watched. A
change to `Classes.md` triggers a light `classes_callback` (reload
classes, create new folders, regen the Sorting Guide) — never a full
pipeline run. Only the exact filename `Classes.md` triggers it; the
pipeline never writes that file, so there's no feedback loop. A content
hash in the poll loop backstops inotify misses, same as PDFs.

Usage:
    python3 run.py --watch

This blocks forever (the watcher runs until killed). systemd should
manage this process with `Restart=always`.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from .config import Config
from .logging_setup import get_logger

log = get_logger("watcher")


class Watcher:
    """Debounced file watcher that triggers a pipeline run on new PDFs.

    Uses a simple debounce: when an event arrives, reset a timer. When
    the timer expires (no new events for `debounce_seconds`), trigger
    the callback. This coalesces Syncthing's burst of write events into
    one pipeline run.

    A polling thread runs alongside inotify as a backstop for bind-mount
    blind spots (see module docstring).

    Also watches `Classes.md` in the vault (separate callback + debounce,
    never a full pipeline run): on change it reloads the class list,
    creates any new class folders, and regenerates the Sorting Guide.
    Syncthing syncs it via temp file + atomic rename, so by the time an
    event carries the real filename the content is complete.
    """

    CLASSES_MD_NAME = "Classes.md"

    def __init__(self, cfg: Config, callback, debounce_seconds: int = 10,
                 poll_seconds: int = 60, classes_callback=None):
        self.cfg = cfg
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self.poll_seconds = poll_seconds
        self.classes_callback = classes_callback
        self._timer: threading.Timer | None = None
        self._classes_timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._running = False
        self._classes_md_hash: str | None = None
        if classes_callback:
            self._classes_md_hash = self._hash_classes_md()

    def start(self) -> None:
        """Start watching. Blocks until stop() is called."""
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        class Handler(FileSystemEventHandler):
            """Forwards watchdog events to a callback. Must subclass
            FileSystemEventHandler so the observer can dispatch() to it."""
            def __init__(self, on_event):
                self._on_event_cb = on_event
                super().__init__()

            def on_created(self, event):
                self._on_event_cb(event)

            def on_modified(self, event):
                self._on_event_cb(event)

            def on_moved(self, event):
                self._on_event_cb(event)

            def on_closed(self, event):
                self._on_event_cb(event)

        watch_dirs = self._watch_dirs()
        if not watch_dirs:
            log.error("watcher: no directories to watch")
            return

        handler = Handler(on_event=self._on_event)
        observer = Observer()

        for d in watch_dirs:
            observer.schedule(handler, str(d), recursive=True)
            log.info("watcher: watching %s (recursive)", d)

        # Also watch the homework root so Classes.md edits (from the
        # phone, via Syncthing) are picked up.
        classes_handler = Handler(on_event=self._on_classes_event)
        observer.schedule(classes_handler, str(self.cfg.paths.homework_root), recursive=True)
        log.info("watcher: watching %s for Classes.md",
                 self.cfg.paths.homework_root)

        observer.start()
        self._running = True
        log.info("watcher: started (debounce=%ds, poll=%ds)",
                 self.debounce_seconds, self.poll_seconds)

        # Polling backstop: catches PDFs that inotify misses on bind-mounts.
        poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        poll_thread.start()

        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("watcher: interrupted, stopping")
        finally:
            observer.stop()
            observer.join()
            log.info("watcher: stopped")

    def stop(self) -> None:
        self._running = False
        with self._lock:
            if self._timer:
                self._timer.cancel()
            if self._classes_timer:
                self._classes_timer.cancel()

    def _watch_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        if self.cfg.paths.openscan_inbox and self.cfg.paths.openscan_inbox.exists():
            dirs.append(self.cfg.paths.openscan_inbox)
        if self.cfg.paths.inbox != self.cfg.paths.openscan_inbox and self.cfg.paths.inbox.exists():
            dirs.append(self.cfg.paths.inbox)
        if self.cfg.paths.manual_retry.exists():
            dirs.append(self.cfg.paths.manual_retry)
        return dirs

    def _poll_loop(self) -> None:
        """Background poll for PDFs that inotify missed (bind-mount backstop).

        Every `poll_seconds`, walks the watched dirs for `.pdf` files and
        arms the same debounce timer that inotify events arm. Stage 0's
        stability check + dedup make this safe to run repeatedly — a
        spurious trigger with no new files is a cheap no-op.

        Also checks Classes.md's content hash so edits missed by inotify
        still trigger the classes callback.
        """
        from .stage0 import _collect_pdfs
        while self._running:
            time.sleep(self.poll_seconds)
            if not self._running:
                break
            try:
                pdfs = _collect_pdfs(self.cfg)
                self._poll_classes_md()
            except Exception as exc:
                log.warning("watcher: poll scan failed: %s", exc)
                continue
            if pdfs:
                names = ", ".join(p.name for p in pdfs[:3])
                log.info("watcher: poll found %d PDF(s) (%s) — arming trigger",
                         len(pdfs), names)
                self._arm_trigger()

    def _hash_classes_md(self) -> str | None:
        """Content hash of Classes.md, or None if it doesn't exist."""
        import hashlib
        path = self.cfg.paths.homework_root / self.CLASSES_MD_NAME
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            return None

    def _poll_classes_md(self) -> None:
        """Detect Classes.md changes by hash (poll backstop path)."""
        if not self.classes_callback:
            return
        current = self._hash_classes_md()
        if current != self._classes_md_hash:
            log.info("watcher: poll detected Classes.md change")
            self._classes_md_hash = current
            self._arm_classes_trigger()

    def _on_classes_event(self, event) -> None:
        """Events from the homework root — only Classes.md matters."""
        if event.is_directory:
            return
        if Path(event.src_path).name != self.CLASSES_MD_NAME:
            return
        log.info("watcher: Classes.md event")
        self._arm_classes_trigger()

    def _on_event(self, event) -> None:
        """Called by the event handler on any file event."""
        if event.is_directory:
            return
        if not event.src_path.lower().endswith(".pdf"):
            return
        # Skip syncthing temp/hidden files.
        name = Path(event.src_path).name
        if name.startswith(".") or name.endswith((".sync", ".tmp", ".lock")):
            return

        log.info("watcher: event %s", event.src_path)
        self._arm_trigger()

    def _arm_trigger(self) -> None:
        """(Re)arm the debounce timer. Called from inotify events and the poll loop."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_seconds, self._trigger)
            self._timer.daemon = True
            self._timer.start()

    def _arm_classes_trigger(self) -> None:
        """(Re)arm the Classes.md debounce timer."""
        if not self.classes_callback:
            return
        with self._lock:
            if self._classes_timer:
                self._classes_timer.cancel()
            self._classes_timer = threading.Timer(
                self.debounce_seconds, self._trigger_classes
            )
            self._classes_timer.daemon = True
            self._classes_timer.start()

    def _trigger(self) -> None:
        """Debounce expired — run the pipeline."""
        log.info("watcher: debounce expired, triggering pipeline run")
        try:
            self.callback()
        except Exception as exc:
            log.error("watcher: pipeline run failed: %s", exc)

    def _trigger_classes(self) -> None:
        """Classes.md debounce expired — run the light classes callback."""
        log.info("watcher: Classes.md debounce expired, updating classes")
        # Update the hash so the poll loop doesn't re-trigger.
        self._classes_md_hash = self._hash_classes_md()
        try:
            self.classes_callback()
        except Exception as exc:
            log.error("watcher: classes update failed: %s", exc)
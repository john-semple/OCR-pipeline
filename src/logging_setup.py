"""Lightweight logging setup. Rotating file in logs/ + stderr.

Used by all stages so logging is configured in one place.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "logs"
LOG_FILE = LOG_DIR / "pipeline.log"

_configured = False


def get_logger(name: str) -> logging.Logger:
    global _configured
    if not _configured:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        root = logging.getLogger("ocr_pipeline")
        root.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s: %(message)s")

        fh = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)

        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
        _configured = True

    return logging.getLogger(f"ocr_pipeline.{name}")
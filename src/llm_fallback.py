"""LLM fallback for class sorting when the alias map misses.

Uses Mistral's chat API (mistral-small-latest) — same SDK, same API key
as the OCR engine. No new signup or key needed. Cost is negligible
(~50 tokens per call, only fires on alias-map misses).

Flow:
  1. Send the OCR'd title + known class names to mistral-small-latest.
  2. The LLM responds with the class name, or "NONE".
  3. We match the LLM's response back to an actual ClassEntry (fuzzy —
     case-insensitive, trimmed). If no match, it's treated as NONE.
  4. NONE (or any unresolvable response) -> Unsorted, needs_review=True.

Safety:
  - temperature=0 for deterministic output.
  - max_tokens=20 so the LLM can't ramble.
  - System prompt is strict: respond with ONLY the class name or NONE.
  - The response is matched against the actual class entries, not
    trusted verbatim — so a hallucinated class name can't misfile a note.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Protocol

from .config import Config, ClassEntry
from .logging_setup import get_logger

log = get_logger("llm_fallback")

MODEL = "mistral-small-latest"
MAX_RETRIES = 3
RETRY_BACKOFF = 1.0
MAX_TOKENS = 20

_SYSTEM_PROMPT = (
    "You are a class router. Given a scanned note title and a list of "
    "class names, pick the single best matching class. Respond with "
    "ONLY the class name, or NONE if no match. Do not explain."
)


class LLMError(Exception):
    """Raised when the LLM call fails after all retries."""


class LLMClassifier(Protocol):
    """Interface for LLM-based classification fallback."""

    def classify(self, title: str, classes: list[ClassEntry]) -> str:
        """Return a class name, or 'NONE'. Raises LLMError on failure."""
        ...


class MistralLLMClassifier:
    """LLM fallback using Mistral's chat API (mistral-small-latest)."""

    def __init__(self, api_key: str | None = None):
        from mistralai import Mistral  # lazy import for testability

        key = api_key or os.environ.get("MISTRAL_API_KEY")
        if not key:
            raise LLMError("MISTRAL_API_KEY not set")
        self._client = Mistral(api_key=key)

    def classify(self, title: str, classes: list[ClassEntry]) -> str:
        if not classes:
            return "NONE"

        class_names = [c.name for c in classes]
        user_msg = (
            f'Title: "{title}"\n'
            f"Classes: {', '.join(class_names)}"
        )

        last_err: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self._client.chat.complete(
                    model=MODEL,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0,
                    max_tokens=MAX_TOKENS,
                )
                raw_answer = resp.choices[0].message.content.strip()
                log.info("llm_fallback: title=%r -> %r", title[:40], raw_answer)
                return self._resolve_class(raw_answer, classes)
            except Exception as exc:
                last_err = exc
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF * attempt
                    log.warning("llm_fallback: attempt %d/%d failed: %s — retrying in %.1fs",
                                attempt, MAX_RETRIES, exc, wait)
                    time.sleep(wait)

        raise LLMError(f"LLM classify failed after {MAX_RETRIES} attempts: {last_err}") from last_err

    def _resolve_class(self, raw_answer: str, classes: list[ClassEntry]) -> str:
        """Match the LLM's raw response to an actual class name.

        The LLM might say "Thermodynamics" when the class is
        "Thermodynamics & Lab" — we match case-insensitively and
        accept partial matches (the LLM's answer is a substring of the
        class name, or vice versa). If nothing matches, return NONE.
        """
        answer = raw_answer.strip().strip('"').strip("'")

        if answer.upper() == "NONE":
            return "NONE"

        answer_lower = answer.lower()
        for entry in classes:
            name_lower = entry.name.lower()
            # Exact match (case-insensitive).
            if answer_lower == name_lower:
                return entry.name
            # LLM's answer is a substring of the class name
            # (e.g. "Thermodynamics" matches "Thermodynamics & Lab").
            if answer_lower in name_lower or name_lower in answer_lower:
                return entry.name

        log.warning("llm_fallback: could not resolve %r to any class -> NONE", answer)
        return "NONE"
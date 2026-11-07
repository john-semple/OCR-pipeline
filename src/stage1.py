"""Stage 1 — OCR engine interface + Mistral implementation.

The OCREngine interface is the only vendor coupling point in the
pipeline. Swapping to Gemini or another vendor means writing a second
class that implements `process(pdf_path) -> OCRResult` — nothing else
changes.

Flow (Mistral, PDF-direct — the primary path per decisions.md):
  1. Upload the PDF to Mistral (`files.upload`, purpose="ocr") -> file_id
  2. Get a signed URL for that file (`files.get_signed_url`) -> url
  3. Call `ocr.process` with the signed URL as `document_url`,
     `include_image_base64=True` -> OCRResponse
  4. Delete the uploaded file from Mistral (`files.delete`) so the
     file store doesn't accumulate.

No client-side rasterization — Mistral's server-side rasterizer produces
better results (POC 2026-08-31). `pdf2image` remains as a fallback for a
future engine variant but is not used here.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .logging_setup import get_logger

log = get_logger("stage1")


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #

@dataclass
class OCRImage:
    """A diagram image extracted by the OCR engine."""

    id: str                       # e.g. "img-0.jpeg" (matches markdown placeholder)
    image_base64: str | None      # raw base64 data (no data: prefix) or None
    top_left_x: int | None
    top_left_y: int | None
    bottom_right_x: int | None
    bottom_right_y: int | None


@dataclass
class OCRPage:
    """One page of OCR output."""

    index: int                    # 0-based page number
    markdown: str                 # page content as markdown (LaTeX + image placeholders)
    images: list[OCRImage] = field(default_factory=list)


@dataclass
class OCRResult:
    """The full OCR output for one PDF."""

    pdf_path: Path
    sha256: str
    source_name: str           # original filename (e.g. "OpenScan 20260831 ...pdf")
    pages: list[OCRPage]
    title: str                 # first non-empty line of page 0, trimmed
    raw_json: dict             # the full API response, for archive/audit

    @property
    def num_pages(self) -> int:
        return len(self.pages)


# --------------------------------------------------------------------------- #
# Engine interface
# --------------------------------------------------------------------------- #

class OCREngine(Protocol):
    """Interface every OCR engine implements."""

    def process(self, pdf_path: Path, sha256: str, source_name: str = "") -> OCRResult:
        """OCR a PDF. Raises OCRError on failure (after retries exhausted)."""
        ...


class OCRError(Exception):
    """Raised when OCR fails after all retries."""


# --------------------------------------------------------------------------- #
# Mistral implementation
# --------------------------------------------------------------------------- #

class MistralOCREngine:
    """OCR via Mistral's API (mistral-ocr-latest).

    PDF-direct path: upload -> signed URL -> ocr.process -> delete.
    """

    MODEL = "mistral-ocr-latest"
    MAX_RETRIES = 3
    RETRY_BACKOFF = 2.0  # seconds, doubled each retry

    def __init__(self, api_key: str | None = None):
        from mistralai import Mistral  # imported lazily so tests can mock

        key = api_key or os.environ.get("MISTRAL_API_KEY")
        if not key:
            raise OCRError("MISTRAL_API_KEY not set")
        self._client = Mistral(api_key=key)

    def process(self, pdf_path: Path, sha256: str, source_name: str = "") -> OCRResult:
        last_err: Exception | None = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                return self._process_once(pdf_path, sha256, source_name)
            except Exception as exc:
                last_err = exc
                if attempt < self.MAX_RETRIES:
                    wait = self.RETRY_BACKOFF * attempt
                    log.warning("stage1: attempt %d/%d failed: %s — retrying in %.1fs",
                                attempt, self.MAX_RETRIES, exc, wait)
                    time.sleep(wait)
        raise OCRError(f"OCR failed for {pdf_path.name} after {self.MAX_RETRIES} attempts: {last_err}") from last_err

    def _process_once(self, pdf_path: Path, sha256: str, source_name: str) -> OCRResult:
        log.info("stage1: uploading %s (%s)", pdf_path.name, sha256[:12])
        file_id = self._upload(pdf_path)
        try:
            url = self._signed_url(file_id)
            log.info("stage1: ocr.process file_id=%s", file_id)
            resp = self._ocr(url)
            result = self._parse_response(resp, pdf_path, sha256, source_name)
            log.info("stage1: ok %s -> %d page(s), title=%r",
                      pdf_path.name, result.num_pages, result.title[:50])
            return result
        finally:
            self._cleanup(file_id)

    def _upload(self, pdf_path: Path) -> str:
        with pdf_path.open("rb") as f:
            uploaded = self._client.files.upload(
                file={"file_name": pdf_path.name, "content": f},
                purpose="ocr",
            )
        return uploaded.id

    def _signed_url(self, file_id: str) -> str:
        signed = self._client.files.get_signed_url(file_id=file_id)
        return signed.url

    def _ocr(self, document_url: str):
        return self._client.ocr.process(
            model=self.MODEL,
            document={"type": "document_url", "document_url": document_url},
            include_image_base64=True,
        )

    def _cleanup(self, file_id: str) -> None:
        try:
            self._client.files.delete(file_id=file_id)
        except Exception as exc:
            log.warning("stage1: failed to delete uploaded file %s: %s", file_id, exc)

    def _parse_response(self, resp, pdf_path: Path, sha256: str, source_name: str) -> OCRResult:
        pages: list[OCRPage] = []
        for page in resp.pages:
            images: list[OCRImage] = []
            for img in (page.images or []):
                # Mistral returns image_base64 as a data URL:
                # "data:image/jpeg;base64,/9j/4AAQ..." — strip the prefix.
                raw_b64 = img.image_base64
                if raw_b64 and raw_b64.startswith("data:"):
                    raw_b64 = raw_b64.split(",", 1)[-1]
                images.append(
                    OCRImage(
                        id=img.id,
                        image_base64=raw_b64,
                        top_left_x=img.top_left_x,
                        top_left_y=img.top_left_y,
                        bottom_right_x=img.bottom_right_x,
                        bottom_right_y=img.bottom_right_y,
                    )
                )
            pages.append(OCRPage(index=page.index, markdown=page.markdown, images=images))

        title = _extract_title(pages[0].markdown if pages else "")

        raw = json.loads(resp.model_dump_json()) if hasattr(resp, "model_dump_json") else {}

        return OCRResult(
            pdf_path=pdf_path,
            sha256=sha256,
            source_name=source_name,
            pages=pages,
            title=title,
            raw_json=raw,
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _extract_title(markdown: str) -> str:
    """First non-empty line of the OCR markdown, trimmed."""
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""
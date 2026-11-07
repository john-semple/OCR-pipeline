# Project Context

## Goal

Take pictures/scan homework notes on a phone, transfer them to a Raspberry
Pi 4 via syncthing, run OCR that converts them to markdown (with equations
as LaTeX and diagrams as image files), and sort the resulting notes into the
right class folder in an Obsidian vault. The Pi runs 24/7, so latency is not
a concern.

## User specifics

- Engineering major → lots of equations and diagrams.
- "Pretty bad" handwriting → a literal transcriber may reproduce misspellings.
- Not the best at spelling → may want a cleanup pass later.
- Wants to search key topics through Obsidian → tags + frontmatter matter.

## Resources available

- Raspberry Pi 4, 4GB RAM, currently idle (may host more later).
- Obsidian vault, synced between phone and a computer via syncthing.
- Syncthing (already in use for the vault).
- OpenScan (Android FOSS scanner app) on a Motorola Moto G5 — captures
  pages, manual 4-corner perspective crop, exports PDF to the syncthing
  Inbox folder. No HEIC/JPG handling needed in the pipeline.
- OpenChamber (this assistant).
- GitHub account.
- Hugging Face account.
- Ollama cloud pro subscription (for now) — but do NOT bank on ollama long
  term. Plan to move to GLM-5.3 via Z.ai API. Keep the OCR model swappable.

## Constraints

- OCR must run in the cloud (the small local OCR models don't fit in 4GB).
- No expensive subscriptions; low per-page cost is fine.
- Don't lock the pipeline to ollama or to any single OCR vendor.
- Latency is not a concern (Pi runs 24/7, notes are reviewed later).
- Weekend-project scope: keep it simple, defer nice-to-haves.

## Non-goals (for now)

- Real-time processing.
- Processing typed/printed PDFs (handwritten phone scans only for Sprint 1).
- Multi-page document assembly across separate PDFs (a single multi-page
  PDF is assembled natively in Stage 3).
- An LLM fallback for class sorting (backlog item).
- A spelling/flow cleanup pass (backlog item).
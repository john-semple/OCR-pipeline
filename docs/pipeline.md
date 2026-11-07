# Pipeline Spec

Stages run sequentially per file. Each stage has a clear input/output so
stages can be tested in isolation.

## Stage 0 — Watch (Inbox -> Processing)

- Watch `Vault/Resources/Homework and Class Notes/00 Inbox/` for new PDF files from OpenScan
  (synced via Syncthing from the phone).
- **Syncthing safety:** only pick up files that are *stable* — no size change
  for N seconds and the `.sync`/`.tmp` syncthing temp file is gone. This
  avoids grabbing half-transferred files.
- Allowed extensions: `.pdf` (OpenScan exports PDF; see `decisions.md`
  "Input format: PDF"). HEIC/JPG handling is intentionally omitted.
- Compute SHA-256 of the file contents. This is the dedup key.
- **Dedup:** if a note with `scan_hash: sha256:{hash}` already exists in
  the vault, skip (idempotent re-sync). Otherwise move the file to
  `Vault/Resources/Homework and Class Notes/00 Pipeline/Processing/{hash}.pdf`.

## Stage 1 — OCR (Processing -> raw OCR result)

- Send the PDF directly to Mistral OCR — no client-side rasterization.
  Mistral's server-side rasterizer is tuned for OCR and produces better
  equation transcription than `pdf2image` on the Pi (confirmed during
  T02 POC, 2026-08-31: PDF-direct caught a `← I think?` annotation and
  read `2ac` correctly where image-based missed both).
- Flow:
  1. `client.files.upload(file={file_name, content}, purpose="ocr")`
     -> `file_id`
  2. `client.files.get_signed_url(file_id=file_id)` -> `url`
  3. `client.ocr.process(model="mistral-ocr-latest",
     document={"type":"document_url", "document_url": url},
     include_image_base64=True)`
- **Multi-page PDFs** return `resp.pages` with one entry per page —
  no extra work to split them.
- Call `OCREngine.process(pdf_path) -> OCRResult` (interface; concrete
  impl is `MistralOCREngine`).
- `OCRResult` contains:
  - `pages`: list of per-page results, each with `markdown`, `images`,
    `index`.
  - `markdown`: the page as markdown with LaTeX (`$...$` / `$$...$$`)
    and inline `![img-N.jpeg](img-N.jpeg)` placeholders for diagrams.
  - `title`: the first non-empty line of page 0 (the class name the
    user writes).
  - `images` (per page): list of image objects with `id` (e.g.
    `img-0.jpeg`), `image_base64` (data URL format:
    `data:image/jpeg;base64,...`), and bounding box coords
    (`top_left_x/y`, `bottom_right_x/y`).
  - `raw_json`: the full Mistral response, saved to `00 Pipeline/Archive/` for audit.
- **No confidence score** — Mistral OCR does not return a confidence
  score in the current SDK (`mistralai>=1.0.0`). The spec's assumed
  `confidence` / `confidence_scores_granularity` / `include_blocks`
  params do not exist. `ocr_confidence` in frontmatter is set to `null`
  in Sprint 1.
- On API error: retry with backoff (3 attempts), then leave the file in
  `Processing/` and log. Do not move to Unsorted on a transient error.
- API key via env var `MISTRAL_API_KEY` (supplied to the container through
  `docker run --env-file /root/ocr-secrets.env`; see
  `decisions.md` "Secrets management").
- **Cleanup:** delete the uploaded file from Mistral after processing
  (`client.files.delete(file_id=...)`) so the Mistral file store
  doesn't accumulate.

## Stage 2 — Extract metadata

- `title`: first non-empty line of the OCR markdown, trimmed.
- `date`: parse from the OpenScan filename first
  (`OpenScan YYYYMMDD HHMMSSffffff.pdf` — capture time, survives
  re-syncs); fall back to PDF creation metadata, then file ctime with
  `needs_review: true`. No EXIF — OpenScan exports PDF.
- `capture_time`: the parsed filename timestamp (or PDF metadata / ctime
  fallback) as epoch seconds (used for multi-page grouping in Stage 3).
- `scan_hash`: the SHA-256 computed in Stage 0.
- `ocr_confidence`: `null` (Mistral OCR does not return a confidence
  score in the current SDK; see Stage 1 note).
- `tags`: Sprint 1 sets `tags: [needs-tagging]`; the topic-tag generation
  is a Sprint 2 cleanup pass.

## Stage 3 — Group + Classify

### 3a — Multi-page grouping

Before classifying, determine whether this scan is part of a multi-page
note. Two cases:

1. **Multi-page PDF:** a single OpenScan PDF may contain multiple pages.
   These are already a grouped unit — rasterized in Stage 1, reassembled
   here as one note with `## Page N` sections. No cross-file heuristic
   needed.
2. **Cross-file grouping:** separate single-page PDFs shot in quick
   succession may belong to the same note. Attempt to group this scan
   with pending pages of the same note:

- Normalize title (lowercase, strip punctuation/whitespace, strip
  continuation markers: `cont`, `continued`, `contd`, `page n`, `pg n`,
  `p n`, trailing `...`).
- Look at the last ~30 minutes (config: `grouping_window_minutes`) of
  OCR'd-but-not-yet-written pages in `Processing/`.
- A page joins an existing group if ALL of:
  1. Normalized title (after marker strip) matches the group's title.
  2. `|capture_time - group_min_capture_time| <= grouping_window_minutes`.
  3. The page is a "continuation" — its raw title contains a continuation
     marker OR it's the first page (no marker needed).
- If grouped, append this page's content to the group. Otherwise start a
  new single-page group.
- Group key: a UUID assigned when the group is created. Stored in
  `Processing/groups/{group_id}.json` so a crash mid-group is recoverable.

**No-marker same-title pages within the window** are treated as
continuations (a stack of "Thermo" pages shot in 5 minutes = one note).
This handles the user's inconsistent naming without an LLM.

### 3b — Classify

- Use the group's normalized title (marker-stripped) for classification.
- Look up against the classes loaded from the vault's `Classes.md`
  (one class per line: `Class Name: keyword, keyword, ...`; the class
  name is also the folder name). The `classes:` section in
  `config/classes.yaml` is a fallback if `Classes.md` is missing:
  ```markdown
  Thermodynamics & Lab: thermo, thermodynamics, me 301, me301
  Structures and Properties of Materials: structures, materials, mse xxx
  Circuits: circuits, circuit analysis, ee xxx
  ```
- Match = normalized title is in any class's keyword list (exact, or
  substring for short keywords to handle "ME 301 - Lecture 5").
- **Hit** -> `class` = matched class, `folder` = its `folder`.
- **Miss** -> `class` = `Unsorted`,
  `folder` = `Resources/Homework and Class Notes/00 Pipeline/Unsorted`,
  set `needs_review: true`.

## Stage 4 — Write note

- Ensure `Vault/{folder}/` exists.
- Create a per-scan folder: `Vault/{folder}/{date} {title-slug}/`. If a
  folder with that name already exists, append `-2`, `-3`, etc. to the
  folder name.
- For a single-page group: note body = the page's OCR markdown.
- For a multi-page group: note body = a `## Page N` section per page, in
  capture-time order, each containing that page's (diagram-rewritten)
  markdown.
- Save diagram images as PNG files loose in the per-scan folder (next
  to the .md), named `{date} {title-slug} - p{page}-diag{n}.png`
  (single-page: drop the `- p{page}` segment). Rewrite each page's
  markdown image placeholders (`![img-0.jpeg](img-0.jpeg)`) to Obsidian
  wikilinks pointing at the saved file: `![[{diag-name}]]`.
- Note filename: `{folder-name}.md` (i.e. the .md name matches the
  per-scan folder name, e.g. `2026-09-15 Thermodynamics.md` inside
  `2026-09-15 Thermodynamics/`).
- Write frontmatter (see `decisions.md`) + the body markdown.

## Stage 5 — Archive + log

- Move the original scan(s) to
  `Vault/Resources/Homework and Class Notes/00 Pipeline/Archive/{date}-{slug}-{pN}.{ext}`.
- Save the raw OCR JSON per page to
  `00 Pipeline/Archive/{date}-{slug}-{pN}.ocr.json`.
- Append one line per page to `logs/spend.csv`:
  `timestamp, scan_hash, class, pages, estimated_cost_usd`
  (cost = pages * 0.004).
- Remove the file(s) from `Processing/` and the group file from
  `00 Pipeline/Processing/groups/`.

## Periodic cleanup (cron, weekly)

- Prune `00 Pipeline/Archive/` entries older than `archive_retention_days` (default 365).

## Failure modes / notes

- Transient OCR API errors: retry, leave in `Processing/`, log. Don't
  mis-route to `Unsorted/` — that's only for *classification* misses.
- EXIF missing: fall back to file ctime, set `needs_review: true`.
- Unsorted review is manual/weekly: the user reads `needs_review: true`
  notes, adds the class to the vault's `Classes.md`, and re-runs a single
  command to re-sort them (Sprint 2 convenience command).
- The OCREngine interface is the only vendor coupling point. Everything
  else is vendor-agnostic.
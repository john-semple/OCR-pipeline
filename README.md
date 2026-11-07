# OCR Pipeline

A weekend project that turns scanned/photographed homework notes into
searchable Obsidian markdown, running unattended on a Raspberry Pi 4 (4 GB).

PDFs dropped into a watched folder are sent to **Mistral OCR**, classified by
the class name written at the top of each page, and written into the matching
class folder of an Obsidian vault — diagrams included. Misses land in
`Unsorted/` for a weekly manual review.

> ⚠️ This was built for one specific setup (a Pi 4 + Syncthing + OpenScan +
> an Obsidian vault). It works as a reference and a starting point, but you'll
> need to point the paths at your own folders and supply your own Mistral API
> key. See [Setup](#setup) below.

---

## Table of contents

- [What it does](#what-it-does)
- [Why these choices](#why-these-choices)
- [Requirements](#requirements)
- [Setup](#setup)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Testing](#testing)
- [Scheduling and logs (Pi host)](#scheduling-and-logs-pi-host)
- [Repository layout](#repository-layout)
- [Documentation](#documentation)
- [Cost](#cost)
- [License](#license)

---

## What it does

1. You scan/photograph homework pages on your phone (OpenScan exports PDF).
2. Syncthing syncs those PDFs into a watched `Inbox/` folder on the Pi.
3. The pipeline collects stable PDFs, dedups by SHA-256, and sends each
   to **Mistral OCR** — no client-side rasterization; Mistral's server
   rasterizer produces better results. It returns markdown + LaTeX +
   extracted diagram images in one call.
4. The title line (the class name you write at the top of every page) is
   normalized and looked up against the keywords in the vault's
   `Classes.md`. On a hit, the note + diagram images are written to a
   per-scan folder inside the right class folder in your Obsidian vault.
5. Misses go to `Unsorted/` for a weekly manual review.
6. Syncthing replicates the resulting markdown back to your phone so it shows
   up in Obsidian.

## Why these choices

- **Mistral OCR 4.1** for OCR: purpose-built for documents, emits clean
  LaTeX, and returns bounding boxes + extracted diagram images in one call.
  ~$4 / 1000 pages ($2 batch), so ~$10 lifetime for this project. See
  `docs/decisions.md`.
- **Alias-map sorting** with **LLM fallback** (Mistral Small): deterministic
  first pass, then a cheap LLM pass to recover misses. Easy to tune via
  `Classes.md`.
- **Classes.md in the vault**: classes and their keywords live in a plain
  markdown file (`Class Name: keyword, keyword`) synced to your phone —
  edit it in Obsidian, no SSH into the Pi. The watcher reacts to saves
  instantly: new class folders appear and the Sorting Guide regenerates
  within ~10s (a 60s poll backstops slow syncs). Removing a class
  deletes its folder only if it's empty — notes are never deleted.
- **One note per page**: each scan is independent; simpler dedup and
  per-page tracking. Multi-page PDFs and same-title scans within a
  30-minute window are grouped into one note with `## Page N` sections.
- **OpenScan filename timestamp** for `date:` frontmatter (not file mtime,
  which syncthing can alter, and not EXIF — OpenScan exports PDF).
- **Image-hash dedup** to survive syncthing re-syncs.

---

## Requirements

- Python 3.11+
- A [Mistral](https://mistral.ai/) account and API key with access to
  Mistral OCR (`mistral-ocr` model). An LLM key for `mistral-small-latest`
  is used for the classification fallback (same key works).
- poppler-utils (`pdftoppm`) on the host if you ever rasterize PDFs locally
  (the default path lets Mistral rasterize server-side, so this is optional).
- (Optional) Docker, if you want to run it in the container setup this was
  built for — see `Dockerfile` and `docs/deployment.md`.
- (Optional) An Obsidian vault + Syncthing + OpenScan on your phone. The
  pipeline itself doesn't require Obsidian; it just writes markdown to a
  folder you point it at.

### Python dependencies

```
mistralai>=1.0.0
pdf2image>=1.16.0
Pillow>=10.0.0
PyYAML>=6.0.0
watchdog>=3.0.0
requests>=2.31.0
google-genai>=1.0.0
```

Install with:

```bash
pip install mistralai pdf2image Pillow PyYAML watchdog requests google-genai
```

---

## Setup

1. **Clone the repo**

   ```bash
   git clone https://github.com/john-semple/OCR-pipeline.git
   cd OCR-pipeline
   ```

2. **Install Python deps** (see above). A venv is recommended:

   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install mistralai pdf2image Pillow PyYAML watchdog requests google-genai
   ```

3. **Provide your Mistral API key** as an environment variable:

   ```bash
   export MISTRAL_API_KEY="your-key-here"
   ```

   (Optional, for the LLM classification fallback — uses the same key:
   `GEMINI_API_KEY` enables a Gemini-based fallback path.)

4. **Point the pipeline at your folders.** Edit
   [`config/classes.yaml`](config/classes.yaml) and set:

   - `vault_root` — where your Obsidian vault (or any markdown folder) lives
   - `openscan_inbox` — the folder new PDFs arrive in (e.g. Syncthing share)
   - `processing`, `unsorted`, `archive` — pipeline workspace paths
     (defaults live under `{vault}/00 Pipeline/...`)

   See [Configuration](#configuration) below.

5. **Create `Classes.md`** in your vault (path is configurable) listing
   each class and its keywords, one per line:

   ```
   Thermodynamics & Lab: thermo, heat, entropy, carnot
   Linear Algebra: linalg, matrix, eigen, vector
   ```

   The pipeline reads this file each run; you can edit it from your phone
   in Obsidian without touching the Pi.

6. **Dry-run** to verify paths and hashing without calling the API:

   ```bash
   python3 run.py --dry-run
   ```

---

## Quick start

```bash
# Dry run — hash and report, don't move anything or call the API
python3 run.py --dry-run

# Full pipeline — collect, OCR, group, classify, write notes, archive
python3 run.py

# Stop after Stage 0 (collect + dedup only, no API calls)
python3 run.py --stage0-only

# Run once, then keep watching the inbox for new PDFs (blocks forever)
python3 run.py --watch

# Re-sort notes sitting in Unsorted/ against the current aliases
python3 run.py --resort
```

A full run processes every PDF in the inbox (`openscan_inbox`): collects +
dedups → sends to Mistral OCR → extracts date from filename → groups
multi-page scans → classifies by title alias (with LLM fallback) → writes a
per-scan folder per note (`.md` + inline diagram PNGs) to the vault →
archives the originals.

---

## Configuration

All runtime config lives in [`config/classes.yaml`](config/classes.yaml).
Classes themselves are **not** hard-coded here — they're loaded from the
vault's `Classes.md` so you can edit them from your phone. `classes.yaml`
holds paths, thresholds, and tuning knobs:

| Key | What it controls |
| --- | --- |
| `paths.vault_root` | Obsidian vault root |
| `paths.openscan_inbox` | Where new PDFs arrive (watched) |
| `paths.processing` | Transient pipeline workspace |
| `paths.unsorted` | Misses land here for review |
| `paths.archive` | Original scans + raw OCR JSON (pruned after 365 days) |
| `grouping.window_minutes` | Multi-page grouping window (default 30) |
| `archive.retention_days` | How long to keep archived originals (default 365) |

See the file itself for the full list and inline comments, and
`docs/decisions.md` for the rationale behind each knob.

### Vault subtree the pipeline expects

```
Vault/
  Resources/
    Homework and Class Notes/
      Classes.md         # EDITABLE class list: "Class: keyword, keyword"
      Sorting Guide.md   # auto-generated keyword reference
      00 Inbox/          # syncthing drops new scans here (watched dir)
      00 Pipeline/       # pipeline workspace, transient
        Processing/
          groups/        # in-progress multi-page groups (crash-recoverable)
        Unsorted/        # class not recognized -> weekly manual review
          {date} {title}/
            {date} {title}.md
            {date} {title}-diag0.png
        Archive/         # original scans + raw OCR JSON
      {Class}/
        {date} {title}/
          {date} {title}.md
          {date} {title}-diag0.png
        lab/             # optional lab subfolder
```

---

## Testing

```bash
python3 -m pytest tests/ -v    # 115+ unit tests, no API calls
```

Tests use stubbed OCR/LLM engines, so they run offline and cost nothing.
See `docs/testing-protocol.md` for the manual end-to-end checklist.

---

## Scheduling and logs (Pi host)

On the Pi 4, a systemd timer runs the pipeline every 10 minutes via
`docker exec` into the OpenChamber container. Output is captured in
journald:

```bash
journalctl -u ocr-pipeline -e       # latest run
systemctl status ocr-pipeline.timer # next scheduled trigger
```

Systemd unit files live in [`deploy/systemd/`](deploy/systemd/). Install
them on the host:

```bash
sudo cp deploy/systemd/ocr-pipeline.service deploy/systemd/ocr-pipeline.timer \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ocr-pipeline.timer
```

If you'd rather run continuously instead of on a 10-minute timer, use
`python3 run.py --watch` with `Restart=always` in your unit file.

See [`docs/deployment.md`](docs/deployment.md) for full install, manual
runs, and troubleshooting.

---

## Repository layout

```
OCR_pipeline/
  README.md
  run.py             # CLI entry point — runs the full pipeline
  Dockerfile         # extends the host container image with the OCR stack
  docs/
    context.md        # project context, resources, constraints
    decisions.md      # locked decisions + rationale
    deployment.md     # systemd timer + journald logging on the Pi host
    image-requirements.md  # image quality + capture guidelines
    pipeline.md       # pipeline stages as a spec
    sprints.md        # sprint plan + backlog
    system-report.md  # full system description for reference
    testing-protocol.md    # manual end-to-end test checklist
  deploy/
    systemd/          # ocr-pipeline.service + .timer unit files
  config/
    classes.yaml      # paths + settings (classes come from vault Classes.md)
  src/
    config.py         # config loading + path resolution
    logging_setup.py  # rotating file log + stderr
    stage0.py         # collect + dedup + move to Processing/
    stage1.py         # OCREngine interface + MistralOCREngine impl
    stage1_runner.py  # loop over Processing/, run OCR on each PDF
    stage2.py         # date/capture_time extraction from filename
    stage3.py         # multi-page grouping + alias-map classification
    stage4.py         # write per-scan folder (note + diagrams) to vault
    stage5.py         # archive scans + OCR JSON + spend log + cleanup
    llm_fallback.py   # Mistral Small classifier for misses
    alias_readme.py   # generates Sorting Guide.md from aliases
    classes_md.py     # parses Classes.md, creates/prunes class folders
    watcher.py        # inbox + Classes.md watcher (--watch mode)
    resort.py         # re-sort Unsorted/ against current aliases
  tests/              # pytest unit tests (115+ tests)
  logs/               # pipeline.log (rotating) + spend.csv  (gitignored)
```

---

## Documentation

Deeper design notes live in [`docs/`](docs/):

- [`docs/pipeline.md`](docs/pipeline.md) — stage-by-stage spec
- [`docs/decisions.md`](docs/decisions.md) — locked decisions + rationale
- [`docs/deployment.md`](docs/deployment.md) — systemd scheduling + logs
- [`docs/image-requirements.md`](docs/image-requirements.md) — capture tips
- [`docs/testing-protocol.md`](docs/testing-protocol.md) — manual E2E checklist
- [`docs/system-report.md`](docs/system-report.md) — full system reference
- [`docs/sprints.md`](docs/sprints.md) — sprint plan + backlog
- [`docs/context.md`](docs/context.md) — project context + constraints

---

## Cost

~$0.004 per page (3 pages ≈ $0.012). Tracked in `logs/spend.csv`. At the
default rate, a year of homework costs roughly $10.

---

## License

This project is shared as-is for reference and personal use. No warranty.
Mistral and OpenScan are third-party services with their own terms — make
sure you comply with them when sending documents through their APIs.

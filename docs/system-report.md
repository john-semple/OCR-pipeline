# OCR Pipeline — System Report

A detailed explanation of how the OCR pipeline works, end to end. Written
for the user (a student) — not a developer. If you want to understand
what's happening when you scan a page and it shows up in Obsidian, read
this.

---

## The 30-second version

1. You scan a homework page on your phone with OpenScan.
2. Syncthing syncs the PDF to the Pi.
3. The pipeline detects the new file (instantly, via file watcher).
4. It sends the PDF to Mistral's OCR API, which returns markdown + LaTeX
   + any diagrams as images.
5. It reads the first line of the OCR text (the title you wrote at the
   top of the page) and matches it against the keyword list in the
   vault's `Classes.md` (editable in Obsidian on your phone).
6. If the keywords match a class, the note is written to that class's
   folder in your Obsidian vault. If not, an AI fallback tries to guess.
   If the AI can't figure it out either, the note goes to `Unsorted/`.
7. The original scan is archived. Syncthing syncs the new note back to
   your phone. You see it in Obsidian.

Total time: ~30 seconds from scan to note (depending on Syncthing sync
speed and OCR API latency).

---

## The hardware

- **Raspberry Pi 4 (4GB RAM)** running 64-bit Debian.
- The Pi runs 24/7. It's plugged in, connected to your network, and
  always watching for new scans.
- The Pi runs a Docker container (`openchamber-pi4-sbc-02`) that
  contains both OpenChamber (the development environment) and the OCR
  pipeline code.

---

## The software stack

| Component | What it is | Why |
|---|---|---|
| OpenScan (Android) | Phone scanning app | FOSS, no ads, exports PDF, configurable save folder |
| Syncthing | File sync between phone and Pi | Decentralized (no cloud), automatic, runs on both devices |
| Mistral OCR API | Cloud OCR service | Purpose-built for documents, returns markdown + LaTeX + diagrams |
| Mistral Chat API | Cloud LLM (mistral-small-latest) | Fallback class sorting when keywords don't match |
| watchdog | Python file watcher | Triggers the pipeline instantly when a new PDF arrives |
| systemd | Linux service manager | Keeps the pipeline running, restarts on crash, captures logs |
| Obsidian | Note-taking app | Where the notes land — synced back to your phone via Syncthing |

---

## The file flow

```
Phone                        Pi                              Vault
─────                        ───                             ─────

OpenScan app                 Syncthing receives PDF
  ↓                          in /mnt/openscan-inbox/
  ↓                                    ↓
Syncthing sends PDF          File watcher detects it
  ↓                          (within seconds)
  ↓                                    ↓
                             Stage 0: collect + dedup
                                       → move to Processing/
                                             ↓
                             Stage 1: send to Mistral OCR
                                       → get markdown + images
                                             ↓
                             Stage 2: extract date from filename
                                             ↓
                             Stage 3a: group multi-page scans
                                             ↓
                             Stage 3b: classify by title
                                       (alias map → LLM fallback)
                                             ↓
                             Stage 4: write .md note + diagrams
                                       → class folder or Unsorted/
                                             ↓
                             Stage 5: archive original + log cost
                                       → Archive/ + spend.csv
                                             ↓
                             Syncthing syncs vault back to phone
                                       ↓
                            Note appears in Obsidian on phone
```

---

## The pipeline stages (in detail)

### Stage 0 — Collect + dedup

**Input:** PDFs in `/mnt/openscan-inbox/` (the Syncthing mount)
**Output:** Deduplicated PDFs in `Processing/`, renamed to `{sha256}.pdf`

When a new PDF arrives, Stage 0:

1. **Checks stability:** waits 5 seconds and verifies the file size
   hasn't changed. This prevents grabbing a file that Syncthing is
   still writing (half-downloaded).

2. **Filters junk:** ignores hidden files (`.hidden.pdf`), Syncthing
   temp files (`.sync`, `.tmp`, `.lock`), and non-PDF files. Only
   `.pdf` files are processed — OpenScan exports PDF, so there's no
   image path.

3. **Hashes the file:** computes SHA-256 of the entire file content.
   This is the dedup key — it's a fingerprint of the file's bytes, not
   its name. Two files with different names but identical content have
   the same hash.

4. **Checks for duplicates:** if a file with the same hash already
   exists in `Processing/`, the new file is a duplicate (likely a
   Syncthing re-sync). The duplicate is deleted and nothing else
   happens. This makes the pipeline idempotent — re-running it on the
   same files is a no-op.

5. **Moves the file:** the PDF is moved from the inbox to
   `Processing/{hash}.pdf`. A sidecar file (`{hash}.name`) records the
   original filename (e.g. `OpenScan 20260831 150515450040.pdf`) so
   later stages can parse the timestamp from the name. The move is
   cross-device (the inbox and vault are on different filesystems), so
   it falls back to copy+delete.

### Stage 1 — OCR

**Input:** PDFs in `Processing/`
**Output:** OCR results (markdown + LaTeX + diagram images) in memory

For each PDF in `Processing/`:

1. **Upload to Mistral:** the PDF is uploaded to Mistral's file storage
   (`files.upload` with `purpose="ocr"`). This returns a `file_id`.

2. **Get a signed URL:** Mistral generates a temporary URL for the
   uploaded file (`files.get_signed_url`). This URL is what the OCR
   engine reads from.

3. **Run OCR:** the pipeline calls `ocr.process` with the signed URL and
   `include_image_base64=True`. Mistral's server-side rasterizer
   converts the PDF to images and runs OCR. The response contains:
   - `pages`: one entry per page, each with:
     - `markdown`: the page as markdown text with LaTeX (`$...$` /
       `$$...$$`) for equations and `![img-0.jpeg](img-0.jpeg)`
       placeholders for diagrams.
     - `images`: list of extracted diagram images, each with a base64
       data blob and bounding box coordinates.

4. **Delete the uploaded file:** the file is removed from Mistral's
   storage (`files.delete`) so it doesn't accumulate.

5. **Retry on failure:** if the API call fails, the pipeline retries up
   to 3 times with exponential backoff (2s, 4s, 6s). If all retries
   fail, the PDF stays in `Processing/` for the next run. It is NOT
   moved to Unsorted — Unsorted is only for classification misses, not
   API errors.

**Why no client-side rasterization?** During the POC (2026-08-31), we
compared two paths: (a) rasterize the PDF on the Pi with `pdf2image`
and send images to Mistral, vs (b) send the PDF directly and let
Mistral's server rasterize it. Path (b) was better — it caught a
handwritten `← I think?` annotation that path (a) missed, and correctly
read `2ac` where path (a) read `2mc`. Mistral's rasterizer is tuned for
OCR; `pdf2image` on the Pi is not. `pdf2image` + `poppler-utils` remain
in the Docker image as a fallback for edge cases (corrupt PDFs that
Mistral rejects) but are not the primary path.

**Cost:** ~$0.004 per page ($4 per 1,000 pages). At ~1,000 pages/year,
lifetime cost is ~$10. Tracked in `logs/spend.csv`.

### Stage 2 — Extract metadata

**Input:** One OCR result + its original filename
**Output:** Date + capture timestamp

The pipeline needs to know when you scanned the page (for the note's
`date:` frontmatter and for multi-page grouping). It tries three sources
in order:

1. **OpenScan filename** (primary): files are named
   `OpenScan YYYYMMDD HHMMSSffffff.pdf` (microsecond precision). The
   pipeline parses this with the regex
   `^OpenScan (\d{8}) (\d{6})(\d{6})\.pdf$`. This is the actual capture
   time, encoded at scan time, and it survives Syncthing re-syncs (the
   filename never changes).

2. **PDF metadata** (fallback): if the filename doesn't match the
   OpenScan pattern (e.g. the file was renamed), the pipeline runs
   `pdfinfo` to check for a `CreationDate` in the PDF metadata. In
   practice, OpenScan PDFs don't have this field — it's always empty.
   This fallback exists for renamed or external PDFs.

3. **File ctime** (last resort): if neither the filename nor PDF
   metadata has a timestamp, the pipeline uses the file's creation time
   on the filesystem. This sets `needs_review: true` because ctime can
   be unreliable (Syncthing can alter it on re-sync).

**No EXIF:** OpenScan exports PDF, not JPG. PDFs don't have EXIF data.
The original design used EXIF `DateTimeOriginal`, but this was changed
after discovering that OpenScan outputs PDF.

### Stage 3a — Multi-page grouping

**Input:** List of OCR results with metadata
**Output:** Groups (one or more OCR results that become a single note)

The pipeline groups scans into "notes." Most scans are single-page
notes, but sometimes you scan multiple pages of the same homework. Two
grouping scenarios:

1. **Multi-page PDF:** a single OpenScan PDF with multiple pages. This
   is already a grouped unit — Stage 1 returns one OCR result with N
   pages. No cross-file heuristic needed.

2. **Cross-file grouping:** separate single-page PDFs shot in quick
   succession. The pipeline tries to group them if ALL of:
   - Their normalized titles match (after stripping continuation
     markers like "cont", "continued", "page 2", "pg 2").
   - Their capture times are within `grouping_window_minutes` (default
     30) of each other.
   - At least one page has a continuation marker OR they're the first
     page (no marker needed for the first page).

   **No-marker same-title pages** within the window are also treated as
   continuations. So if you shoot 5 pages all titled "Thermo" within 5
   minutes, they become one note. This handles the case where you
   don't bother writing "page 2" on continuation pages.

**Title normalization:** the title is lowercased, punctuation is
stripped, and continuation markers are removed before comparison. So
"Thermo cont" and "Thermo" both normalize to "thermo" and can be grouped.

**Group persistence:** groups are written to
`Processing/groups/{group_id}.json` so a crash mid-group is recoverable.
These files are cleared at the start of each run.

### Stage 3b — Classification

**Input:** One group (with a normalized title)
**Output:** A class name + destination folder

The pipeline determines which class folder the note goes to. Three
passes, in order:

1. **Exact match (keyword map):** the normalized title is compared
   exactly against every keyword in the vault's `Classes.md`. If the
   title exactly equals a keyword (after both are lowercased +
   stripped), it's a hit.
   This is checked first so that "thermodynamics" matches the exact
   alias "thermodynamics" before the short alias "thermo" matches it as
   a substring.

2. **Substring match (short keywords only):** for keywords ≤12
   characters, the title just needs to *contain* the keyword. So "hw 3
   thermo lecture notes" matches keyword "thermo". Longer keywords only
   match exactly to
   avoid false positives (e.g. "materials" shouldn't match "materials
   science engineering 101" unless that's an exact alias).

3. **LLM fallback:** if no keyword matches, the pipeline asks
   `mistral-small-latest` (Mistral's chat API) to pick the best class
   from the loaded class list (from `Classes.md`). The LLM gets the raw
   title and the class names, and responds with the class name or
   "NONE". The response is matched back to actual class entries — a
   hallucinated class name that doesn't exist is treated as NONE.
   If the LLM call fails (API error), the note goes to Unsorted
   (graceful degradation).

**On a miss (all three passes fail):** the note goes to `Unsorted/` with
`needs_review: true`. You review it manually, add the right keyword to
`Classes.md`, and run `--resort` to move it.

**Important:** classification only reads the **first line** of the OCR
text (the title you write at the top of the page). The body text is
never used for sorting. So mentioning "thermo" on page 3 of a circuits
note cannot route it to Thermodynamics.

### Stage 4 — Write note

**Input:** One group + its classification + OCR results
**Output:** A per-scan folder in the vault containing a `.md` note + loose diagram PNGs

The pipeline assembles the note:

1. **Frontmatter** (YAML metadata at the top):
   ```yaml
   ---
   class: "Thermodynamics & Lab"
   date: 2026-09-01
   source_scan: 00 Pipeline/Archive/2026-09-01-OpenScan-...pdf
   scan_hash: sha256:25fadd0b0d97...
   ocr_confidence: null
   tags: [needs-tagging]
   needs_review: false
   pages: 1
   ---
   ```
   - `class` is quoted so the `&` in "Thermodynamics & Lab" doesn't
     break YAML parsing.
   - `ocr_confidence` is `null` because Mistral's SDK doesn't return a
     confidence score (as of the POC, 2026-08-31).
   - `tags` is `[needs-tagging]` — a placeholder. Sprint 2's spelling
     cleanup was going to replace this with real tags, but that was
     skipped (risk of corrupting equations). You tag notes manually in
     Obsidian.
   - `needs_review` is `true` for Unsorted notes, `false` for matched
     classes.

2. **Body:**
   - **Single-page note:** the OCR markdown directly.
   - **Multi-page note:** `## Page 1`, `## Page 2`, etc. sections in
     capture-time order, each containing that page's OCR markdown.

3. **Diagrams:** Mistral returns diagram images as base64 data. The
   pipeline decodes them and saves as PNG files loose in the per-scan
   folder (next to the .md). The OCR markdown's image placeholders
   (`![img-0.jpeg](img-0.jpeg)`) are rewritten to Obsidian wikilinks
   (`![[2026-09-01 Thermo-Notes-diag0.png]]`). Wikilinks handle spaces
   in filenames natively and render reliably in Obsidian. Diagrams
   appear inline in the note at the position Mistral detected them.

4. **Filename:** Each scan gets its own folder
   `{Class}/{date} {title-slug}/` containing the .md (named to match
   the folder) + diagram PNGs loose alongside it. If a folder with
   that name already exists, `-2`, `-3` is appended to both folder
   and .md.

### Stage 5 — Archive + log

**Input:** Processed groups
**Output:** Archived scans + OCR JSON + spend log entries

After the note is written:

1. **Archive the original scan:** the PDF is moved from `Processing/`
   to `00 Pipeline/Archive/` as `{date}-{slug}.pdf`. This is your audit
   trail — if the OCR made a mistake, you can look at the original.

2. **Save raw OCR JSON:** the full Mistral API response is saved to
   `Archive/{date}-{slug}.ocr.json`. This contains everything Mistral
   returned — useful for debugging or reprocessing later.

3. **Log spend:** one line is appended to `logs/spend.csv`:
   `timestamp, scan_hash, class, pages, estimated_cost_usd`. Cost is
   `pages × $0.004`. This lets you track exactly how much you're
   spending.

4. **Clean up:** the PDF, the `.name` sidecar, and the group JSON file
   are removed from `Processing/`. After a successful run, `Processing/`
   is empty.

---

## The file watcher

Instead of running on a timer (every 10 minutes), the pipeline uses
`watchdog` (a Python library that wraps Linux's inotify) to watch the
inbox directory for new files. When Syncthing drops a new PDF:

1. **Events fire:** Syncthing creates the file, writes data, and closes
   it. `watchdog` generates events for each of these (`on_created`,
   `on_modified`, `on_closed`).

2. **Debounce:** the watcher collects events and resets a 10-second
   timer on each one. Only when 10 seconds pass with no new events does
   the watcher trigger a pipeline run. This coalesces Syncthing's burst
   of events into one run — you don't get 5 pipeline runs for one file.

3. **Filter:** the watcher ignores non-PDF files, hidden files, and
   Syncthing temp files (`.sync`, `.tmp`).

4. **Trigger:** the debounce expires → `run_pipeline()` is called →
   Stages 0→5 run → notes are written → watcher goes back to waiting.

The watcher is a long-running process managed by systemd
(`ocr-pipeline.service`). If it crashes, systemd restarts it within 10
seconds (`Restart=always`). Logs go to journald
(`journalctl -u ocr-pipeline`).

---

## The sorting guide

Two files live in `Homework and Class Notes/` in your vault:

- **`Classes.md`** — *you edit this.* One class per line:
  `Class Name: keyword, keyword, ...`. The class name is also the
  folder name (auto-created on the next run). Synced to your phone, so
  you can add a class from Obsidian without SSH-ing into the Pi.
- **`Sorting Guide.md`** — *auto-generated* on every pipeline run from
  the loaded classes. It shows:

- Each class and its folder
- All keywords (aliases) for each class
- How matching works (exact → substring → AI fallback)
- Instructions for adding new keywords or classes

To add a keyword (e.g. you call it "thermy"): open `Classes.md`, add
"thermy" to the class's line after the colon, re-run the pipeline (or
wait for the watcher to trigger). The guide updates automatically. If
you have notes in Unsorted that should now match, run
`python3 run.py --resort` to re-sort them.

---

## The resort command

```bash
python3 /home/ubuntu/workspaces/OCR_pipeline/run.py --resort
```

After editing `Classes.md` (adding new keywords or a new class), this
command re-sorts everything in `Unsorted/`:

1. Reads each per-scan folder in `Unsorted/` (each contains a .md +
   loose diagram PNGs).
2. Extracts the title from the .md (first non-frontmatter line).
3. Re-classifies using the alias map + LLM fallback.
4. On a hit: moves the entire per-scan folder to the class folder,
   updates frontmatter (`class` and `needs_review`).
5. On a miss: leaves the per-scan folder in Unsorted.

This is how you handle notes that were initially unsorted because you
hadn't added the right alias yet. Add the alias, run `--resort`, done.

---

## Cost

| Item | Cost |
|---|---|
| Mistral OCR | $0.004 per page ($4/1,000 pages) |
| Mistral Chat (LLM fallback) | ~$0.001 per miss (50 tokens, $0.10/Mtok) |
| Everything else | $0 (Syncthing, systemd, Obsidian, OpenScan are free) |

At ~1,000 pages/year, annual cost is ~$4 for OCR + ~$1 for LLM fallback
= **~$5/year**. Tracked in `logs/spend.csv`.

---

## Secrets

The Mistral API key lives in `/root/ocr-secrets.env` on the Pi host
(root-owned, `chmod 600`). It's passed to the container via
`docker run --env-file`. Inside the container, it's read from the
`MISTRAL_API_KEY` environment variable. It is NEVER in the repo, in
`classes.yaml`, or in the vault (which is Syncthing-synced to your
phone).

---

## Crash recovery

The pipeline is designed to survive crashes:

- **If the watcher crashes:** systemd restarts it in 10 seconds.
- **If the pipeline crashes mid-OCR:** the PDF stays in `Processing/`.
  The next run re-OCR's it. Cost: ~$0.004 wasted. No data loss.
- **If the pipeline crashes after writing the note but before
  archiving:** the PDF stays in `Processing/`, but the note is already
  in the vault. The next run re-OCR's the PDF and writes a duplicate
  note (with `-2` suffix). You'd delete the duplicate manually. This is
  rare — Stage 5 runs immediately after Stage 4.
- **If the Pi reboots:** systemd auto-starts the watcher
  (`systemctl enable ocr-pipeline`). Any PDFs still in `Processing/`
  are processed on the next run.
- **If Syncthing re-syncs an already-processed file:** SHA-256 dedup
  catches it. The re-synced file is deleted, no new note is created.

**The only data loss scenario:** a crash that corrupts the filesystem
*and* destroys the SD card. This would lose anything not yet synced by
Syncthing. Mitigation: the Pi's vault is synced to your phone, so notes
that have been written are already on your phone. The original scans
are in `Archive/` (on the Pi) — if the SD card dies, you lose the
archives but not the notes.

---

## The vault structure

```
Vault/
  Resources/
    Homework and Class Notes/
      Classes.md               ← YOU EDIT THIS: "Class: keyword, keyword"
      Sorting Guide.md          ← auto-generated keyword reference
      00 Inbox/                 ← (vault inbox, rarely used — Syncthing
                                  mount is the primary inbox)
      00 Pipeline/              ← pipeline workspace (transient)
        Processing/             ← PDFs being processed (empty after run)
          groups/               ← group state files (cleared each run)
        Unsorted/               ← notes that didn't match any class
          2026-09-01 Random/    ← per-scan folder (same layout as class)
            2026-09-01 Random.md
            2026-09-01 Random-diag0.png
        Archive/                ← original scans + raw OCR JSON (1 year)
      Thermodynamics & Lab/
        2026-09-01 Thermo/      ← one folder per scan
          2026-09-01 Thermo.md  ← note (named to match folder)
          2026-09-01 Thermo-diag0.png   ← diagrams loose next to .md
      Circuits/
        lab/                    ← (manual — lab routing not automated)
        2026-09-01 Circuits/
          2026-09-01 Circuits.md
          2026-09-01 Circuits-diag0.png
      Structures and Properties of Materials/
        lab/
        2026-09-01 Materials/
          2026-09-01 Materials.md
          2026-09-01 Materials-diag0.png
```

---

## Configuration

Configuration is split across two files:

**The vault's `Classes.md`** (editable in Obsidian, synced to your
phone) — the classes themselves:

- One class per line: `Class Name: keyword, keyword, ...`
- The class name is also the folder name; the folder is auto-created
  in `Homework and Class Notes/` on the next pipeline run.
- Keywords match the title line of a scan (case-insensitive). Prose,
  headings, and code fences in the file are ignored.

**`config/classes.yaml`** (in the repo, on the Pi) — infrastructure:

- **`settings`**: grouping window (30 min), archive retention (365
  days), spend log path.
- **`paths`**: vault root, inbox, processing dirs, stability timeout.
  Override any with env vars (`OCR_VAULT_ROOT`, etc.) for testing.
- **`classes`**: *fallback only* — used if `Classes.md` is missing or
  has no class lines.

Edit either freely. Re-run the pipeline (or wait for the watcher) to
pick up changes. Run `--resort` to re-sort Unsorted notes against new
keywords.

---

## The CLI

```bash
# Full pipeline (Stages 0→5) — run once and exit
python3 run.py

# Full pipeline, then watch for new files (blocks forever)
python3 run.py --watch

# Re-sort Unsorted/ notes against current aliases
python3 run.py --resort

# Dry run — hash and report, don't move anything or call the API
python3 run.py --dry-run

# Stop after Stage 0 (collect + dedup only, no API calls)
python3 run.py --stage0-only
```

---

## Testing

See `Testing Protocol.md` in this folder for a 10-test checklist that
verifies every feature works end-to-end with real scans.

---

## What's not implemented (and why)

- **Auto-generated topic tags:** the `tags: [needs-tagging]` placeholder
  stays. The LLM spelling cleanup pass that would replace it was skipped
  — the risk of corrupting equations or changing meaning exceeds the
  value. You tag notes manually in Obsidian.
- **Lab subfolder routing:** `lab/` folders exist under Circuits and
  Structures, but the pipeline routes all notes to the class root. Lab
  sorting is manual for now.
- **File watcher on the Pi host:** the watcher runs inside the container
  via `docker exec`. inotify events on bind-mounted directories propagate
  into the container on Linux (ext4 → overlayfs → container), which works
  on the Pi 4. If the Pi's storage were on a network mount, this might
  not work.
- **Dataview plugin:** not installed in the vault yet. The frontmatter
  is Dataview-compatible (verified YAML), but you need to install the
  plugin in Obsidian to query notes by class/date.
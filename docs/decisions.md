# Locked Decisions

## OCR: Mistral OCR 4.1

**Decision:** Use the Mistral OCR 4.1 API (`mistral-ocr-latest`) for OCR.

**Rationale:**
- Purpose-built for documents; emits clean markdown + LaTeX.
- Returns bounding boxes, block classification (titles/tables/equations/
  signatures), per-block confidence, and **extracted diagram images** in one
  call. Diagram handling is turnkey — critical for an engineering major.
  **POC note (2026-08-31):** per-block confidence is *not* returned by
  the current SDK (`mistralai>=1.0.0`) — the `include_blocks` and
  `confidence_scores_granularity` params do not exist. `ocr_confidence`
  is `null` in Sprint 1. Bounding boxes *are* returned on extracted
  images (`top_left_x/y`, `bottom_right_x/y`).
- Doc-as-prompt mode can force a structured title field (the class name) as
  JSON, which is the sorting key.
- Cost ~$4 / 1000 pages ($2 with Batch API). At <1k pages/year this is
  ~$10 lifetime — effectively free for this project.

**Alternatives rejected:**
- Gemini 2.5 Flash: cheaper (likely $0 on free tier) and better at inferring
  intended words from bad handwriting/spelling, but does NOT return
  extracted diagram images or reliable bounding boxes. Would require a
  separate layout/crop pass. More pipeline complexity for a weekend project.
- Mathpix: gold standard for handwritten math but real money at scale and
  another vendor. Not needed given Mistral's quality.
- Local OCR models (GLM-OCR etc.): the usable ones don't fit in 4GB RAM.

**Risk noted:** Mistral is a literal transcriber and will reproduce the
user's misspellings, which can hurt Obsidian search recall. Mitigation is a
backlog item: an optional text-LLM cleanup pass (Gemini free tier or future
GLM-5.3) that fixes obvious spelling while preserving equations.

**Swappability:** the OCR call sits behind one interface (`OCREngine`) so
Gemini or another vendor can be A/B tested later without touching the
pipeline.

## Sorting: alias map, no LLM fallback (Sprint 1)

**Decision:** Sort pages using a deterministic alias map (`classes.yaml`).
Lowercase + strip the OCR'd title, look it up. Misses go to `Unsorted/`.

**Rationale:**
- No extra API cost, no extra latency, easy to tune by editing a YAML file.
- Handles the common case once tuned for a few weeks of classes.

**Backlog:** Add an LLM fallback (Gemini free tier or GLM-5.3) when no alias
hits — send the detected title + known classes and ask "which class, or
NONE?". Only build this if the miss rate is actually painful.

## Class config: vault Classes.md (replaces yaml classes section)

**Decision:** Classes and their keywords live in `Classes.md` in the
vault (`Homework and Class Notes/Classes.md`), one class per line:

```markdown
Dynamics: dyno, dynamics, esg36
```

The text before the colon is both the class name and the folder name
(auto-created under `Homework and Class Notes/` on each run). The
comma-separated keywords after the colon are the sort keywords
(case-insensitive, same matching rules as before). Prose, headings, and
code fences in the file are ignored. The `classes:` section in
`config/classes.yaml` remains only as a fallback when `Classes.md` is
missing.

**Rationale:**
- The vault syncs to the phone via Syncthing, so classes can be added
  or tuned from Obsidian in seconds — no SSH into the Pi to edit YAML.
- The pipeline reloads the file on every run, so a saved edit takes
  effect immediately (watcher-triggered runs included).
- Auto-created folders remove the manual scaffold step entirely; the
  pipeline is the only thing that needs to touch the folder tree.
- `Sorting Guide.md` stays as auto-generated output so the edit surface
  (Classes.md) and the reference output are never the same file.

**Removing a class prunes its folder — only if empty:** when a class
line disappears from Classes.md, its folder is deleted *only if it
contains nothing* (no notes, no PNGs, no hidden files). A folder that
still has notes is never touched — a warning is logged and cleanup is
manual. So an accidental line deletion can never lose work. Runs on
every pipeline start and on every Classes.md change.

**Folder creation is instant, not scan-gated:** the watcher also watches
`Classes.md` itself. On change (debounced 10s, same as PDFs) it runs a
light callback — reload classes, create new folders, regenerate the
Sorting Guide — never a full pipeline run. Three layers keep it safe:
(1) only the exact filename `Classes.md` triggers (the pipeline never
writes that file, so no feedback loop; Syncthing's temp+rename sync
means the real-name event only fires once content is complete);
(2) the 10s debounce coalesces event bursts; (3) a 60s content-hash
poll backstops inotify misses on the vault bind-mount — observed in
testing: a rename-style write (how Syncthing propagates phone edits)
generated no inotify event, and the poll caught it.

**Adopted 2026-09-04 (instant folder creation added same day).**

## Note granularity: one note per page

**Decision:** Each scanned page becomes its own markdown note.

**Rationale:**
- Each scan is independent; simpler dedup (image hash) and per-page
  confidence tracking.
- Matches how phone scans arrive (individual photos, not multi-page PDFs).
- A later sprint could assemble related pages into a session note if useful.

## Vault layout

```
Vault/
  Resources/
    Homework and Class Notes/
      00 Inbox/            # syncthing drops new scans here (watched dir)
      00 Pipeline/         # pipeline workspace, transient
        Processing/
          groups/          # in-progress multi-page groups (crash-recoverable)
        Unsorted/          # class not recognized -> weekly manual review
        Archive/           # original scans + raw OCR JSON kept for audit
      {Class}/             # e.g. Thermodynamics & Lab/
        YYYY-MM-DD {title}/    # one folder per scan
          YYYY-MM-DD {title}.md
          YYYY-MM-DD {title}-diag{n}.png   # diagrams loose next to .md
        lab/               # optional, for classes with a lab component
```

- The `00` prefix on `00 Inbox` and `00 Pipeline` keeps them at the top of
  Obsidian's file tree, separate from class folders.
- Pipeline working dirs (Processing/Unsorted/Archive) are grouped under
  `00 Pipeline/`, a sibling of the class folders, not interleaved with them.
- Per-scan folder: each scan gets its own folder containing the .md +
  diagram PNGs loose alongside it. This keeps each scan self-contained
  and simplifies the image link path (just the bare filename).
- `lab/` subfolder exists where the class has a lab component (Circuits,
  Structures and Properties of Materials); routing into it is a later
  sprint concern, manual for now.
- Diagrams named `{date} {slug}-diag{n}.png` and referenced as Obsidian
  wikilinks: `![[{diag-name}]]`. Wikilinks handle spaces in filenames
  natively and render reliably in Obsidian regardless of the
  "New link format" setting. Standard markdown `![](path)` with spaces
  is unreliable in Obsidian — it fails to resolve and creates an empty
  note on click instead.

## Frontmatter on every note

```yaml
---
class: Thermodynamics
date: 2026-09-15          # from OpenScan filename timestamp, else PDF metadata / ctime
source_scan: 00 Pipeline/Archive/YYYY-MM-DD-{slug}.pdf
scan_hash: sha256:...     # dedup key, survives syncthing re-syncs
ocr_confidence: null       # Mistral OCR SDK does not return confidence (POC 2026-08-31)
tags: [entropy, second-law]   # auto-generated (Sprint 2; manual until then)
needs_review: false
---
```

This frontmatter is what makes Obsidian search precise: `line:(class:
Thermodynamics) entropy`, or Dataview tables by date.

## Dedup: image content hash

**Decision:** SHA-256 of the image bytes is the dedup key, stored in
frontmatter and checked before processing.

**Rationale:** Syncthing re-syncs and re-saves happen; filename and mtime
are not reliable. A content hash makes dedup idempotent — re-syncing an
already-processed image is a no-op.

## Scanning app: OpenScan (output PDF)

**Decision:** Use the OpenScan Android app to scan pages. Set its save
folder to the syncthing Inbox folder. OpenScan exports PDF (confirmed
from actual inbox contents — `OpenScan YYYYMMDD HHMMSSffffffXXX.pdf`).

**Rationale:**
- FOSS, no ads, no data collection. Configurable save folder is the key
  feature — Lens/vFlat push you toward their own cloud/storage and
  fight a custom save dir, which would break the pipeline.
- Uses the phone's native camera, so no quality cap below hardware.
- Review-before-save is built in (gate each capture as "good enough").
- PDF output means the pipeline needs `pdf2image` + `poppler-utils` to
  rasterize pages before sending to Mistral OCR. Both are in the image
  (see `docs/image-requirements.md`).

**Tradeoff accepted:** OpenScan has *manual* 4-corner perspective crop,
not the auto-edge-detection that Lens/Drive scan offer. User will
square manually. This is acceptable because Mistral OCR handles
moderate skew itself, and handwritten notes lack the clean printed
edges auto-detect relies on anyway.

## Input format: PDF (sent directly to Mistral, no client rasterization)

**Decision:** Sprint 1 accepts `.pdf` from OpenScan. Stage 1 uploads the
PDF directly to Mistral (via `files.upload` + `files.get_signed_url` +
`ocr.process` with `document_url`) — no client-side `pdf2image`
rasterization.

**Rationale:** OpenScan on the user's phone exports PDF (confirmed from
actual inbox contents, 2026-08-31). The inbox contains three
`OpenScan *.pdf` files.

POC comparison (2026-08-31, same scan, both paths tested):
- **Image-based** (manual `pdf2image` rasterize + `image_url`):
  missed a `← I think?` annotation, misread `2ac` as `2mc`.
- **PDF-direct** (upload + `document_url`): caught the annotation,
  read `2ac` correctly. Mistral's server-side rasterizer is tuned for
  OCR and produces better equation transcription than `pdf2image` on
  the Pi.
- Cost: one extra API round-trip (upload + signed URL) before the OCR
  call. Acceptable on a 24/7 Pi where latency is not a concern.

`pdf2image` + `poppler-utils` remain in the image as a fallback path
for edge cases (e.g. a corrupt PDF that Mistral rejects) but are not
the primary path. See `docs/image-requirements.md`.

**Consequence — date source changes:** PDFs do not carry photo EXIF
`DateTimeOriginal`. The `date:` frontmatter uses the OpenScan filename
timestamp first, then PDF creation metadata, then file ctime. See the
"Date source" decision below.

## Date source: filename timestamp, then PDF metadata, then ctime

**Decision:** Extract the capture timestamp from the OpenScan filename
first; fall back to PDF creation metadata, then file ctime.

**Priority order:**
1. **OpenScan filename** — files are named
   `OpenScan YYYYMMDD HHMMSSffffff.pdf` (microsecond precision). Parse
   this directly. It's the capture time, survives Syncthing re-syncs
   (filename doesn't change), and doesn't depend on OpenScan writing
   PDF metadata.
2. **PDF creation metadata** — if the filename pattern doesn't match
   (e.g. file was renamed), check PDF creation metadata via `pdfinfo`
   or `pypdf`.
3. **File ctime** — last resort; set `needs_review: true`.

**Rationale:** The original decision used photo EXIF `DateTimeOriginal`,
but OpenScan exports PDF, not JPG — there is no EXIF. Syncthing can
alter file mtime on re-sync, so ctime is the more stable filesystem
fallback on Linux. But the OpenScan filename is the most reliable
source of all: it's the actual capture time, encoded at scan time, and
immutable across re-syncs. It should be checked first.

**Filename regex:** `^OpenScan (\d{8}) (\d{6})(\d{6})\.pdf$` where
group 1 = `YYYYMMDD`, group 2 = `HHMMSS`, group 3 = microseconds.

**Resolved during T02 POC (2026-08-31):** the filename pattern is
stable across all three inbox PDFs. PDF metadata contains *no*
creation timestamp (`pdfinfo` shows no `CreationDate`/`ModDate`), so
the filename is the only reliable timestamp source. Priority 1
(filename) is the primary path; priority 3 (ctime) is the fallback
when the filename doesn't match. PDF metadata (priority 2) is
effectively a no-op for OpenScan PDFs but kept in the chain for
renamed/external PDFs.

## Deployment: single container with OpenChamber (64-bit Pi 4)

**Decision:** The OCR pipeline runs inside the *same* container as
OpenChamber — not a separate pipeline container. The OpenChamber image
(`openchamber-pi4-sbc-02:latest`) is extended via a Dockerfile in this
repo (`OCR_pipeline/Dockerfile`) that layers the OCR stack on top. The
pipeline code itself is plain Python files in
`/home/ubuntu/workspaces/OCR_pipeline/`, editable through OpenChamber.

**Rationale:**
- User wants to build and edit the pipeline through OpenChamber in one
  container, not juggle a second container.
- The Dockerfile is `FROM openchamber-pi4-sbc-02:latest`, so the OCR
  deps (mistral SDK, pdf2image, poppler, etc.) sit on top of the
  OpenChamber base. Rebuilding is a one-command `docker build` when
  deps change or the base image is updated.
- 64-bit Debian host → ARM64 base → pip wheels resolve cleanly.

**Tradeoff accepted:** when the OpenChamber base image is updated, the
extended image must be rebuilt (`docker build` on the host, a few
minutes) so the OCR deps ride on top of the new base. This is
acceptable for a single-user weekend project.

**Build + run (host):**
```bash
docker build -t openchamber-pi4-sbc-02-ocr:latest \
  /home/ubuntu/workspaces/OCR_pipeline/

docker run --env-file /root/ocr-secrets.env \
  -v <vault>:/home/ubuntu/Vault \
  -v <openscan-inbox>:/mnt/openscan-inbox \
  -v <workspaces>:/home/ubuntu/workspaces \
  <...other OpenChamber args...> \
  openchamber-pi4-sbc-02-ocr:latest
```

**Replaced earlier plan:** the original spec assumed a standalone
pipeline container with its own systemd unit. The pipeline is now a
tenant of the OpenChamber container; scheduling is a **host** systemd
timer that runs `docker exec` into that container every 10 minutes
(see `docs/deployment.md`).

## Vault path: Resources/Homework and Class Notes/{Class}/

**Decision:** Notes go to `Resources/Homework and Class Notes/{Class}/`
inside the existing vault. Inbox is `Resources/Homework and Class
Notes/00 Inbox/` (syncthing drop target). Pipeline working dirs
(Processing/Unsorted/Archive) live under
`Resources/Homework and Class Notes/00 Pipeline/`. Each scan gets its
own subfolder `Resources/Homework and Class Notes/{Class}/{date} {title}/`
containing the .md + diagram PNGs loose alongside it. Classes with a
lab component have a `lab/` subfolder (Circuits, Structures and
Properties of Materials).

**Rationale:** User's vault already has a `Resources/` tree; the
"Homework and Class Notes" subtree sits under it, with `00`-prefixed
pipeline dirs sorted to the top of Obsidian's tree and class folders as
plain-named siblings. Keeps OCR notes in a self-contained subtree the
user rarely edits by hand.

## Diagrams: inline embedding (Sprint 1)

**Decision:** Embed diagram images inline in the note at the position
Mistral detected them. Mistral's markdown already contains
`![img-0.jpeg](img-0.jpeg)` placeholders; the `images` field carries
base64 data. Save each as a PNG loose in the per-scan folder (next to
the .md), rewrite the placeholder to an Obsidian wikilink
`![[{diag-name}]]`. No extra layout/crop pass needed.

**Rationale:** Confirmed from Mistral docs — `include_image_base64=True`
returns both the inline placeholder and the image bytes. This is turnkey,
not a hard extra stage. User explicitly wanted inline embedding.

## Note granularity: one note per page, with multi-page grouping

**Decision:** Default is one note per page. When two or more scans share
the same normalized title AND their EXIF capture times are within
{N} minutes of each other AND a later page's title contains a
continuation marker ("cont", "continued", "page 2", "pg 2", ...), the
pages are grouped: a single note is created with a section per page
(`## Page 1`, `## Page 2`, ...), diagrams stay inline per page.

**Rationale:** User wants multi-page notes when safe. The title +
time + marker heuristic prevents mix-ups without an LLM call. Grouping
window default {N} = 30 minutes (tunable in config). Pages that don't
match the heuristic stay as single notes.

**Risk:** If a continuation marker is missed or the time gap is too big,
pages land as separate notes — recoverable by hand. Worst case is two
notes instead of one, not a mis-merge.

## Tags in Sprint 1: `[needs-tagging]` placeholder

**Decision:** Every Sprint 1 note gets `tags: [needs-tagging]` in
frontmatter. Sprint 2's LLM cleanup pass replaces this with real
topic tags.

**Rationale:** User chose this so untagged notes are filterable in
Obsidian immediately.

## Archive retention: prune after 1 year

**Decision:** Original scans + raw OCR JSON live in `Archive/` for
~365 days, then are pruned by a periodic cleanup job.

**Rationale:** User wants a long retention for audit but not forever
(SD card space on the Pi). A simple cron/weekly job deletes Archive
entries older than 365 days.

## Cost tracking: spend log

**Decision:** Append one line per processed page to
`logs/spend.csv` with columns: `timestamp, scan_hash, class, pages,
estimated_cost_usd`. A small `cost_report` helper sums it.

**Rationale:** User wants visibility into spend. Mistral is
~$4/1000 pages, so cost = pages * 0.004.

## Secrets management: host env-file, never in repo or vault

**Decision:** The Mistral API key is supplied to the container via
`docker run --env-file /root/ocr-secrets.env` (root-owned, `chmod 600`,
lives on the host at `/root/ocr-secrets.env`, outside both the repo and
the Syncthing-synced vault). Inside the container it is read from the
`MISTRAL_API_KEY` environment variable only.

**Rationale:**
- The vault is Syncthing-synced to the phone and other devices — a key
  committed there would propagate in plaintext to every synced device.
- The repo may eventually be pushed to GitHub — a key in `config.yaml`
  would leak.
- A host-side `--env-file` survives container recreation (unlike an
  in-container file on the writable layer, which is lost on `docker rm`),
  while staying off any network-synced or shareable path.
- `chmod 600` + root ownership means no unprivileged process on the host
  can read it; it is not served on any network port, so "accessible from
  school wifi" is not a meaningful exposure surface for this file.

**Setup steps (run on the Pi host, not in the container):**
```bash
read -s -p "Paste Mistral API key: " KEY
echo "MISTRAL_API_KEY=$KEY" | sudo tee /root/ocr-secrets.env > /dev/null
sudo chmod 600 /root/ocr-secrets.env
sudo chown root:root /root/ocr-secrets.env
unset KEY
```

`read -s` hides the paste and `$KEY` is expanded at runtime so the key
does not land in shell history. Verify with
`sudo ls -l /root/ocr-secrets.env` (should show `-rw------- root root`).

**Container launch must include:**
```
docker run --env-file /root/ocr-secrets.env <...other args...>
```

**Rejected alternatives:**
- `config/config.yaml` in the repo: leaks on `git push`.
- Any path under `/home/ubuntu/Vault/`: Syncthing-propagated in plaintext.
- `ENV` baked into a custom image: exposed via `docker inspect` / image
  pulls; not a secret.
- In-container writable-layer file only: lost on `docker rm` +
  recreate (image update, compose change). Not durable.
- Exporting in an interactive shell: fine for a one-off POC, not a
  durable deployment.

**Convention:** any future secret (other vendor keys, etc.) follows the
same pattern — one line per secret in `/root/ocr-secrets.env`, read
inside the container as an env var.

## Logging: rotating file in logs/

**Decision:** Pipeline logs to `logs/pipeline.log` with rotation
(size-based, keep N files). Also surfaced in journald via a host systemd
service (`StandardOutput=journal`, `StandardError=journal` on
`ocr-pipeline.service`).

**Rationale:** A rotating file is easy to tail and grep; journald gives
the systemd view (`journalctl -u ocr-pipeline`). Both is cheap. An
Obsidian-based log was considered and rejected (log noise in the vault,
not searchable in a useful way). Install and schedule: `docs/deployment.md`.
# Testing Protocol

Step-by-step tests for the OCR pipeline. Each test uses your phone
(OpenScan → Syncthing) and checks the result in Obsidian. Do them in
order — each builds on the last.

## Setup

Before starting, make sure Processing/ is clean:

```bash
# Inside the container
ls "/home/ubuntu/Vault/Resources/Homework and Class Notes/00 Pipeline/Processing/"*.pdf 2>/dev/null
```

Should be empty. If not, run `python3 /home/ubuntu/workspaces/OCR_pipeline/run.py`
to process them first.

Watch logs live on the Pi host:

```bash
sudo journalctl -u ocr-pipeline -f
```

---

## Test 1: Basic routing (alias hit)

**What to scan:** One homework page. Write "Thermo" clearly at the top.

**What to watch:** Journald — you should see the watcher detect the file,
then Stages 0→5 run.

- [ ] A per-scan folder appears in `Thermodynamics & Lab/` named
      `{date} {title}` (e.g. `2026-09-03 Thermo/`), containing the `.md`
      note + any diagram PNGs loose next to it
- [ ] The `.md` inside is named to match the folder
      (e.g. `2026-09-03 Thermo.md`)
- [ ] Frontmatter has `class: "Thermodynamics & Lab"`, `needs_review: false`
- [ ] `tags: [needs-tagging]`
- [ ] `date:` matches the day you scanned (from OpenScan filename)
- [ ] OCR text is readable (handwriting → markdown + LaTeX)
- [ ] If the page had a diagram, it shows inline as a wikilink embed
      (`![[...-diag0.png]]` renders the image, not a broken link)
- [ ] `Sorting Guide.md` exists in `Homework and Class Notes/` and lists "thermo" under Thermodynamics & Lab

---

## Test 2: Unknown class (Unsorted + needs_review)

**What to scan:** One page with a title that's NOT a class — write "Random doodles" at the top.

- [ ] A per-scan folder appears in `00 Pipeline/Unsorted/` (same layout
      as class folders: `{date} {title}/` containing the .md + any PNGs)
- [ ] Frontmatter has `class: "Unsorted"`, `needs_review: true`
- [ ] LLM fallback logged in journald: `stage3b: LLM said NONE for 'random doodles'`
- [ ] No note in any class folder for this scan

---

## Test 3: LLM fallback (miss → AI resolves)

**What to scan:** One page titled "ME 301 homework 4" (or any title that's
clearly a class but not in your alias list).

- [ ] Note appears in `Thermodynamics & Lab/` (LLM recognized ME 301 = thermo)
- [ ] Frontmatter has `class: "Thermodynamics & Lab"`, `needs_review: false`
- [ ] Journald shows: `stage3b: MISS ... LLM HIT ... -> Thermodynamics & Lab`

---

## Test 4: Dedup (re-sync same file)

A new photo of the same page has a different SHA-256 (different pixels),
so true dedup only catches identical files (Syncthing re-sync). To test:

**What to do:** Copy a PDF from Archive back into the inbox:

```bash
# Inside the container
cp "/home/ubuntu/Vault/Resources/Homework and Class Notes/00 Pipeline/Archive/2026-08-31-OpenScan-*.pdf" \
   /mnt/openscan-inbox/
```

- [ ] Journald shows: `stage0: dedup hit, removing source`
- [ ] No new note created
- [ ] The copied file is deleted from the inbox
- [ ] No change to the existing note

---

## Test 5: Multi-page grouping (same title, within 30 min)

**What to scan:** Two separate single-page scans, both titled "Circuits",
taken within 30 minutes of each other.

- [ ] ONE note created (not two) — a single per-scan folder in `Circuits/`
- [ ] Note has `## Page 1` and `## Page 2` sections
- [ ] Each section has the correct page's content
- [ ] `pages: 2` in frontmatter

---

## Test 6: Multi-page PDF (single file, multiple pages)

**What to scan:** Use OpenScan's multi-page mode — scan 2-3 pages into one
PDF. Title the first page "Circuits lab".

- [ ] ONE note created in `Circuits/` (one per-scan folder)
- [ ] Note has `## Page 1`, `## Page 2` (and `## Page 3` if 3 pages)
- [ ] `pages: 3` in frontmatter (or however many you scanned)
- [ ] All pages' content present in order

---

## Test 7: File watcher (instant trigger)

**What to scan:** One page titled "Materials" — but watch the timing.

- [ ] Journald shows `watcher: event /mnt/openscan-inbox/.../OpenScan *.pdf`
      within seconds of Syncthing finishing
- [ ] After ~10s debounce: `watcher: debounce expired, triggering pipeline run`
- [ ] Note appears in `Structures and Properties of Materials/`
- [ ] Total time from scan to note: under 1 minute

---

## Test 8: Resort command

**Setup:** First, make sure there's at least one note in Unsorted (from
Test 2). Then add "doodles" to the Materials line in the vault's
`Classes.md`:

```markdown
Structures and Properties of Materials: structures, materials, doodles
```

**What to run:**

```bash
# Inside the container
python3 /home/ubuntu/workspaces/OCR_pipeline/run.py --resort
```

- [ ] The "Random doodles" per-scan folder (with .md + any diagrams)
      moves from `Unsorted/` to `Structures and Properties of Materials/`
- [ ] Frontmatter updated: `class: "Structures and Properties of Materials"`,
      `needs_review: false`
- [ ] Per-scan folder no longer in `Unsorted/`
- [ ] `Sorting Guide.md` updated to show "doodles" under Materials

**Cleanup:** Remove the "doodles" keyword from `Classes.md` after
testing (or keep it if you want).

---

## Test 9: Cost tracking

**What to check:**

```bash
cat /home/ubuntu/workspaces/OCR_pipeline/logs/spend.csv
```

- [ ] One row per page processed
- [ ] Columns: `timestamp, scan_hash, class, pages, estimated_cost_usd`
- [ ] `class` matches where the note landed
- [ ] `estimated_cost_usd` = pages × 0.0040
- [ ] Total cost is reasonable (~$0.04 for the 10 scans above)

---

## Test 10: Crash recovery

**What to do:**

1. Scan a page (let it start processing)
2. Mid-run, kill the service: `sudo systemctl stop ocr-pipeline` (on the Pi host)
3. Restart: `sudo systemctl start ocr-pipeline`

- [ ] The PDF is still in `Processing/` (not lost)
- [ ] On restart, the pipeline picks it up and processes it
- [ ] Note appears correctly
- [ ] No duplicate note (Stage 5 archives before the note is "final")

---

## Quick reference: journald commands

```bash
# Live view
sudo journalctl -u ocr-pipeline -f

# Search for errors
sudo journalctl -u ocr-pipeline --since "1 hour ago" | grep -i error

# Count processed notes today
sudo journalctl -u ocr-pipeline --since today | grep "pipeline complete" | wc -l
```

---

## After testing: add real keywords

Once you've done a few real homework sessions, look at what titles you
actually write and add them as keywords. Edit the vault's `Classes.md`
(you can do it from your phone), then run `python3 run.py --resort` to
re-sort anything in Unsorted. The Sorting Guide updates automatically.
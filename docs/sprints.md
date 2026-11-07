# Sprints

## Sprint 1 — Minimum working pipeline (this weekend)

Goal: drop a scan in `Inbox/`, get a sorted markdown note (with inline
diagrams) in the vault.

- [x] Dockerfile at `OCR_pipeline/Dockerfile` (`FROM
      openchamber-pi4-sbc-02:latest`, adds pip, poppler-utils,
      mistralai, pdf2image, Pillow, PyYAML, watchdog, requests,
      google-genai). Build on host with
      `docker build -t openchamber-pi4-sbc-02-ocr:latest .`. See
      `docs/decisions.md` "Deployment: single container".
      **Done 2026-08-31** — image built, all imports verified.
- [x] Host-side secrets: create `/root/ocr-secrets.env` (root:root, 600)
      containing `MISTRAL_API_KEY=...` per `docs/decisions.md`. Confirm
      `sudo ls -l /root/ocr-secrets.env` shows `-rw------- root root`
      before launching the container. Add `GEMINI_API_KEY=...` when the
      Gemini key is obtained (Sprint 2; same file).
      **Done 2026-08-31** — env-file created, key loaded via `--env-file`.
- [x] Recreate the container from the new image with
      `docker run --env-file /root/ocr-secrets.env <...mounts...>`
      (see `docs/decisions.md` "Deployment: single container" for the
      full invocation). The key is read inside the container from
      `$MISTRAL_API_KEY` only — never from `config.yaml`, never from a
      vault path.
      **Done 2026-08-31** — container running, `$MISTRAL_API_KEY` verified.
- [x] Vault folder scaffold under `Resources/Homework and Class Notes/`:
      `00 Inbox/`, `00 Pipeline/Processing/groups/`, `00 Pipeline/Unsorted/`,
      `00 Pipeline/Archive/`, `{Class}/` (+ `lab/` where the class has a lab
      component). Per-scan folders are created on the fly by Stage 4.
      **Done 2026-09-01** — classes: Thermodynamics & Lab, Structures and
      Properties of Materials (+`lab/`), Circuits (+`lab/`). Pipeline dirs
      grouped under `00 Pipeline/`; Inbox at `00 Inbox/` (syncthing target).
      `classes.yaml` updated with real classes + starter aliases. `logs/`
      created in repo. `.gitkeep` in each empty dir for Syncthing stability.
- [x] `config/classes.yaml` with 1-2 placeholder classes + aliases, plus
      `grouping_window_minutes` and `archive_retention_days` settings.
      **Done 2026-08-31** — exists with Thermodynamics + Statics placeholders.
- [x] Stage 0: watch `Inbox/`, syncthing-stable-file detection, SHA-256
      dedup, move to `Processing/`. Input is `.pdf` (OpenScan exports
      PDF, not JPG). No HEIC/JPG path.
      **Done 2026-09-01** — `src/stage0.py`, `src/config.py`,
      `src/logging_setup.py`, `run.py`. 10 unit tests pass. Verified
      against the real `/mnt/openscan-inbox` (3 PDFs moved into
      `00 Pipeline/Processing/`, inbox cleared, idempotent re-run).
      `--dry-run` and `--stage0-only` flags. Cross-device move handled
      via `shutil.move`. Paths configurable in `classes.yaml` `paths:`
      block, overridable via env vars (`OCR_VAULT_ROOT`, etc.).
- [x] Stage 1: `OCREngine` interface + `MistralOCREngine` impl.
      Primary path: upload PDF to Mistral (`files.upload` purpose=ocr
      + `files.get_signed_url` + `ocr.process` with `document_url`),
      `include_image_base64=True`. No client-side rasterization —
      Mistral's server rasterizer produces better results (POC
      2026-08-31). `pdf2image` remains as a fallback only.
      **POC done 2026-08-31** — API call verified, response structure
      documented in `docs/pipeline.md` Stage 1. Impl not yet written.
      **Done 2026-09-01** — `src/stage1.py` (interface + Mistral impl +
      retry/backoff + file cleanup), `src/stage1_runner.py` (loop over
      Processing/, leave failed files for retry). 7 unit tests pass.
      Smoke test against real API: 3/3 PDFs OCR'd, titles extracted
      (`"Hw test thermodynamics"`, `"Ideas for CL"`, bible reading
      table). Thermo page returned 1 diagram image (base64 + bbox).
      Retried once on a transient 404, succeeded on attempt 2.
- [x] Stage 2: date/capture_time extraction — parse OpenScan filename
      timestamp first (`OpenScan YYYYMMDD HHMMSSffffff.pdf`), fall back
      to PDF metadata, then file ctime with `needs_review: true`. No
      EXIF path.
      **Filename regex confirmed 2026-08-31** — pattern
      `^OpenScan (\d{8}) (\d{6})(\d{6})\.pdf$` matches all inbox PDFs.
      PDF metadata has no creation timestamp (verified via `pdfinfo`).
      **Done 2026-09-01** — `src/stage2.py`. Priority chain: filename →
      `pdfinfo` (subprocess, no pip dep) → ctime (sets needs_review=True).
      `NoteMeta` dataclass holds date + capture_time + date_source +
      needs_review. 8 unit tests pass. Smoke test: 3/3 real PDFs got
      `date=2026-08-31` from filename, no review needed. Original
      filename threaded from Stage 0 via `{sha256}.name` sidecar file
      in Processing/ (survives crashes + re-runs).
- [x] Stage 3a: multi-page grouping (title+time+continuation-marker
      heuristic, group file in `Processing/groups/`).
      **Done 2026-09-01** — `src/stage3.py`. Two paths: multi-page PDFs
      (num_pages > 1) form their own single-file group; single-page PDFs
      grouped cross-file by normalized title + capture_time within
      `grouping_window_minutes` (config). Continuation markers (`cont`,
      `continued`, `page N`, `pg N`, `p N`, trailing `...`) stripped
      during normalization. No-marker same-title pages within the window
      treated as continuations. Groups persisted to
      `Processing/groups/{group_id}.json` (clears stale files each run).
      16 unit tests pass. Smoke test: 3 real PDFs (all unique titles) →
      3 separate single-page groups, JSON persisted correctly.
- [x] Stage 3b: alias-map classification -> class folder or `Unsorted/`.
      **Done 2026-09-01** — `classify()` + `classify_groups()` in
      `src/stage3.py`. Two-pass matching: exact match first (any alias
      length), then substring for short aliases (<= 12 chars) to handle
      "ME 301 - Lecture 5". First match wins within each pass (YAML
      order). On miss: class_name="Unsorted", folder=Unsorted/,
      needs_review=True (set by caller in Stage 4). 16 unit tests pass.
      Smoke test: 1/3 real groups hit (`hw test thermodynamics` ->
      Thermodynamics & Lab via substring alias `thermo`), 2 misses ->
      Unsorted (`ideas for cl`, bible reading table).
- [x] Stage 4: write note + inline diagram attachments + frontmatter
      (`tags: [needs-tagging]`). Multi-page notes get `## Page N` sections.
      **Done 2026-09-01, updated 2026-09-03** — `src/stage4.py`. Writes `.md`
      notes with frontmatter (class, date, source_scan, scan_hash,
      ocr_confidence, tags, needs_review, pages). Each scan gets its own
      folder `{Class}/{date} {slug}/` containing the .md + diagram PNGs
      loose alongside it. Diagrams decoded from base64, saved as PNG next
      to the .md, image placeholders rewritten to Obsidian wikilinks
      `![[{diag-name}]]` (standard markdown `![](path with spaces)` is
      unreliable in Obsidian). Multi-page groups get `## Page N` sections
      in capture-time order. Folder collision handling (`-2`, `-3`).
      `needs_review: true` for Unsorted misses. 17 unit tests pass.
      Smoke test: 3 notes written to the vault — thermo note in
      `Thermodynamics & Lab/` with 1 diagram, 2 notes in `Unsorted/`.
      LaTeX, frontmatter, and inline image link all verified.
- [x] Stage 5: archive original scans + raw OCR JSON; append to
      `logs/spend.csv`; remove from `Processing/`.
      **Done 2026-09-01** — `src/stage5.py`. Moves PDFs from Processing/
      to Archive/ as `{date}-{slug}.pdf`. Saves raw OCR JSON to
      Archive/ as `{date}-{slug}.ocr.json`. Appends to `logs/spend.csv`
      (timestamp, scan_hash, class, pages, cost = pages × $0.004).
      Removes `.name` sidecar + group JSON from Processing/. 9 unit
      tests pass. Smoke test: 3 scans archived, 3 OCR JSONs saved,
      3 spend rows logged, Processing/ cleared of PDFs.
- [x] A `run` command that processes everything in `Inbox/` once (watcher
      can come later; a manual/cron run is fine for a weekend).
      **Done 2026-09-01** — `run.py` runs Stages 0→5 in one pass.
      `--dry-run` and `--stage0-only` flags.
- [x] Rotating `logs/pipeline.log` + journald via systemd.
      **Done 2026-09-01** — rotating file log in `src/logging_setup.py`
      (1MB rotation, 5 backups). Host systemd units
      (`ocr-pipeline.service` + `.timer`) run `docker exec ... run.py`
      every 10 minutes; stdout/stderr captured in journald
      (`journalctl -u ocr-pipeline`). See `docs/deployment.md`.
- [ ] Smoke test: scan one thermo page, one unknown page, a multi-page
      PDF, and two same-title single-page PDFs within 30 min; verify
      routing + grouping + multi-page assembly.
- [x] README quick-start section.
      **Done 2026-09-01** — updated with full-pipeline instructions,
      flags, cost, and test command.

**Sprint 1 explicitly defers:** the file watcher (use a manual/cron
`run`), auto-generated topic tags, and the LLM sorting fallback.

## Sprint 2 — LLM fallback + file watcher + usability (next weekend)

Revised per user request 2026-09-01.

- [x] LLM fallback for class sorting when the alias map misses. Send the
      detected title + known class names to a cheap text model (Gemini
      free tier or future GLM-5.3 via Z.ai) and ask "which class, or NONE?".
      Only fires on alias-map miss, so cost is minimal. Needs
      `GEMINI_API_KEY` added to `/root/ocr-secrets.env`.
      **Done 2026-09-01** — `src/llm_fallback.py`. Uses Mistral chat
      (`mistral-small-latest`) instead of Gemini — same SDK, same API
      key, no new signup. temperature=0, max_tokens=20, strict system
      prompt ("respond with ONLY the class name or NONE"). Response is
      matched back to actual ClassEntry entries (case-insensitive,
      substring) — hallucinated class names fail safe to NONE. LLM
      errors degrade gracefully to Unsorted. Wired into Stage 3b as
      pass 3 (only fires after alias exact + substring miss). 12 unit
      tests pass. Smoke test: correctly routes "thermo hw 3" → Thermo,
      "circuits lab 2" → Circuits, "materials properties lecture" →
      Materials; correctly returns NONE for "Ideas for CL" and bible
      reading table. Cost: ~$0.001 per miss (50 tokens, $0.10/Mtok).
- [x] File watcher (inotify) so `run` isn't manual. `watchdog` is already
      installed in the image. Replaces the 10-minute systemd timer with
      instant triggering when Syncthing drops a new file.
      **Done 2026-09-01** — `src/watcher.py`. Uses `watchdog` (inotify on
      Linux). Debounced 10s to coalesce Syncthing's burst of create/write/
      close events into one pipeline run. Watches `/mnt/openscan-inbox`
      recursively. Skips hidden/temp/`.sync` files. `--watch` flag on
      `run.py` runs the pipeline once then blocks forever watching.
      Manage with systemd (`Restart=always`). To switch from the 10-min
      timer to the watcher, update the systemd service to run
      `python3 run.py --watch` instead of `python3 run.py`. 5 unit tests
      pass (debounce, ignores non-PDF/directories/hidden, reset-on-new-event).
- [x] Auto-generated README showing the alias map as a human-readable
      "keywords -> class" reference. Regenerated from `classes.yaml` on
      each run (or on demand). So you can look at one place and see:
      "thermo, thermodynamics, me 301, me301 -> Thermodynamics & Lab".
      Editing `classes.yaml` and re-running updates the README.
      **Done 2026-09-01** — `src/alias_readme.py`. Writes
      `Sorting Guide.md` to the vault's `Homework and Class Notes/`
      folder. Shows each class, its folder, and all keywords as a
      bulleted list. Includes explanation of matching (exact → substring
      → AI fallback) and instructions for adding keywords/classes.
      Regenerated on every pipeline run. 7 unit tests pass.
- [ ] Grouping heuristic tuning — low risk: review the multi-page grouping
      window (currently 30 min) and continuation markers against real
      homework once a few weeks of scans are in. Adjust
      `grouping_window_minutes` in `classes.yaml` if needed. No code
      change unless a marker is missing.
- [x] Watcher auto-updates on Classes.md edits: watch Classes.md
      (exact filename only, separate debounce + callback from PDF
      triggers) and run a light action on change — reload classes,
      create new folders, regenerate the Sorting Guide. A 60s
      content-hash poll backstops inotify misses (bind-mount). Also
      fixed: the old `_EventHandler` never subclassed watchdog's
      `FileSystemEventHandler`, so the observer thread crashed on its
      first dispatch — inotify had been dead since Sprint 2, masked by
      the poll backstop; and the watch callback now reloads config
      per-run instead of reusing the startup config (stale-class bug).
      **Done 2026-09-04** — `src/watcher.py` (Handler subclass,
      classes timer, hash poll), `run.py` (classes_changed callback,
      per-run config reload). 6 new watcher tests; suite at 144.
      Live-verified: adding a class line creates the folder in ~10s;
      removing it (rename-style write, no inotify) is caught by the
      60s poll. Also added `prune_class_folders`: a removed class's
      folder is deleted only if completely empty (notes are never
      touched — warning logged, manual cleanup). Wired into startup
      and both watcher callbacks. 7 new prune tests; suite at 150.
- [x] Classes editable from the vault: move the class list out of
      `classes.yaml` into `Classes.md` in the vault (one
      `Class Name: keyword, keyword` line per class), auto-create class
      folders on each run, keep `Sorting Guide.md` as generated output.
      **Done 2026-09-04** — `src/classes_md.py` parses the file
      (prose/headings/code fences ignored, duplicate classes merged,
      keywords lowercased). `load_config` prefers Classes.md, falls
      back to the yaml `classes:` section. `run.py` calls
      `ensure_class_folders()` so a new class line gets its folder on
      the next run. `alias_readme.py` regenerated guide now points at
      Classes.md. Starter Classes.md created in the vault with the
      three existing classes migrated. 12 new unit tests; suite at 136.
- [x] `resort Unsorted/` command: after editing `classes.yaml`, re-sort
      everything in `Unsorted/` against the new aliases (and the new LLM
      fallback).
      **Done 2026-09-01** — `src/resort.py`. `python3 run.py --resort`
      reads each note in Unsorted/, extracts the title (first
      non-frontmatter line), re-classifies via alias map + LLM fallback.
      On a hit: moves the note's per-scan folder (containing .md +
      diagrams) to the class folder, updates frontmatter (class +
      needs_review=false). On a miss: leaves the note in Unsorted.
      Folder collision handling (`-2`, `-3`). Ignores `Sorting Guide.md`.
      10 unit tests pass.
      Smoke test: 2 real Unsorted notes (Ideas for CL, bible reading)
      correctly stay — LLM says NONE, they're not homework.
- [x] Dataview-friendly frontmatter (verify Obsidian reads it correctly).
      **Done 2026-09-01** — frontmatter is valid YAML (verified with
      PyYAML). Class name is now quoted (`class: "Thermodynamics & Lab"`)
      so the `&` doesn't cause YAML/Dataview parsing issues. `tags` is a
      YAML array, `needs_review` is a boolean, `date` is a date string —
      all Dataview-compatible. Dataview plugin not installed in this
      vault yet; install it in Obsidian to query notes by class/date.

**Sprint 2 explicitly defers:** auto-generated topic tags (the
`[needs-tagging]` placeholder stays), and the LLM spelling cleanup pass
(skipped — risk of corrupting equations/meaning exceeds the value; alias
map already handles misspellings by adding them as aliases).

## Sprint 3 — Outage resilience & self-healing (proposed)

Motivated by the 2026-09-03 Mistral OCR outage: a scan sat stuck in
`Processing/` for 30+ min because the watcher only triggers on *new*
inbox PDFs, never re-attempting failed files. The 502 "invalid response
from upstream server" errors also exposed aggressive retry timing, a
leaked uploaded file, and no alerting. This sprint hardens all four.

Implementation order: Item 1 (foundational) → 2 → 4 → 3 (most complex,
least urgent). Items 2-4 are independent of each other; Item 4 should
wire into the failure paths of 1-3.

### Item 1 — Watcher auto-retries stuck files in Processing/

The core gap: `--watch` only fires on new inbox PDFs. Files left in
`Processing/` after a failed OCR run sit indefinitely until you scan
something new or run `python3 run.py` manually.

- [ ] Extend `src/watcher.py` `_poll_loop` to also scan
      `cfg.paths.processing` for stuck `.pdf` files. Add a
      `_stuck_pdfs(cfg)` helper (same pattern as
      `stage1_runner.process_pending:36`).
- [ ] Add `retry_cooldown_seconds` (default **300s**) to `Watcher`,
      tracked via `_last_retry` timestamp. If stuck files exist and
      cooldown has elapsed, arm the trigger; otherwise skip.
- [ ] Add `_pipeline_running` flag (set in `_trigger()` before
      `callback()`, cleared after) so the poll loop doesn't arm while a
      run is in flight. Use the existing `self._lock` or an `Event`.
- [ ] Log: `watcher: poll found N stuck PDF(s) in Processing/ (last
      retry Xs ago) — arming trigger`
- [ ] Tests: `tests/test_watcher.py` — arms when Processing/ has PDFs
      + cooldown elapsed; doesn't arm within cooldown; doesn't arm while
      pipeline running. Use fake callback + `poll_seconds=0`.

**Risk: Medium.** Double-trigger with an in-flight run is the main
concern; mitigated by `_pipeline_running` flag. Cooldown-too-short during
long outages = log noise (acceptable; revisit with exponential cooldown
if it gets annoying).

### Item 2 — Exponential backoff with jitter for OCR/LLM retries

Current backoff (2s/4s/6s linear) exhausts all 3 retries within ~12s,
guaranteeing failure during a multi-minute outage. The leaked file from
today (orphaned upload `ce6e32cf`) also showed `_cleanup` never ran
because `ocr.process` hung — Item 3 addresses the hang, this item
addresses the retry timing.

- [ ] `src/stage1.py` `MistralOCREngine.process`: change backoff to
      exponential — **5s, 30s, 120s** with ±25% jitter. Total worst case
      ~155s before `OCRError`, vs. current ~12s.
      `RETRY_BACKOFF_BASE=5.0`, `RETRY_BACKOFF_MAX=120.0`,
      `wait = min(base * 2**(attempt-1), max) * jitter(0.75..1.25)`.
- [ ] Add `_is_retryable(exc)`: retry on 5xx / timeout / connection
      errors; do **not** retry on 400/401/403 (bad request / auth).
- [ ] `src/llm_fallback.py` `MistralLLMClassifier.classify`: same
      treatment, lower caps since it's a cheaper call — **2s, 10s, 30s**.
- [ ] Tests: extend `test_stage1.py` — verify increasing backoff
      intervals via mocked `time.sleep`; verify non-retryable errors
      (mock 401) raise immediately without retrying.

**Risk: Low-Medium.** Longer retry window blocks the pipeline thread for
up to ~3 min/file, but that's fine because Item 1's `_pipeline_running`
flag prevents concurrent runs and the 5-min cooldown fits the 3-min
window. Jitter makes test timing non-deterministic — inject a
`sleep_func`/`jitter_func` for tests.

### Item 3 — Timeout + guaranteed cleanup of uploaded Mistral files

If `ocr.process` hangs (we saw 75s+ today), `_cleanup` in the `finally`
runs on timeout but `files.delete` can *also* 502 (we saw it today),
leaking uploaded files on Mistral's file store.

- [ ] Add `request_timeout` (default **180s**) to `MistralOCREngine`.
      Prefer the `mistralai` SDK's native `timeout` param if it exists
      (clean abort via `TimeoutError`); fall back to a
      `threading.Timer`-based timeout if not.
- [ ] Make `_cleanup` retry: 2 attempts with 5s backoff. On total
      failure, log at **ERROR** level (so Item 4 can alert) but don't
      raise — the OCR result is still valid.
- [ ] Track `self._pending_uploads: list[str]` — appended on upload,
      removed on successful cleanup. On each new `process()` call, sweep
      any orphans. Cap at last 10 entries (self-cleaning).
- [ ] Tests: `_cleanup` retries on failure (mock `files.delete` fail×2
      then succeed → 3 calls); logs ERROR after all retries fail;
      timeout raises `OCRError` (mock `ocr.process` to sleep, short
      timeout).

**Risk: Medium.** Timeout implementation is SDK-dependent (check for
native `timeout` param first). `threading.Timer` doesn't truly cancel
the HTTP call — the abandoned response is GC'd, file still cleaned up.
Timeout should be per-attempt (inside `_process_once`), not per-file —
the retry loop in `process()` wraps it correctly. Most code-complex
item; least urgent (orphaned uploads are a minor resource leak, not a
correctness issue).

### Item 4 — Alerting on exhausted retries

Failed OCR currently produces a log line that's easy to miss for hours.
Add a minimal webhook alerter (recommended target: `ntfy.sh` — free, no
signup, phone push notifications, perfect for a Pi hobby project).

- [ ] New `src/alerter.py`: `Alerter` `Protocol` (one method,
      `alert(title, message, severity)`) + `WebhookAlerter` impl using
      `urllib.request` (stdlib, no new pip dep).
- [ ] **Safety property:** `WebhookAlerter.alert()` wraps everything in
      `try/except`, logs a warning on failure, and **never raises**. The
      pipeline must not fail because the alerter failed.
- [ ] **Alert dedup/cooldown:** don't send the same alert (same file +
      same error class) more than once per 30 min. Track
      `_last_alert: dict[str, float]`.
- [ ] `src/stage1_runner.py`: on `except OCRError`, call the alerter with
      filename + error + "will auto-retry in 5 min".
- [ ] `config/classes.yaml`: add optional `alerts:` section
      (`webhook_url`, `severity_threshold`). Empty/absent = disabled
      (no-op, zero impact if unconfigured).
- [ ] `src/config.py`: parse `alerts:` into `Config` (typed
      `AlertConfig` or `dict`). Support env var override
      `OCR_ALERT_WEBHOOK_URL` so the URL goes in
      `/root/ocr-secrets.env`, not the committed YAML.
- [ ] Tests: `tests/test_alerter.py` — mock `urllib.request.urlopen`,
      verify never-raises on HTTP failure, verify dedup cooldown logic.
      `test_stage1.py`: verify `process_pending` calls alerter on
      failure (FakeEngine + FakeAlerter).

**Risk: Low.** The "never raise" property makes this safe by
construction. Alert spam during sustained outages is mitigated by the
30-min dedup cooldown. New module + config parsing adds surface area but
the interface is minimal (one method) and tested with fakes in the
existing codebase pattern.

### Item 4 alert content (reference)

```
[OCR Pipeline] OCR failed after 3 retries
File: d2b2b8cb1262...pdf (in Processing/)
Error: Status 502 — An invalid response was received from the upstream server
Action: Will auto-retry in 5 minutes. Manual retry: python3 run.py
```

### What this sprint does NOT change

- The pipeline's stage architecture (0→5) — clean and correct.
- The alias-map + LLM fallback classification — working fine.
- The `mistralai` SDK version — unless Item 3 needs a timeout feature
  from a newer version.
- The systemd/deployment setup — `--watch` is the right model; we're
  just making it smarter.

### Estimated effort

~170 lines + 4 test files (~3-4 hours total).
- Item 1: ~40 lines + tests (~1h) — do first, highest value.
- Item 2: ~20 lines + tests (~30min).
- Item 4: ~60 lines + tests (~1h) — before Item 3 for visibility.
- Item 3: ~50 lines + tests (~1h) — most complex, least urgent.

## Backlog (maybe, if painful)

- A/B test a second `OCREngine` (Gemini 2.5 Flash) on real pages to compare
  handwriting/LaTeX quality. The interface makes this a drop-in swap.
- Tuning the multi-page grouping heuristic once you see real miss rates
  (window size, which markers actually get used, same-title no-marker
  handling).
- Multi-page assembly: group same-day same-class pages into a session note.
- A simple web/TUI status page on the Pi showing processed/failed/Unsorted
  counts.
- Exponential cooldown on Item 1's auto-retry (5→10→20→… capped at 30 min)
  if log noise during long outages becomes annoying.
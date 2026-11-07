# Deployment — scheduling and logging

The pipeline runs inside the OpenChamber container on the Pi 4 host.
Scheduling and log capture happen on the **host** via systemd: a timer
fires every 10 minutes, a oneshot service runs `docker exec` into the
container, and stdout/stderr land in journald.

No Docker rebuild is required. The container's `--env-file
/root/ocr-secrets.env` (set at `docker run` time) is already active,
so `docker exec` inherits `MISTRAL_API_KEY` and other secrets.

## Prerequisites

- OpenChamber container running (confirm name with `docker ps`; currently
  `openchamber-pi4-sbc-02`).
- Pipeline entry point:
  `python3 /home/ubuntu/workspaces/OCR_pipeline/run.py`

## Systemd units

Unit files live in `deploy/systemd/` in this repo. Install them on the Pi
host:

```bash
sudo cp deploy/systemd/ocr-pipeline.service deploy/systemd/ocr-pipeline.timer \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ocr-pipeline.timer
```

### `ocr-pipeline.service`

- **Type:** `oneshot` — the pipeline runs once and exits (not a daemon).
- **ExecStart:** `docker exec <container> python3 .../run.py`
- **Logging:** `StandardOutput=journal` and `StandardError=journal` so
  all pipeline output is captured by journald.
- **Timeout:** `TimeoutStartSec=300` — kill the run if OCR stalls for
  more than 5 minutes.

### `ocr-pipeline.timer`

- **Schedule:** `OnCalendar=*:0/10` — every 10 minutes (`:00`, `:10`,
  `:20`, …).
- **No overlap:** if a run is still active when the timer fires, systemd
  skips that trigger rather than starting a second instance.

## Querying logs

```bash
journalctl -u ocr-pipeline          # all runs
journalctl -u ocr-pipeline -f         # follow live
journalctl -u ocr-pipeline -e         # jump to latest entries
systemctl status ocr-pipeline.timer   # timer status + next trigger
```

Pipeline output also goes to the rotating file log at
`logs/pipeline.log` inside the repo (see `src/logging_setup.py` and
`docs/decisions.md` "Logging").

## Manual run

```bash
# Inside the container (interactive)
python3 /home/ubuntu/workspaces/OCR_pipeline/run.py

# From the host (same as the systemd service)
sudo systemctl start ocr-pipeline
```

## Verify after install

```bash
systemctl status ocr-pipeline.timer
sudo systemctl start ocr-pipeline
journalctl -u ocr-pipeline -e
```

A successful run ends with a line like:

```
ocr_pipeline.run INFO: pipeline complete: N note(s) written, M scan(s) archived
```

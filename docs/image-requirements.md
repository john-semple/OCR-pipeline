# Image Requirements — what to add to the OpenChamber Docker image

This is the canonical list of everything the OCR pipeline needs baked
into the OpenChamber container image. Edit the original OpenChamber
Dockerfile to add these, then rebuild and recreate the container.

## System packages (apt-get install)

| Package | Why |
|---|---|
| `python3-pip` | pip is not in the base OpenChamber image |
| `poppler-utils` | Required by `pdf2image` (PDF -> image conversion). Provides `pdftoppm` etc. |
| `libjpeg-dev` | Pillow build dep (JPEG handling) |
| `zlib1g-dev` | Pillow build dep (PNG handling) |

```Dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-pip \
        poppler-utils \
        libjpeg-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
```

## Python packages (pip install)

| Package | Why | Sprint |
|---|---|---|
| `mistralai>=1.0.0` | Mistral OCR API client | 1 |
| `pdf2image>=1.16.0` | PDF -> image fallback (Mistral PDF-direct path is primary; pdf2image kept for edge cases like corrupt PDFs) | 1 |
| `Pillow>=10.0.0` | Image handling (fallback rasterization path) | 1 |
| `PyYAML>=6.0.0` | Parse `config/classes.yaml` | 1 |
| `watchdog>=3.0.0` | Inbox file watcher (Sprint 1 defers to manual run; included now so Sprint 2 needs no rebuild) | 1-2 |
| `requests>=2.31.0` | HTTP backup / misc | 1 |
| `google-genai>=1.0.0` | Gemini SDK — Sprint 2 LLM sorting fallback + spelling cleanup; backlog A/B test as second OCREngine. Included now to avoid a rebuild next weekend. | 2 / backlog |

```Dockerfile
RUN pip3 install --no-cache-dir --break-system-packages \
        mistralai>=1.0.0 \
        pdf2image>=1.16.0 \
        Pillow>=10.0.0 \
        PyYAML>=6.0.0 \
        watchdog>=3.0.0 \
        requests>=2.31.0 \
        google-genai>=1.0.0
```

> If `--break-system-packages` is not needed on your base (older pip),
> drop it. It's required on PEP 668 Debian/Ubuntu bases that mark the
> system Python as externally-managed.

## Environment variables (via --env-file, NOT baked into image)

Supplied at `docker run` time via `--env-file /root/ocr-secrets.env`
(host-side, root:root, chmod 600). Never bake secrets into an image.

| Var | When | Source |
|---|---|---|
| `MISTRAL_API_KEY` | Sprint 1 | console.mistral.ai -> API Keys |
| `GEMINI_API_KEY` | Sprint 2 | aistudio.google.com -> API key (add to the same env file when obtained) |

## Verification (optional RUN step in Dockerfile)

```Dockerfile
RUN python3 -c "from mistralai import Mistral; print('mistralai ok')" \
 && python3 -c "from pdf2image import convert_from_path; print('pdf2image ok')" \
 && python3 -c "from PIL import Image; print('Pillow ok')" \
 && python3 -c "import yaml; print('PyYAML ok')" \
 && python3 -c "import watchdog; print('watchdog ok')" \
 && python3 -c "from google import genai; print('google-genai ok')" \
 && pdftoppm -v
```

If all seven lines print, the image is ready.

## Bind mounts the container needs at run time

| Container path | Host path | Purpose |
|---|---|---|
| `/home/ubuntu/Vault` | (vault) | Obsidian vault — notes land here, Syncthing-synced |
| `/mnt/openscan-inbox` | (openscan inbox) | OpenScan PDF drop via Syncthing |
| `/home/ubuntu/workspaces` | (workspaces) | Repo + this file persist on the host; editable via OpenChamber |

All three are already bind-mounted in the current container. Recreate
with the same mounts plus `--env-file /root/ocr-secrets.env`.

## Recreate command (host)

```bash
docker run --env-file /root/ocr-secrets.env \
  -v <vault>:/home/ubuntu/Vault \
  -v <openscan-inbox>:/mnt/openscan-inbox \
  -v <workspaces>:/home/ubuntu/workspaces \
  <...other OpenChamber args...> \
  openchamber-pi4-sbc-02-ocr:latest
```

## Not included (intentional)

- `pillow-heif` — OpenScan outputs PDF, not HEIC. Not needed.
- Z.ai / `openai` SDK — GLM-5.3 is a future plan; Z.ai is
  OpenAI-compatible so the `openai` SDK can be added later if/when
  needed. No rebuild urgency.
- A second `OCREngine` impl (Gemini) — backlog A/B test; the
  `google-genai` SDK is included so the impl can be written without a
  rebuild, but no Gemini OCR call is made in Sprint 1.
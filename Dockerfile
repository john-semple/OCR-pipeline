# DEPRECATED: OCR deps now live in docker/Dockerfile (single image).
# Kept for reference only — do not build this file.
#
# OCR Pipeline — extends the OpenChamber image with the OCR stack.
#
# Build on the Pi host:
#   docker build -t openchamber-pi4-sbc-02-ocr:latest .
#
# Then recreate the container from this image (see docs/decisions.md
# "Deployment: single container" and "Secrets management"):
#   docker run --env-file /root/ocr-secrets.env \
#     -v <vault>:/home/ubuntu/Vault \
#     -v <openscan-inbox>:/mnt/openscan-inbox \
#     -v <workspaces>:/home/ubuntu/workspaces \
#     <...other OpenChamber args...> \
#     openchamber-pi4-sbc-02-ocr:latest
#
# When the base OpenChamber image is updated, rebuild this image so the
# OCR deps ride on top of the new base:
#   docker pull openchamber-pi4-sbc-02:latest
#   docker build -t openchamber-pi4-sbc-02-ocr:latest . --no-cache

FROM openchamber-pi4-sbc-02:latest

# System deps: pip, poppler (for pdf2image), image build libs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-pip \
        poppler-utils \
        libjpeg-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Python deps for the OCR pipeline.
# mistralai  — Mistral OCR API (Sprint 1)
# pdf2image  — PDF -> image conversion (requires poppler-utils above)
# Pillow     — image handling (pdf2image returns PIL Images)
# PyYAML     — parse config/classes.yaml
# watchdog   — Inbox file watcher (Sprint 1 defers to manual run;
#              included now so Sprint 2 inotify watcher needs no rebuild)
# requests   — HTTP backup / misc
# google-genai — Gemini SDK (Sprint 2 LLM sorting fallback + spelling
#              cleanup; backlog A/B test as second OCREngine).
#              Installed now to avoid a rebuild next weekend.
RUN pip3 install --no-cache-dir --break-system-packages \
        mistralai>=1.0.0 \
        pdf2image>=1.16.0 \
        Pillow>=10.0.0 \
        PyYAML>=6.0.0 \
        watchdog>=3.0.0 \
        requests>=2.31.0 \
        google-genai>=1.0.0

# Verify the key imports work after build.
RUN python3 -c "from mistralai import Mistral; print('mistralai ok')" \
 && python3 -c "from pdf2image import convert_from_path; print('pdf2image ok')" \
 && python3 -c "from PIL import Image; print('Pillow ok')" \
 && python3 -c "import yaml; print('PyYAML ok')" \
 && python3 -c "import watchdog; print('watchdog ok')" \
 && python3 -c "from google import genai; print('google-genai ok')" \
 && pdftoppm -v
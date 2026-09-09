# Phase 1 of the Cloud Run migration (see CLOUD_RUN_MIGRATION_PLAN.md).
# Packages the backend (server/) as a self-contained container -- the exact
# same code that runs locally on Windows, but with a Linux Chromium instead
# of relying on a Windows-only Chrome install path.

FROM python:3.12-slim

# Chromium (Linux's Chrome-equivalent) -- report_writer.py's render_pdf()
# shells out to this, same --headless --print-to-pdf approach as the local
# Windows Chrome install, just a different binary/path.
RUN apt-get update && apt-get install -y --no-install-recommends chromium \
    && rm -rf /var/lib/apt/lists/*

# Overrides report_writer.py's CHROME_PATH default -- already the same
# value as that default, set explicitly here so the image is correct even
# if that default ever changes.
ENV CHROME_PATH=/usr/bin/chromium

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./server/

# Cloud Run expects the container to listen on the port it provides via the
# PORT env var (usually 8080) -- server/config.py already reads PORT from
# the environment, no code change needed for that part.
EXPOSE 8080

CMD ["python", "-m", "server.Website.web_app"]

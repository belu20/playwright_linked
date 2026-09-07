FROM python:3.11-slim

# Env vars Python & Playwright:
# - PYTHONDONTWRITEBYTECODE: hindari tulis .pyc ke container disk
# - PYTHONUNBUFFERED: flush stdout/stderr langsung agar log Grafana/Promtail realtime
# - PLAYWRIGHT_BROWSERS_PATH: lokasi browser binary Playwright yang konsisten
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    TMPDIR=/tmp

# Setup tmp dengan sticky-bit permission untuk operasi Chrome headless
RUN mkdir -p /tmp && chmod 1777 /tmp

# Layer 1: Install utilitas dasar (curl & ca-certificates untuk healthcheck & ssl)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Layer 2: Install dependensi python (dipisah agar build cache tetap valid saat source code berubah)
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

# Layer 3: Download Playwright Chromium beserta dependensi OS minimal yang dibutuhkan
RUN playwright install --with-deps chromium \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /root/.cache /tmp/*

# Setup direktori kerja aplikasi
WORKDIR /app

# Buat folder logs & chrome_profiles dan symlink agar volume mount /home/promtail/:/logs selalu sinkron
RUN mkdir -p /logs /app/chrome_profiles /app/debug_image \
    && ln -sf /logs /app/logs \
    && chmod -R 777 /logs /app/chrome_profiles /app/debug_image

# Copy source code ke /app
COPY run.sh setting.py api.py ./
COPY src/ ./src/

# Permission execute run.sh
RUN chmod +x run.sh

# Healthcheck ke endpoint /healthz atau fallback ke /status
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://127.0.0.1:${PORT:-5000}/healthz || curl -f http://127.0.0.1:${PORT:-5000}/status || exit 1

# Run
CMD ["/app/run.sh"]


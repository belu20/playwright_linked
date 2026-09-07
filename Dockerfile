FROM python:3.11-slim-bookworm

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

# Layer 1: Install utilitas sistem & shared libraries Chromium untuk Debian Bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        fonts-liberation \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libatspi2.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libglib2.0-0 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libvulkan1 \
        libx11-6 \
        libx11-xcb1 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxi6 \
        libxkbcommon0 \
        libxrandr2 \
        libxrender1 \
        libxss1 \
        libxtst6 \
    && rm -rf /var/lib/apt/lists/*

# Layer 2: Install dependensi python (dipisah agar build cache tetap valid saat source code berubah)
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

# Layer 3: Download Playwright Chromium binary saja (shared libs sudah terpasang rapi di Layer 1)
RUN playwright install chromium \
    && rm -rf /root/.cache /tmp/*

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


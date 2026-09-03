FROM python:3.11-slim

# Env vars Python: jangan tulis .pyc ke disk, stdout langsung flush
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random

# Setup tmp (biar aman di container)
RUN mkdir -p /tmp && chmod 1777 /tmp
ENV TMPDIR=/tmp

# Install system dependencies yang dibutuhkan Playwright Chromium
# (shared libs untuk proses Chrome bisa start di headless mode)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    fonts-liberation \
    libnss3 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libxss1 \
    libasound2 \
    libgbm1 \
    libgtk-3-0 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxtst6 \
    libxkbcommon0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libpango-1.0-0 \
    libcairo2 \
    libglib2.0-0 \
    libnspr4 \
    libu2f-udev \
    libvulkan1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python libs dari requirements.txt
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

# Download Playwright Chromium browser binary
# (playwright install hanya download binary, tidak butuh apt lagi karena sudah diinstall di atas)
RUN playwright install chromium

# Copy source code
COPY run.sh /
COPY setting.py /
COPY api.py /
COPY src/ /src/
# COPY accounts.json /

# Permission
RUN chmod +x /run.sh

# NOTE: Jalan sebagai root (bukan non-root user) karena volume mount /home/promtail/:/logs
# di host dimiliki root, dan non-root user (appuser) tidak punya izin write ke situ.
# Kalau nanti mau balik ke non-root, sesuaikan ownership folder host dulu (lihat chat history).

# Healthcheck ke endpoint status Flask
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${PORT:-5000}/status || exit 1

# Run
CMD ["/run.sh"]

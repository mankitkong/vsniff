# vsniff bridge: Sonarr <-> streaming sites. See README "Sonarr bridge".
# The Playwright image ships Chromium + its system libraries; its version must
# match the playwright package installed below.
FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg python3-venv \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/vsniff
ENV PATH=/opt/vsniff/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    VSNIFF_DATA_DIR=/data

WORKDIR /src
COPY pyproject.toml README.md ./
COPY vsniff ./vsniff
RUN pip install --no-cache-dir ".[bridge]" "playwright==1.61.0"

VOLUME /data
EXPOSE 8990
HEALTHCHECK --interval=60s --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8990/health')"
CMD ["vsniff-bridge", "--port", "8990"]

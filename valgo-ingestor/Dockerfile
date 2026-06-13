FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Build context is the parent dir (set in valgo-platform/docker-compose.yml).
# Install valgo-common from the sibling source tree first; in CI/prod this is
# replaced by `pip install valgo-common` from the private package index.
COPY valgo-common/ ./valgo-common/
RUN pip install --no-cache-dir -e ./valgo-common

# Install the service itself
COPY valgo-ingestor/ ./valgo-ingestor/
RUN pip install --no-cache-dir -e ./valgo-ingestor

WORKDIR /app/valgo-ingestor

RUN useradd -m -u 1000 valgo && chown -R valgo:valgo /app
USER valgo

CMD ["python", "-m", "ingestor.main"]

FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY valgo-common/ ./valgo-common/
RUN pip install --no-cache-dir -e ./valgo-common

COPY valgo-execution-node/ ./valgo-execution-node/
RUN pip install --no-cache-dir -e ./valgo-execution-node

WORKDIR /app/valgo-execution-node

RUN useradd -m -u 1000 valgo && chown -R valgo:valgo /app
USER valgo

EXPOSE 8095
CMD ["uvicorn", "execution_node.main:app", "--host", "0.0.0.0", "--port", "8095"]

FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# Build deps + the TA-Lib C library (required before the Python wrapper installs)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ---- Build & install TA-Lib C library ----
ARG TALIB_VERSION=0.6.4
RUN wget -qO /tmp/talib.tar.gz \
        https://github.com/ta-lib/ta-lib/releases/download/v${TALIB_VERSION}/ta-lib-${TALIB_VERSION}-src.tar.gz \
    && tar -xzf /tmp/talib.tar.gz -C /tmp \
    && cd /tmp/ta-lib-${TALIB_VERSION} \
    && ./configure --prefix=/usr \
    && make -j"$(nproc)" \
    && make install \
    && ldconfig \
    && rm -rf /tmp/talib.tar.gz /tmp/ta-lib-${TALIB_VERSION}

WORKDIR /app

# Install valgo-common (sibling path dep — see valgo-ingestor/Dockerfile).
COPY valgo-common/ ./valgo-common/
RUN pip install --no-cache-dir -e ./valgo-common

# Install the decision service with the [indicators] (TA-Lib) and
# [pandas-indicators] (pandas-ta) extras both enabled so all strategies work.
COPY valgo-decision/ ./valgo-decision/
RUN pip install --no-cache-dir -e "./valgo-decision[indicators]"

WORKDIR /app/valgo-decision

RUN useradd -m -u 1000 valgo && chown -R valgo:valgo /app
USER valgo

CMD ["python", "-m", "decision.main"]

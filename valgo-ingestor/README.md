# valgo-ingestor

Market data ingestor for Valgo. Subscribes to Kite WebSocket in FULL mode (LTP + OHLC + 5-level depth), normalizes ticks to the canonical `valgo_common.models.Tick`, and pushes them into Redis. Auto-reconnects with exponential backoff and fails over to a backup provider when the primary is unreachable past the configured threshold.

## Architecture position

```
Kite WS  ──┐
           ├──→  ingestor (this repo)  ──→  Redis (tick:full:* + pub/sub)
Fyers WS ──┘                                       │
                                                   ▼
                                           valgo-decision (subscribes)
```

The ingestor is the only producer for the tick keyspace. Other services consume from Redis and never know which provider produced a tick.

## Run locally

```bash
# Prerequisite: valgo-common cloned as a sibling
git clone https://github.com/your-org/valgo-common.git ../valgo-common

python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ../valgo-common
pip install -e ".[dev]"

cp .env.example .env
# Fill KITE_API_KEY, KITE_ACCESS_TOKEN, etc.

python -m ingestor.main
```

The access token is generated daily by the `valgo-auth-refresh` Lambda. For local development, run `python -m valgo_auth_refresh.kite_login` (from that repo) to produce one interactively, then paste it into `.env` as `KITE_ACCESS_TOKEN=...`.

## Run via Docker

```bash
docker build -t valgo-ingestor .
docker run --rm --env-file .env --network host valgo-ingestor
```

## What this repo does NOT do

- It does not place orders. The `decision` service consumes ticks from Redis and decides; the `execution_router` and `execution_node` services place the actual orders.
- It does not implement any strategy. It is a dumb pipe: provider → normalize → Redis.
- It does not store ticks long-term. Redis TTL is 5 minutes; ticks past that window are gone unless your strategy explicitly snapshots them.

## Operational notes

- **Reconnection** is delegated to the kiteconnect SDK's built-in retry. Config in `kite_source.py`: `max_reconnect_tries=5`, `reconnect_max_delay=32` (exponential 1s → 32s).
- **Failover** to Fyers is gated by a threshold (default 10s). The `Fyers` source is currently a skeleton (`fyers_source.py`) — implement when you have credentials and want failover testing.
- **Subscription** is the union of the base instrument list (always-subscribed indices, VIX) plus instruments declared by active strategies. Resolved on startup via `valgo_common.dynamodb.get_config("subscriptions")`.

## Tests

```bash
pytest
```

## Versioning

Bumps semver on every release. Changes that affect the canonical Redis key schema or the Tick payload force a `valgo-common` major bump first; this service follows.

## Branching & environments

This repo follows the workspace-wide branching strategy:

- `main` — production. Auto-deploys to cloud-prod on merge.
- `staging` — pre-production. Auto-deploys to cloud-staging on merge.
- Feature branches off `staging`; PR into `staging`; promote to `main` after a full market session of soak.

Two env templates ship with this repo:

- `.env.local.example` — local dev (plain values; copy to `.env`).
- `.env.cloud.example` — cloud (mostly Secrets Manager IDs; real values injected at runtime).

Full doc: [valgo-docs/branching.md](../valgo-docs/branching.md).

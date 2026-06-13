# valgo-execution-router

The pre-trade gate. Sits behind an internal NLB. The decision engine and webhook handler POST `OrderRequest` payloads here; the router decides whether to let them through.

## Pipeline

```
incoming OrderRequest
        │
        ├─ 1. idempotency (Redis SETNX, 24h TTL)        ← rejects duplicates
        ├─ 2. risk gates (kill switch, daily loss, …)   ← rejects unsafe orders
        ├─ 3. rate limit (10/sec per account, SEBI)     ← rejects bursts
        ├─ 4. persist Order to DynamoDB (status=PENDING)
        └─ 5. dispatch to execution node (HTTP POST)
```

Each gate is cheaper than the next — a kill-switch read costs one Redis GET; a DDB write costs ~10ms. Order matters: cheapest first.

## Why it's separate from the execution node

The router runs on Fargate (stateless, scales horizontally, no broker creds). The execution node runs on EC2 in the cluster placement group with the broker SDK loaded and credentials in memory. Splitting them keeps the broker-credentialed surface area small.

## Run locally

```bash
git clone https://github.com/your-org/valgo-common.git ../valgo-common
pip install -e ../valgo-common
pip install -e ".[dev]"

cp .env.example .env
uvicorn execution_router.main:app --reload --port 8090
```

## SEBI compliance notes

- The `10 orders/sec` cap is enforced via Redis fixed-window counter (`rate_limiter.py`). This matches the rule literally; token-bucket would allow burst > 10/sec inside a 1-second wall-clock window.
- Every rejection is audited to DDB with a `order_rejected_pretrade` event type.

## Versioning

Bumps independently of `valgo-common`. The router speaks `valgo_common.models.OrderRequest`; if that model changes shape, bump common first, then this repo.

## Branching & environments

This repo follows the workspace-wide branching strategy:

- `main` — production. Auto-deploys to cloud-prod on merge.
- `staging` — pre-production. Auto-deploys to cloud-staging on merge.
- Feature branches off `staging`; PR into `staging`; promote to `main` after a full market session of soak.

Two env templates ship with this repo:

- `.env.local.example` — local dev (plain values; copy to `.env`).
- `.env.cloud.example` — cloud (mostly Secrets Manager IDs; real values injected at runtime).

Full doc: [valgo-docs/branching.md](../valgo-docs/branching.md).

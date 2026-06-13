# valgo-webhook

Receives TradingView signals (and any other external signal source), verifies their HMAC, audits them, then forwards as `OrderRequest` to the execution router.

## Architecture position

```
TradingView  ──(HTTPS POST + X-Signature)──> ALB ──> webhook (this repo)
                                                          │
                                                          ├─→ DDB audit trail
                                                          └─→ execution-router (POST /orders)
```

Every signal is audited regardless of what happens downstream. The router applies the same risk gates webhook-originated orders as decision-engine-originated orders — this service does not bypass any pre-trade checks.

## Endpoint

```
POST /webhook/tv/{slug}
Headers:
  X-Signature: <hex(HMAC-SHA256(body, TRADINGVIEW_SHARED_SECRET))>
Body (JSON):
  {
    "strategy_id": "s1",
    "tradingsymbol": "NIFTY26500CE",
    "side": "BUY",
    "quantity": 50,
    "price": 142.5,
    "idempotency_key": "tv:s1:1714521825"   // optional; auto-generated if absent
  }
```

In TradingView's webhook config, set the URL to `https://your-alb-host/webhook/tv/<slug>` and add a custom header for the signature. TradingView doesn't sign payloads natively — generate the signature in your alert message template using the platform's macros, or use a TradingView-to-webhook bridge that handles signing.

## Run locally

```bash
git clone https://github.com/your-org/valgo-common.git ../valgo-common
pip install -e ../valgo-common
pip install -e ".[dev]"

cp .env.example .env
uvicorn webhook.main:app --reload --port 8092
```

Test with curl:

```bash
SECRET=$(grep TRADINGVIEW_SHARED_SECRET .env | cut -d= -f2)
BODY='{"strategy_id":"s1","tradingsymbol":"NIFTY26500CE","side":"BUY","quantity":50,"price":142.5}'
SIG=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | cut -d' ' -f2)
curl -X POST http://localhost:8092/webhook/tv/test \
  -H "X-Signature: $SIG" -H "Content-Type: application/json" -d "$BODY"
```

## Versioning

Independent of common. The wire contract is the HTTP shape above; change it carefully because TradingView alerts are notoriously hard to roll out atomically.

## Branching & environments

This repo follows the workspace-wide branching strategy:

- `main` — production. Auto-deploys to cloud-prod on merge.
- `staging` — pre-production. Auto-deploys to cloud-staging on merge.
- Feature branches off `staging`; PR into `staging`; promote to `main` after a full market session of soak.

Two env templates ship with this repo:

- `.env.local.example` — local dev (plain values; copy to `.env`).
- `.env.cloud.example` — cloud (mostly Secrets Manager IDs; real values injected at runtime).

Full doc: [valgo-docs/branching.md](../valgo-docs/branching.md).

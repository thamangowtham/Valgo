# valgo-execution-node

The only service that talks to the broker for placing orders. Runs on EC2 in the cluster placement group, in the private subnet whose default route goes through the NAT Gateway with the whitelisted Elastic IP.

## Architecture position

```
execution-router  ──(POST /place)──>  execution-node (this repo)
                                              │
                                              └─→ Kite Connect API
                                                    (egress via NAT EIP — the IP
                                                     whitelisted with the broker)
```

All N execution-node instances share the same NAT EIP → same whitelisted IP → same broker API key. SEBI's static-IP rule is satisfied at the NAT GW level, not per-instance.

## Run locally

```bash
git clone https://github.com/your-org/valgo-common.git ../valgo-common
pip install -e ../valgo-common
pip install -e ".[dev]"

cp .env.example .env
# Fill KITE_API_KEY and KITE_ACCESS_TOKEN
uvicorn execution_node.main:app --reload --port 8095
```

## How orders flow

1. `execution-router` POSTs `{order_id, request}` to `POST /place`
2. `broker_adapter.py` translates the internal `OrderRequest` into Kite Connect's parameters (including MARKET → MPP per SEBI 2026)
3. The broker SDK's `place_order` runs on a thread (it's blocking)
4. On success, the order record in DDB is updated to `SUBMITTED` with the broker's order id
5. On failure, the record is updated to `REJECTED` with the broker's reason

A separate goroutine (TODO in this skeleton) listens to the broker's order-update WebSocket and writes fill events to DDB.

## SEBI 2026 compliance touchpoints

- **Static IP**: enforced at the NAT GW level. Verify with `terraform output whitelist_ip` from the `valgo-infra` repo and confirm broker registration matches.
- **MARKET → MPP conversion**: handled in `broker_adapter._build_kite_params`. Kite Connect enforces MPP server-side post-April-2026, so no client-side price-band math is required.
- **Idempotency**: the router has already deduped before the order reaches this service. The `tag` field on Kite orders records the strategy id for audit traceability.

## Versioning

Bumps independently. The contract this service implements is the HTTP shape on `POST /place` — `{order_id, request}` where `request` is a `valgo_common.models.OrderRequest`. As long as that holds, internal refactoring is free.

## Branching & environments

This repo follows the workspace-wide branching strategy:

- `main` — production. Auto-deploys to cloud-prod on merge.
- `staging` — pre-production. Auto-deploys to cloud-staging on merge.
- Feature branches off `staging`; PR into `staging`; promote to `main` after a full market session of soak.

Two env templates ship with this repo:

- `.env.local.example` — local dev (plain values; copy to `.env`).
- `.env.cloud.example` — cloud (mostly Secrets Manager IDs; real values injected at runtime).

Full doc: [valgo-docs/branching.md](../valgo-docs/branching.md).

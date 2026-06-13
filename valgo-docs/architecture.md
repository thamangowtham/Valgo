# Architecture

## Goals

1. **Sub-second tick → order latency.** Target: 160-570ms p99 for the full path from market tick arrival to broker order acknowledgment.
2. **SEBI April 2026 compliance.** Static IP whitelisting, daily TOTP auth refresh, 10 orders/sec rate cap, MARKET → MPP conversion.
3. **Always-on data layer.** Auto-reconnect with exponential backoff, auto-failover from Kite primary to Fyers backup on threshold breach.
4. **Operationally simple.** A single person should be able to run, deploy, and debug this system.

## Non-goals

- Multi-region resilience (single AZ in `ap-south-1` for latency)
- Multi-broker trading (one whitelisted IP = one broker account, by design)
- Multi-tenant isolation (personal project)

## Latency budget

```
Market tick arrives at Kite WS
  │
  ├─ Kite → ingestor pod          ~5-30ms   (network, AZ-local)
  ├─ Normalize + Redis SET        ~1-2ms    (in-VPC ElastiCache)
  ├─ Pub/sub → decision engine    ~1-3ms    (Redis pub/sub)
  ├─ Strategy compute             ~5-50ms   (depends on strategy)
  ├─ Decision → router HTTP POST  ~2-5ms    (internal NLB)
  ├─ Risk + rate limit checks     ~3-8ms    (Redis reads)
  ├─ DynamoDB put_item            ~10-30ms  (DDB)
  ├─ Router → exec node           ~2-5ms    (intra-VPC HTTP)
  ├─ Kite Connect place_order     ~80-400ms (broker round-trip — dominates)
  └─ Total                        ~110-550ms typical
```

The broker round-trip dominates everything else. Every other component must stay tight to keep the budget.

## Component responsibilities

### Ingestor (`services/ingestor`)
- Subscribes to Kite WebSocket in FULL mode (LTP + OHLC + 5-level depth)
- Normalizes provider-specific tick payloads to the common `Tick` model
- Writes to Redis (`tick:full:{symbol}` with 5min TTL) and publishes on `tick:channel:{symbol}`
- Manages reconnection (exponential backoff 1s → 32s, max 5 attempts) and auto-failover to Fyers backup
- One pod, always warm — Fargate task, never scales to zero

### Decision engine (`services/decision`)
- Loads active strategies from DDB config
- For each strategy, subscribes to its required tick channels in Redis
- Routes ticks to the strategy's `on_tick`, signals to `on_signal`
- When a strategy emits an order, POSTs to the execution router

### Execution router (`services/execution_router`)
- Internal-only FastAPI service behind an internal NLB
- The pre-trade gate: idempotency check → risk gates → rate limit → persist → dispatch
- Risk: kill switch, daily loss, position count, notional value (cheapest checks first)
- Rate limit: token bucket in Redis, fixed 1-second window, 10/sec per account
- Idempotency: Redis SETNX on `idempotency_key`, 24h TTL

### Execution node (`services/execution_node`)
- Runs on EC2 in the cluster placement group, private subnet
- Has the broker connection — only egress through the NAT GW with whitelisted EIP
- Receives orders from the router via HTTP, calls Kite Connect's `place_order`
- Updates the `Order` record in DDB with `broker_order_id` and status

### Webhook handler (`services/webhook`)
- Public-facing FastAPI behind ALB on port 443
- Verifies HMAC signature on inbound payloads (TradingView etc)
- Audits every signal regardless of outcome
- Forwards as `OrderRequest` to the execution router (same path as decision-engine orders)

### Auth refresh (`services/auth_refresh`)
- AWS Lambda, triggered by EventBridge cron at `cron(15 3 ? * MON-FRI *)` (08:45 IST)
- Runs the four-step Kite login flow with TOTP
- Writes the fresh `access_token` to Secrets Manager
- Notifies via SNS on failure

### Admin API (`services/admin_api`)
- FastAPI service backing the React panel
- Bearer-token auth (swap for Cognito in production)
- CRUD on strategies, data sources, accounts, nodes, signals, risk
- Reads/writes the `config` DDB table; mirrors hot config (risk limits, kill switch) to Redis

### Admin panel (`admin/`)
- React + Vite + Tailwind + TypeScript
- Dark/light theme switcher with persistent storage
- 8 sections: Dashboard, Strategies, Market data (4 tabs), Signals, Accounts, Nodes, Risk, Audit
- Single-file `App.jsx` (76KB) — the v4 panel from the iteration

## Network topology

```
Internet
  │
  ├─→ ALB (public)        → admin panel, webhook handler
  │
NAT GW (Elastic IP)       ← THE whitelisted IP, all egress
  │
  ├──── Public subnet ────────────────────────────────────────
  │     · ALB ENIs
  │     · NAT GW
  │
  └──── Private subnet ───────────────────────────────────────
        · ECS Fargate (ingestor, decision, router, webhook, admin_api)
        · EC2 execution nodes (cluster placement group)
        · ElastiCache Redis (single node)
        · Internal NLB (decision → router)
```

Single AZ. Single NAT. Multi-AZ trades latency for HA — for personal-scale algo trading, latency matters more than 99.99% uptime, and the broker is the actual failure domain anyway (your strategy can't trade if the broker is down).

## Data layer

- **Redis (ElastiCache)**: hot tick cache, pub/sub channels, rate limiter buckets, idempotency keys, risk hot config (kill switch, daily P&L). Single node `cache.t4g.small` for dev, `cache.m6g.large` for prod.
- **DynamoDB**: orders, positions, audit, config. On-demand billing; PITR enabled in prod.
- **No relational DB.** All access patterns are key-based. SQL would add latency without benefit.

## Cost estimates (ap-south-1, INR ₹83/USD)

| Tier | Components | Monthly USD | Monthly INR |
|------|-----------|-------------|-------------|
| Starter (1-2 nodes, 1 strategy) | t4g.small Redis + Fargate + 1 EC2 + NAT GW | ~$195 | ~₹16,000 |
| Production (4 nodes, multi-strategy) | m6g.large Redis + Fargate + 4 EC2 + NAT GW + CloudWatch | ~$575 | ~₹48,000 |
| SEBI compliance overhead | NAT GW ($33) + Lambda ($1) + Secrets ($1) | +$35-40 | +₹3,000-3,500 |

Optimization wins: Spot for stateless services (~70% off), Graviton ARM (~20% off), tight CloudWatch budget alarms.

## Sequence: tick to order

```
Kite WS  ──tick──>  Ingestor pod
                       │
                       ├─ SET tick:full:NIFTY26500CE
                       └─ PUBLISH tick:channel:NIFTY26500CE
                                                │
                                                ▼
                                       Decision engine pod
                                                │
                                                ├─ Strategy.on_tick(tick)
                                                └─ emit_order(...)
                                                        │
                                                        ▼
                                               Execution router (NLB)
                                                        │
                                                        ├─ idem check (Redis)
                                                        ├─ risk check (Redis)
                                                        ├─ rate limit (Redis INCR)
                                                        ├─ put_order (DDB)
                                                        └─ dispatch_to_node(order)
                                                                │
                                                                ▼
                                                       Execution node (EC2)
                                                                │
                                                                ├─ broker.place_order()
                                                                │       │
                                                                │       └──→ Kite (via NAT EIP)
                                                                │
                                                                └─ update_order_status (DDB)
```

## What's intentionally not in this scaffold

- **Fyers source implementation** — only the Kite primary is fleshed out. `fyers_source.py` is a skeleton; implement when you have credentials and want to test failover.
- **Per-service ECS task definitions in Terraform** — `compute/main.tf` defines the cluster, ALB, NLB, and EC2 SG; expand it with `aws_ecs_task_definition` and `aws_ecs_service` blocks per service when you deploy.
- **Observability module** — directory exists but empty. Add CloudWatch alarms (feed_disconnected, daily_loss_breached, auth_refresh_failed) and a dashboard there.
- **Tests** — no test files yet. Add `tests/` directories under each service.

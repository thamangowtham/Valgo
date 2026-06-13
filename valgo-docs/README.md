# valgo-docs

Cross-cutting documentation for the Valgo trading platform. Lives in its own repo so it can be referenced from any of the service repos without circular pulls, and so non-engineers (or future-you) can read it without cloning ten things.

## Contents

- `architecture.md` — system design, latency budget, component responsibilities, data layer, network topology, sequence diagrams. Read first.
- `runbook.md` — daily operations checklist, incident response procedures, common debugging recipes, deployment commands.
- `sebi-compliance.md` — maps each SEBI April 2026 algorithmic trading rule to the specific repo + file that implements it.

## Repo map

The Valgo platform is split into eleven repos. Here's what each one owns:

| Repo | What it owns |
|------|-------------|
| `valgo-common` | Shared library: pydantic models, config, Redis/DDB clients, logging |
| `valgo-ingestor` | Market data ingestor (Kite WebSocket → Redis) |
| `valgo-decision` | Decision engine + strategies + TA-Lib indicator wrappers |
| `valgo-execution-router` | Pre-trade gate: idempotency, risk, rate limit, dispatch |
| `valgo-execution-node` | Broker-facing executor (Kite Connect order placement) |
| `valgo-webhook` | TradingView signal receiver (HMAC-verified) |
| `valgo-auth-refresh` | Daily TOTP authentication refresh (AWS Lambda) |
| `valgo-admin-api` | FastAPI backend for the admin panel |
| `valgo-admin-ui` | React admin panel with dark/light theme |
| `valgo-infra` | Terraform for AWS resources |
| `valgo-docs` | This repo |

## How the repos depend on each other

```
                            valgo-common
                                  │
        ┌─────────┬───────────────┼───────────────┬──────────┐
        ▼         ▼               ▼               ▼          ▼
   ingestor   decision    execution-router   execution-node  webhook
                                  ▲                            │
                                  └────────────────────────────┘
        ┌────────────────┐
        │ admin-api ────→ admin-ui (HTTP, not pip)
        └────────────────┘

valgo-auth-refresh   independent — packages as Lambda zip
valgo-infra          independent — Terraform, but consumes the auth-refresh zip
```

`valgo-common` is the only Python package every service depends on. Bumping it requires coordination; bumping any service is an isolated event.

## Reading order for new contributors

1. `architecture.md` — what is this system?
2. `valgo-common/valgo_common/models.py` — the canonical types every service speaks
3. `valgo-ingestor/README.md` — how data enters the system
4. `valgo-decision/README.md` — how decisions get made
5. `valgo-execution-router` and `valgo-execution-node` READMEs — how orders get to the broker
6. `sebi-compliance.md` — why the architecture is shaped the way it is
7. `runbook.md` — what you do when things break

## Versioning

Tag this repo when a major architectural change lands across the system. The version here corresponds to a "platform version" that other repos can reference for compatibility.

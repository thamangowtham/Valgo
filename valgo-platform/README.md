# valgo-platform

Workspace orchestration for the Valgo multi-repo. Clone this repo, run `./bootstrap.sh`, and you'll have all eleven Valgo repos checked out as siblings, with a single `docker-compose.yml` that spins up Redis, DynamoDB-local, and the services for local development.

This repo is **not** part of the runtime. It exists for developer ergonomics — replacing the convenience that a monorepo would have provided.

## Usage

```bash
git clone https://github.com/your-org/valgo-platform.git
cd valgo-platform
./bootstrap.sh
docker compose up -d
```

After that:

```
../valgo-common              ← cloned, editable in your IDE
../valgo-ingestor            ← cloned
../valgo-decision            ← cloned
../valgo-execution-router    ← cloned
../valgo-execution-node      ← cloned
../valgo-webhook             ← cloned
../valgo-auth-refresh        ← cloned
../valgo-admin-api           ← cloned
../valgo-admin-ui            ← cloned
../valgo-infra               ← cloned
../valgo-docs                ← cloned
```

## What's in here

- `bootstrap.sh` — clones all repos as siblings of this one
- `docker-compose.yml` — starts Redis + DynamoDB-local + all services in dev mode
- `dev.env` — single source of truth for local env vars (consumed by all services)
- `Makefile` — common dev commands (`make up`, `make logs`, `make test-all`)

## Why this isn't a monorepo

The Valgo platform is split across eleven repos — see `../valgo-docs/README.md` for the full map and the rationale. The short version: each service has a clear owner, an independent release cadence, and a focused dependency footprint. Splitting them prevents a change in one service from forcing CI to rebuild the whole world.

The price is exactly this kind of orchestration scaffolding for local development. We accept that price.

## Branching & environments

This repo follows the workspace-wide branching strategy:

- `main` — production. Auto-deploys to cloud-prod on merge.
- `staging` — pre-production. Auto-deploys to cloud-staging on merge.
- Feature branches off `staging`; PR into `staging`; promote to `main` after a full market session of soak.

Two env templates ship with this repo:

- `.env.local.example` — local dev (plain values; copy to `.env`).
- `.env.cloud.example` — cloud (mostly Secrets Manager IDs; real values injected at runtime).

Full doc: [valgo-docs/branching.md](../valgo-docs/branching.md).

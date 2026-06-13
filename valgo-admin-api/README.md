# valgo-admin-api

Backend for the Valgo admin panel (`valgo-admin-ui`). FastAPI service that reads/writes config rows in DynamoDB and exposes them as JSON over HTTP, with bearer-token auth.

## Routes

- `GET/PUT /api/strategies`
- `GET/PUT /api/data-sources`
- `GET/PUT /api/accounts`
- `GET/PUT /api/nodes`
- `GET/PUT /api/signals`
- `GET/PUT /api/risk` — also mirrors hot config to Redis (kill switch, risk limits)
- `GET /api/audit?limit=100` — recent audit events

All routes require `Authorization: Bearer <ADMIN_API_TOKEN>`. CORS is open to `localhost:5173` and `localhost:3000` for development; tighten for production.

## Run locally

```bash
git clone https://github.com/your-org/valgo-common.git ../valgo-common
pip install -e ../valgo-common
pip install -e ".[dev]"

cp .env.example .env
uvicorn admin_api.main:app --reload --port 8080
```

The admin UI at `valgo-admin-ui` is configured to proxy `/api/*` to this service in dev (see its `vite.config.ts`).

## Production auth

The bearer-token approach is fine for personal use. For team deployments swap for AWS Cognito User Pools or a similar provider — replace the `auth_dep` function in `main.py` with a JWT verifier.

## Versioning

Couples to `valgo-common` for models. Couples to the `valgo-admin-ui` repo for the wire format. The two should bump in lockstep when the API contract changes; bump independently when only one side's internals change.

## Branching & environments

This repo follows the workspace-wide branching strategy:

- `main` — production. Auto-deploys to cloud-prod on merge.
- `staging` — pre-production. Auto-deploys to cloud-staging on merge.
- Feature branches off `staging`; PR into `staging`; promote to `main` after a full market session of soak.

Two env templates ship with this repo:

- `.env.local.example` — local dev (plain values; copy to `.env`).
- `.env.cloud.example` — cloud (mostly Secrets Manager IDs; real values injected at runtime).

Full doc: [valgo-docs/branching.md](../valgo-docs/branching.md).

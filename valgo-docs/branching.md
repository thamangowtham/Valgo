# Branching strategy and deployment environments

All Valgo service repos follow the same conventions so an operator can move between them without surprises.

## Branches

| Branch    | Tracks                  | Deploys to               | Who can push                       |
|-----------|-------------------------|--------------------------|------------------------------------|
| `main`    | Production-ready code   | Cloud / production       | Merge from PRs only (no direct)    |
| `staging` | Pre-production / canary | Cloud / staging          | Merge from PRs only (no direct)    |
| Feature   | `feature/*`, `fix/*`    | Local dev only           | Anyone                             |

**Promotion flow:** feature branch → PR → `staging` → soak / verify → PR → `main`.

A change must run on staging for at least one full market session before it's eligible to merge into main. The execution layer is especially strict here — anything that touches broker API calls, risk checks, or the OMS state machine should be merged to `staging` and observed for at least 24 hours before being promoted.

`main` is protected. Direct pushes are disabled. Force-push to either branch is disallowed.

## Deployment environments

| Environment | Branch    | Trigger              | Infra source         | Env vars file              |
|-------------|-----------|----------------------|----------------------|----------------------------|
| Local       | any       | `docker compose up`  | `valgo-platform`     | `.env.local.example`        |
| Staging     | `staging` | CI on push           | `valgo-infra` (TF)   | `.env.cloud.example` + SM   |
| Production  | `main`    | CI on push           | `valgo-infra` (TF)   | `.env.cloud.example` + SM   |

Each repo ships **two** env templates:

- `.env.local.example` — local dev. Real values pasted in. Plain `.env` is gitignored.
- `.env.cloud.example` — cloud deploys. Most fields are *secret IDs* (e.g. `KITE_ACCESS_TOKEN_SECRET_ID=valgo/kite/access_token`); the running container resolves them against AWS Secrets Manager at startup.

The two `prod` environments (`staging` and production) share the same template; they differ only in the `DYNAMODB_TABLE_PREFIX` (`valgo_staging_` vs `valgo_prod_`) and the Secrets Manager namespace (`valgo/staging/...` vs `valgo/prod/...`).

## Reference vs deployable

The `sharemarket/` directory at the workspace root is the **reference monolith** — it captures the full data → indicator → signal → alert flow in one place, and is **not deployed**. New code lands in the per-service repos, all of which are individually deployable to local and cloud.

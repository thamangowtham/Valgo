# valgo-auth-refresh

The daily Kite Connect authentication refresh. Runs as an AWS Lambda triggered by EventBridge at 08:45 IST every weekday. Performs the four-step Kite login flow (username/password → TOTP → request_token → access_token) and writes the fresh `access_token` to AWS Secrets Manager.

## Why a Lambda

This isn't on the hot path — it runs once per trading day and finishes in a few seconds. Lambda is cheaper, easier to schedule, and isolates the broker credentials from the long-running services that consume the resulting token.

## Why this is its own repo

The Lambda has very different deployment characteristics from the long-running services: it ships as a zip with bundled dependencies, runs on AWS-provided runtime, and gets invoked directly by EventBridge. Bundling it with everything else would mean every service redeploy would touch the Lambda, and vice versa.

## Schedule

EventBridge cron expression in `valgo-infra/modules/auth/main.tf`:

```
cron(15 3 ? * MON-FRI *)   # 03:15 UTC = 08:45 IST, weekdays
```

## Local dev — generating an access token interactively

When developing services that need a real token (ingestor, execution-node), run:

```bash
pip install -e .
python -m valgo_auth_refresh.kite_login
# → prompts you to open Kite's login URL, paste request_token, prints access_token
```

Paste the printed token into the consuming service's `.env` as `KITE_ACCESS_TOKEN=...`.

## Build & deploy

```bash
./scripts/package.sh
# Produces dist/auth_refresh.zip
# Deployed via Terraform from valgo-infra:
#   cd ../valgo-infra/envs/prod
#   terraform apply -target=module.valgo.module.auth.aws_lambda_function.auth_refresh
```

## Secrets read

| Secret ID | Purpose |
|-----------|---------|
| `valgo/kite/api_key` | Kite Connect app API key |
| `valgo/kite/api_secret` | Kite Connect app API secret |
| `valgo/kite/user_id` | Your Zerodha user id |
| `valgo/kite/password` | Your Zerodha account password |
| `valgo/kite/totp_seed` | Base32 TOTP seed from Zerodha 2FA setup |

## Secret written

| Secret ID | Purpose |
|-----------|---------|
| `valgo/kite/access_token` | The day's access token. Consumed by ingestor, execution-node. |

## On failure

A failure here means no service can authenticate to Kite for the day. The Lambda publishes to an SNS topic (`valgo-{env}-alerts`) on both success and failure — wire that to your phone via SES → email or SNS → SMS. The runbook in `valgo-docs` explains manual recovery.

## Versioning

Lambda code is small and changes rarely. When it does change, bump version, repackage, and apply Terraform. The wire interface (Secrets Manager IDs, SNS topic format) is the contract — bumping major would force `valgo-infra` to bump too.

## Branching & environments

This repo follows the workspace-wide branching strategy:

- `main` — production. Auto-deploys to cloud-prod on merge.
- `staging` — pre-production. Auto-deploys to cloud-staging on merge.
- Feature branches off `staging`; PR into `staging`; promote to `main` after a full market session of soak.

Two env templates ship with this repo:

- `.env.local.example` — local dev (plain values; copy to `.env`).
- `.env.cloud.example` — cloud (mostly Secrets Manager IDs; real values injected at runtime).

Full doc: [valgo-docs/branching.md](../valgo-docs/branching.md).

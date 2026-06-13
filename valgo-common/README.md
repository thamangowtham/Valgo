# valgo-common

Shared library used by every Valgo service. Lives in its own repo so it can be versioned, published, and pinned independently.

## What's in here

- `valgo_common.models` — pydantic models for ticks, orders, strategies, risk limits (the canonical types every service speaks)
- `valgo_common.config` — pydantic-settings loader; reads `.env` locally, AWS Secrets Manager in prod
- `valgo_common.redis_client` — async Redis wrapper with the canonical key schema (`tick:full:{symbol}`, `rate:orders:{account}`, etc.)
- `valgo_common.dynamodb` — async DynamoDB accessors for orders, audit, config, positions tables
- `valgo_common.logging` — structlog setup; JSON in prod, pretty in local

## Install

In production, this gets published to a private package registry (CodeArtifact / private PyPI) and other services pin a version:

```toml
# in valgo-ingestor/pyproject.toml
dependencies = [
    "valgo-common>=0.1,<0.2",
]
```

For local development, install editable from a sibling clone:

```bash
git clone https://github.com/your-org/valgo-common.git
git clone https://github.com/your-org/valgo-ingestor.git
cd valgo-ingestor
pip install -e ../valgo-common
pip install -e ".[dev]"
```

## Versioning

Semver. Breaking model changes (renaming a field on `Tick`, removing a key in `redis_client`) require a major bump. Adding new models or new helpers is a minor bump.

When you bump, update each consumer repo's pinned version on its own schedule — the whole point of separate repos is letting them diverge.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

## Building & publishing

```bash
pip install build twine
python -m build
# Internal:
twine upload --repository codeartifact dist/*
# Or PyPI for an open-source variant:
twine upload dist/*
```

## Branching & environments

This repo follows the workspace-wide branching strategy:

- `main` — production. Auto-deploys to cloud-prod on merge.
- `staging` — pre-production. Auto-deploys to cloud-staging on merge.
- Feature branches off `staging`; PR into `staging`; promote to `main` after a full market session of soak.

Two env templates ship with this repo:

- `.env.local.example` — local dev (plain values; copy to `.env`).
- `.env.cloud.example` — cloud (mostly Secrets Manager IDs; real values injected at runtime).

Full doc: [valgo-docs/branching.md](../valgo-docs/branching.md).

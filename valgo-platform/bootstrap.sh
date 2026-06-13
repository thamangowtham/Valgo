#!/usr/bin/env bash
# Clone all Valgo repos as siblings of this one.
set -euo pipefail

cd "$(dirname "$0")/.."   # parent of valgo-platform

REPOS=(
    valgo-common
    valgo-ingestor
    valgo-decision
    valgo-execution-router
    valgo-execution-node
    valgo-webhook
    valgo-auth-refresh
    valgo-admin-api
    valgo-admin-ui
    valgo-infra
    valgo-docs
)

# Set this to your fork URL
ORG_URL="${VALGO_ORG_URL:-https://github.com/your-org}"

for repo in "${REPOS[@]}"; do
    if [ -d "$repo" ]; then
        echo "✓ $repo already exists"
    else
        echo "→ cloning $repo"
        git clone "$ORG_URL/$repo.git"
    fi
done

echo
echo "All repos checked out. Next:"
echo "  cd valgo-platform"
echo "  cp dev.env.example dev.env   # fill in Kite credentials"
echo "  docker compose up -d"

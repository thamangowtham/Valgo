# valgo-admin-ui

The Valgo admin panel. React + Vite + Tailwind + TypeScript. Single-file `App.jsx` with a dark/light theme switcher.

## Sections

1. **Dashboard** — system health, recent activity, key metrics
2. **Strategies** — CRUD on trading strategies, declared instrument subscriptions
3. **Market data** — data sources (Kite primary, Fyers backup), effective subscription view, base instruments list, resilience config
4. **Signal sources** — webhook URL registry mapping inbound signals to strategies
5. **Broker accounts** — credentials, static IP, TOTP status
6. **Execution nodes** — EC2 inventory and status
7. **Risk limits** — kill switch, daily loss cap, position limits, rate limit
8. **Audit log** — order history with filters

Theme switcher persists choice across sessions via the platform's storage API.

## Develop

```bash
npm install
npm run dev
```

The Vite dev server runs on port 5173 and proxies `/api/*` to `valgo-admin-api` at `http://localhost:8080` (configured in `vite.config.ts`).

Set the bearer token via env var so the UI can authenticate:

```bash
echo "VITE_ADMIN_API_TOKEN=<your-token>" > .env.local
npm run dev
```

For production, the token is typically injected at deploy time via the host page (set `window.__VALGO_TOKEN` before mounting).

## Build

```bash
npm run build
# Output goes to dist/, ready to be served by nginx (see Dockerfile)
```

## Deploy

```bash
docker build -t valgo-admin-ui .
docker run --rm -p 8080:80 valgo-admin-ui
```

In production this is served by an nginx container behind the public ALB. The nginx config (`nginx.conf`) proxies `/api/*` to the `admin-api` service in the same VPC.

## Versioning

Couples loosely to `valgo-admin-api`. The admin API should be backward compatible across UI versions for at least one minor version so that out-of-sync deploys don't break the panel.

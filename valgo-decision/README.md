# valgo-decision

The decision engine. Loads active strategies from DDB config, subscribes to the Redis tick channels each strategy declares, dispatches ticks to strategies, and forwards the resulting orders to the execution router.

## Architecture position

```
Redis tick channels  ──→  decision (this repo)  ──→  valgo-execution-router
                                  │
                                  └─ runs strategy.on_tick() / on_signal()
```

This repo holds:

- `decision/main.py` — strategy loader and tick dispatch loop
- `decision/indicators.py` — TA-Lib wrappers tuned for streaming tick data
- `decision/strategies/base.py` — base class strategies inherit from
- `decision/strategies/ema_crossover.py` — example strategy (EMA(9)/EMA(21) crossover via TA-Lib)

## Adding a new strategy

1. Create `decision/strategies/your_strategy.py` with a class inheriting `StrategyBase`
2. Implement `async def on_tick(self, tick: Tick) -> None`
3. Optionally implement `async def on_signal(self, signal: dict) -> None` for webhook-driven strategies
4. Register the class in `STRATEGY_REGISTRY` in `decision/main.py`
5. Add a strategy config row via the admin panel

The base class provides `self.emit_order(symbol, side, qty)` — call this when entry/exit fires. It builds an `OrderRequest`, generates an idempotency key, and POSTs to the execution router.

## TA-Lib

This service is the only one with the heavy TA-Lib dependency, which is why it has its own repo and its own Dockerfile that builds the C library from source. Without TA-Lib, indicator math would have to be reimplemented in Python — slow, error-prone.

To run locally without Docker, install the C library first:

```bash
# macOS
brew install ta-lib

# Linux (Debian/Ubuntu)
wget https://github.com/ta-lib/ta-lib/releases/download/v0.6.4/ta-lib-0.6.4-src.tar.gz
tar -xzf ta-lib-0.6.4-src.tar.gz
cd ta-lib-0.6.4 && ./configure --prefix=/usr && make && sudo make install

# Then
git clone https://github.com/your-org/valgo-common.git ../valgo-common
pip install -e ../valgo-common
pip install -e ".[indicators,dev]"
python -m decision.main
```

If you don't need indicators (writing strategies that use only price comparisons, time-of-day rules, etc.), install just the base:

```bash
pip install -e ".[dev]"
```

The example strategy uses TA-Lib so won't import in that mode — remove or comment it from the registry.

## Versioning

Bumps independently. Strategy classes are an internal contract; what's external is what the engine consumes (`Tick`) and emits (`OrderRequest`). Changes to either force a `valgo-common` bump first.

## Branching & environments

This repo follows the workspace-wide branching strategy:

- `main` — production. Auto-deploys to cloud-prod on merge.
- `staging` — pre-production. Auto-deploys to cloud-staging on merge.
- Feature branches off `staging`; PR into `staging`; promote to `main` after a full market session of soak.

Two env templates ship with this repo:

- `.env.local.example` — local dev (plain values; copy to `.env`).
- `.env.cloud.example` — cloud (mostly Secrets Manager IDs; real values injected at runtime).

Full doc: [valgo-docs/branching.md](../valgo-docs/branching.md).

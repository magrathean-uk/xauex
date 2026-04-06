# Integrated upgrade notes

This package upgrades the original MiroFish -> XAUEX bridge in two practical ways:

1. **It adds a curated source registry and auto-context builder** so you no longer have to hand-write a market-news text file every run.
2. **It makes the bridge asset-aware** for `XAUUSD`, `WTI`, and `GBPJPY`, while keeping XAUEX execution honest and safe.

## What changed

### New bridge modules

- `bridge/assets.py` - asset profiles, aliases, prompt requirements and distance rules
- `bridge/source_registry.py` - curated source lists for gold, oil and GBPJPY
- `bridge/context_builder.py` - RSS and HTML ingestion into a normalized markdown dossier
- `bridge/market_oracle.py` - generalized simulation driver replacing the old gold-only assumption
- `bridge/export_sources.py` - writes JSON, CSV and Markdown source packs

### Updated bridge modules

- `bridge/run.py` - supports `--asset`, `--auto-context`, `--list-sources`, `--dump-context`
- `bridge/signal_parser.py` - emits schema v2 signals with `symbol`, `asset_class`, `distance_unit`, and legacy USD keys for gold
- `bridge/signal_writer.py` - writes schema version and preserves the kill switch
- `bridge/gold_oracle.py` - compatibility wrapper

### XAUEX patch

XAUEX now tolerates the new schema and reads:

- `stop_loss_distance` / `take_profit_distance` when legacy USD keys are absent
- `symbol` so it can skip unsupported assets safely

## Important execution boundary

The bridge can now **generate** signals for XAUUSD, WTI and GBPJPY, but the execution bot in this repo is still effectively **XAUUSD-first**.

That means:

- `XAUUSD` can move through the full chain today.
- `WTI` and `GBPJPY` can be sourced, simulated and parsed, but XAUEX should not auto-execute them until its symbol model, tick handling, contract specs and risk logic are generalized.

## Example commands

```bash
cd MiroFish
pip install -r bridge/requirements.txt

# auto-build context from the curated gold sources
DEEPSEEK_API_KEY=your-key python -m bridge.run --asset XAUUSD --auto-context --dry-run

# auto-build context for oil and inspect the normalized dossier
DEEPSEEK_API_KEY=your-key python -m bridge.run --asset WTI --auto-context --dump-context bridge/context_out/wti.md --dry-run

# list curated GBPJPY sources
python -m bridge.run --asset GBPJPY --list-sources

# export JSON/CSV/Markdown source packs from inside the repo
python -m bridge.export_sources --output-dir bridge/sourcepacks
```

## Recommended next refactor if you want real multi-asset execution

1. Move XAUEX from a single-symbol configuration to a `symbol_registry` with contract metadata per asset.
2. Replace the XAU-specific dollar-based sizing assumptions with asset-aware tick/pip logic.
3. Add per-symbol news blocks, session filters and spread limits.
4. Write broker adapters or config for `WTI` and `GBPJPY` instrument names as exposed by cTrader.
5. Backtest the new signal ingestion path before any live deployment.

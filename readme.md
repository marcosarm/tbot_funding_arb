# tbot_funding_arb

Strategy repository for funding + basis arbitrage.
This project consumes `btengine` as an external dependency (private repo).

## Repository boundaries

- Engine (private): `git@github.com:marcosarm/btengine.git`
- Strategy/app: `tbot_funding_arb`
- Rule: strategy code stays here (`funding/`), not inside `btengine`.

## Current strategy scope

- Pair trading: Perpetual vs Quarterly (for example `BTCUSDT` vs `BTCUSDT_260626`)
- Signal: basis z-score + projected funding filter
- Execution: maker-first, then IOC taker fallback with slippage cap
- Safety: liquidity gate, cooldown, legging hedge check, funding-flip exit
- Sampling: time-based windows (`Z_WINDOW`, `VOL_RATIO_WINDOW`) using `BASIS_SAMPLE_MS`

## Setup (Windows)

1. Install `btengine`:

```bash
pip install -e C:\4mti\Projetos\btengine
```

Or by Git (private):

```bash
pip install "git+ssh://git@github.com/marcosarm/btengine.git@<tag>"
```

2. Install this project:

```bash
pip install -e .
```

3. Configure S3 credentials:

- Copy `.env.example` to `.env`
- Fill required keys (`AWS_REGION`, `S3_BUCKET`, `S3_PREFIX`)

4. Run tests:

```bash
pytest -q
```

## Backtest command

Example: 3 days, hour 12 only, perp x quarterly:

```bash
python scripts\run_backtest_basis_funding.py ^
  --future-symbol BTCUSDT_260626 ^
  --start-day 2025-07-01 ^
  --days 3 ^
  --hours 12-12 ^
  --include-open-interest ^
  --include-liquidations ^
  --out-csv batch_basis_funding.csv
```

Notes:
- `--future-symbol` is required (`--perp-symbol` defaults to `BTCUSDT`)
- CLI defaults follow `SPECIFICATION.md`
- main execution realism flags:
  - `--maker-wait-sec`
  - `--max-slippage`
  - `--legging-check-delay-ms`
  - `--asof-tolerance-ms`
  - `--basis-sample-ms`

## Output

The batch script writes one CSV row per day with:
- event counts
- entries/exits by reason
- fills and round trips
- gross/net pnl, fees, max drawdown
- status (`OK`, `MISSING`, `ERROR`)

## References

- Strategy spec: `SPECIFICATION.md`
- Engine integration context: `BTENGINE_CONTEXT.md`
- Engine docs (local): `C:\4mti\Projetos\btengine\docs\btengine\README.md`

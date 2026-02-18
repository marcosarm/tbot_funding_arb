# tbot_funding_arb

Strategy repo for funding/basis arbitrage. Depends on the private `btengine` repo.

## Repos

- Engine (private): `git@github.com:marcosarm/btengine.git`
- Strategy/app: this repo (`tbot_funding_arb`)

Keep the boundary clean: do not place strategy code inside `btengine`.

## Setup

1) Install `btengine`:

```bash
pip install -e C:\4mti\Projetos\btengine
```

Or via Git:

```bash
pip install "git+ssh://git@github.com/marcosarm/btengine.git@<tag>"
```

2) Install this repo:

```bash
pip install -e .
```

3) Configure S3 access:

- Copy `.env.example` to `.env` and fill in the required keys.

4) Run tests:

```bash
pytest -q
```

## Run backtests (basis + funding)

Example (Perp x Quarterly), 3 days, hour 12 only:

```bash
python scripts\run_backtest_basis_funding.py \
  --future-symbol BTCUSDT_260626 \
  --start-day 2025-07-01 \
  --days 3 \
  --hours 12-12 \
  --include-open-interest \
  --include-liquidations \
  --out-csv batch_basis_funding.csv
```

Notes:
- `--future-symbol` is required. `--perp-symbol` defaults to `BTCUSDT`.
- Strategy knobs default to `SPECIFICATION.md` and can be overridden by CLI flags.

## References

- Strategy spec: `SPECIFICATION.md`
- Engine integration: `BTENGINE_CONTEXT.md`
- Engine docs: `C:\4mti\Projetos\btengine\docs\btengine\README.md`

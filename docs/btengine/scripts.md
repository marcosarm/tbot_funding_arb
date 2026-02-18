# Scripts (validacao e replay)

Os scripts vivem em `scripts/` e sao voltados para:

- inspecionar Parquets locais
- validar o dataset no S3 (existencia, schema, ranges)
- replayar dados no motor `btengine` para sanity-check

## `validate_s3_dataset.py`

Arquivo: `scripts/validate_s3_dataset.py`

Objetivo:

- checar se os arquivos do dia existem no S3
- inspecionar rapidamente: numero de linhas, row groups, schema Arrow, min/max de timestamps
- para orderbook: min/max de `final_update_id` e checagem simples de continuidade por hora

Exemplos:

```bash
# BTCUSDT (dia inteiro)
python scripts\\validate_s3_dataset.py --day 2025-07-01 --symbols BTCUSDT --hours 0-23

# Apenas a hora 12 do orderbook
python scripts\\validate_s3_dataset.py --day 2025-07-01 --symbols BTCUSDT --hours 12-12

# Multiplos simbolos (ex: perp + future)
python scripts\\validate_s3_dataset.py --day 2025-07-01 --symbols BTCUSDT,BTCUSDT_260626 --hours 12-12 --skip-missing
```

Notas:

- O script le `.env` por default do root (use `.env.example` como template).
- Ele nao imprime credenciais.
- `--skip-missing` trata `FileNotFoundError` como "MISSING" (nao falha o processo por arquivos ausentes).

## `run_backtest_replay.py`

Arquivo: `scripts/run_backtest_replay.py`

Objetivo:

- montar streams no S3 (por simbolo)
- fazer merge multi-simbolo
- rodar no `BacktestEngine` com uma estrategia no-op
- imprimir resumo (books, PnL, contagem de eventos)

Exemplo:

```bash
python scripts\\run_backtest_replay.py --day 2025-07-01 --symbols BTCUSDT --mark-price-symbols BTCUSDT --hours 12-12 --max-events 200000
```

O script:

- deriva uma janela `[start,end)` a partir de `--hours` (UTC)
- aplica a mesma janela em trades/orderbook/mark_price via `CryptoHftDayConfig.stream_start_ms/stream_end_ms`

Streams opcionais:

```bash
python scripts\\run_backtest_replay.py --day 2025-07-01 --symbols BTCUSDT --hours 12-12 --include-ticker --include-open-interest --include-liquidations
```

Se estiver explorando simbolos/dias com cobertura incompleta:

```bash
python scripts\\run_backtest_replay.py --day 2025-07-01 --symbols BTCUSDT,BTCUSDT_260626 --hours 12-12 --skip-missing
```

Knobs de execucao (para estrategias que submetem ordens):

- `--submit-latency-ms`
- `--cancel-latency-ms`
- `--maker-queue-ahead-factor`
- `--maker-queue-ahead-extra-qty`
- `--maker-trade-participation`

Knobs de dados (anti-lookahead):

- `--open-interest-delay-ms`: atrasa a disponibilidade do snapshot de open interest em relacao ao `timestamp` (ex: 5s, 30s).

## `run_backtest_entry_exit.py`

Arquivo: `scripts/run_backtest_entry_exit.py`

Objetivo:

- rodar um setup simples de entrada + saida (market) para gerar operacoes (fills)
- aferir lucro/prejuizo (PnL realizado) e fees
- imprimir estatisticas basicas:
  - round trips reconstruidos a partir de fills (wins/losses, net/gross, duracao)
  - curva de equity (PnL) amostrada em `mark_price` + max drawdown

Exemplo (BTCUSDT, 2h, 3 ciclos):

```bash
python scripts\\run_backtest_entry_exit.py --day 2025-07-01 --symbol BTCUSDT --hours 12-13 --direction long --qty 0.001 --enter-offset-s 30 --hold-s 60 --gap-s 60 --cycles 3 --out-fills-csv fills.csv --out-equity-csv equity.csv
```

## `run_backtest_ma_cross.py`

Arquivo: `scripts/run_backtest_ma_cross.py`

Objetivo:

- criar candles por timeframe (ex: 5m)
- calcular MA(N) e gerar sinais:
  - `rule=cross`: compra/vende apenas no cruzamento (price vs MA)
  - `rule=state`: fica long quando price>=MA e short quando price<MA
- executar ordens market (taker) para atingir o target (+qty / -qty / flat)
- imprimir PnL, fees, round trips e equity curve + max drawdown

Exemplo (BTCUSDT, MA9, candles 5m, usar `mark_price` como fonte de preco):

```bash
python scripts\\run_backtest_ma_cross.py --day 2025-07-01 --symbol BTCUSDT --hours 12-13 --tf-min 5 --ma-len 9 --price-source mark --rule cross --mode long_short --qty 0.001 --out-fills-csv fills.csv --out-equity-csv equity.csv
```

## `run_backtest_batch.py`

Arquivo: `scripts/run_backtest_batch.py`

Objetivo:

- rodar setup por varios dias (entry_exit ou ma_cross)
- validar temporalidade/continuidade por dia
- consolidar metricas e PnL em CSV
- opcionalmente ativar guard de book (`--strict-book`) para reduzir impacto de dados ruins

Exemplo (5 dias, MA9 5m, full day):

```bash
python scripts\\run_backtest_batch.py --start-day 2025-07-20 --days 5 --symbol BTCUSDT --hours 0-23 --setup ma_cross --tf-min 5 --ma-len 9 --price-source mark --rule cross --mode long_short --qty 0.001 --include-ticker --include-open-interest --include-liquidations --strict-book --out-csv batch_5d.csv
```

Knobs principais de guard:

- `--strict-book-max-spread`: spread absoluto maximo permitido
- `--strict-book-max-spread-bps`: spread maximo relativo ao mid (bps)
- `--strict-book-max-staleness-ms`: idade maxima do ultimo depth update
- `--strict-book-cooldown-ms`: bloqueio temporario apos trip
- `--strict-book-warmup-depth-updates`: bloqueio por N updates apos trip
- `--strict-book-reset-on-mismatch` / `--strict-book-no-reset-on-mismatch`
- `--strict-book-reset-on-crossed` / `--strict-book-no-reset-on-crossed`

Colunas relevantes no CSV:

- qualidade temporal: `out_of_order`, `duplicates`, `outside_window`
- continuidade: `final_id_nonmonotonic`, `prev_id_mismatch`
- sanidade do book: `book_crossed`, `book_missing_side`, `spread_*`
- guard: `strict_guard_*`, `strict_blocked_submits`, `strict_blocked_submit_reason`
- resultado: `round_trips`, `net_pnl_usdt`, `fees_usdt`, `max_drawdown_usdt`

## `run_backtest_basis_funding.py`

Arquivo: `scripts/run_backtest_basis_funding.py`

Objetivo:

- rodar backtest da estrategia de referencia de funding+basis (perp x future)
- processar batch multi-dia com duas pernas simultaneas
- consolidar sinais/execucao/risco basico em CSV por dia

Exemplo (1 dia, janela 12:00-13:00 UTC):

```bash
python scripts\\run_backtest_basis_funding.py --start-day 2026-02-01 --days 1 --hours 12-12 --perp-symbol BTCUSDT --future-symbol BTCUSDT_260626 --include-ticker --include-open-interest --include-liquidations --out-csv batch_basis_funding.csv
```

Principais parametros:

- par de symbols: `--perp-symbol`, `--future-symbol`
- regime/sinal: `--z-window`, `--vol-ratio-window`, `--z-exit-eps`, `--z-hard-stop`
- funding/financeiro: `--funding-threshold`, `--entry-safety-margin`, `--max-slippage`
- liquidez: `--impact-notional-usdt`, `--liquidity-min-ratio`, `--liquidity-depth-pct`
- operacao: `--entry-cooldown-sec`, `--hedge-eps-base`, `--no-reverse`

Colunas principais no CSV:

- entradas/saidas: `entries_standard`, `entries_reverse`, `exits_*`
- risco/execucao: `liquidity_rejects`, `hedge_actions`, `state_end`
- resultado: `net_pnl_usdt`, `fees_usdt`, `realized_pnl_usdt`, `max_drawdown_usdt`

## `analyze_replay_temporal.py`

Arquivo: `scripts/analyze_replay_temporal.py`

Objetivo:

- validar ordenacao temporal (por stream e no merge)
- checar se a janela `[start,end)` esta sendo respeitada
- checar continuidade do orderbook (`prev_final_update_id` vs `final_update_id`)
- sanity-check do book (spread e book crossed, amostrado a cada N updates)

Exemplo:

```bash
python scripts\\analyze_replay_temporal.py --day 2025-07-01 --symbols BTCUSDT --hours 12-12 --include-ticker --include-open-interest --include-liquidations --skip-missing --max-events 0 --book-check-every 5000
```

Se quiser simular atraso de publicacao de open interest:

```bash
python scripts\\analyze_replay_temporal.py --day 2025-07-01 --symbols BTCUSDT --hours 12-12 --include-open-interest --open-interest-delay-ms 5000
```

Opcionalmente, voce pode controlar a janela explicitamente:

```bash
python scripts\\run_backtest_replay.py --day 2025-07-01 --symbols BTCUSDT --hours 0-23 --start-utc 2025-07-01T12:00:00Z --end-utc 2025-07-01T13:00:00Z
```

## `inspect_orderbook_parquet.py` (local)

Arquivo: `scripts/inspect_orderbook_parquet.py`

Objetivo:

- imprimir schema/metadados do parquet
- checar range de `event_time`
- checar monotonicidade (event_time, final_update_id)
- estatistica de rows por mensagem (group by `final_update_id`)
- continuidade basica via `prev_final_update_id`

Exemplo:

```bash
python scripts\\inspect_orderbook_parquet.py C:\\Users\\marco\\Downloads\\orderbook_00.parquet
```

## `replay_orderbook.py` (local)

Arquivo: `scripts/replay_orderbook.py`

Objetivo:

- aplicar deltas L2 no `L2Book`
- imprimir snapshots periodicos (best bid/ask, mid, impact VWAP)

Exemplo:

```bash
python scripts\\replay_orderbook.py C:\\Users\\marco\\Downloads\\orderbook_00.parquet --max-messages 2000
```

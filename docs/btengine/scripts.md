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

- O script le `.env` por default do root.
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

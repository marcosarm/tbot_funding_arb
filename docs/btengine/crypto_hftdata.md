# Adapter CryptoHFTData (S3 + Parquet)

## Layout (paths)

O dataset segue particionamento por dia (UTC) e, para orderbook, por hora:

- Trades: `trades/{exchange}/{symbol}/YYYY/MM/DD/trades.parquet`
- Mark price: `mark_price/{exchange}/{symbol}/YYYY/MM/DD/mark_price.parquet`
- Orderbook: `orderbook/{exchange}/{symbol}/YYYY/MM/DD/orderbook_{HH}.parquet`
- Ticker: `ticker/{exchange}/{symbol}/YYYY/MM/DD/ticker.parquet`
- Open interest: `open_interest/{exchange}/{symbol}/YYYY/MM/DD/open_interest.parquet`
- Liquidations: `liquidations/{exchange}/{symbol}/YYYY/MM/DD/liquidations.parquet`

No codigo:

- builder de paths: `btengine.data.cryptohftdata.CryptoHftLayout` (`src/btengine/data/cryptohftdata/paths.py`)
- leitura/replay: `btengine.data.cryptohftdata.build_day_stream` (`src/btengine/data/cryptohftdata/replay.py`)
  - streams adicionais: `iter_ticker*`, `iter_open_interest*`, `iter_liquidations*`

## Conectando no S3 via PyArrow

O adapter usa `pyarrow.fs.S3FileSystem`. Para criar:

- `btengine.data.cryptohftdata.S3Config`
- `btengine.data.cryptohftdata.make_s3_filesystem`

Os scripts do repo leem um `.env` via `btengine.util.load_dotenv` e montam o filesystem com:

- `AWS_REGION`
- `S3_BUCKET`
- `S3_PREFIX`
- opcionais: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`

Exemplo de uso (simplificado):

```python
from btengine.data.cryptohftdata import CryptoHftLayout, S3Config, make_s3_filesystem

fs = make_s3_filesystem(S3Config(region="ap-northeast-1"))
layout = CryptoHftLayout(bucket="amzn-tdata", prefix="hftdata")
```

## Schemas (resumo)

Os Parquets reais podem ter colunas extras, mas o adapter espera (baseado nas amostras em `cryptohftdata_amostras.md`):

Trades (`trades.parquet`):

- `received_time` (int64, ns)
- `event_time` (int64, ms) (na pratica pode existir, mas o adapter usa `trade_time` como tempo canonico)
- `trade_time` (int64, ms)
- `symbol` (string)
- `trade_id` (int64)
- `price` (string/float) -> e convertido para `float64`
- `quantity` (string/float) -> e convertido para `float64`
- `is_buyer_maker` (bool)

Orderbook (`orderbook_{HH}.parquet`, representacao flattened):

- `received_time` (int64, ns)
- `event_time` (int64, ms)
- `transaction_time` (int64, ms)
- `symbol` (string)
- `first_update_id` (int64)
- `final_update_id` (int64)
- `prev_final_update_id` (int64)
- `side` ("bid"/"ask")
- `price` (string) -> cast float64
- `quantity` (string) -> cast float64

Mark price (`mark_price.parquet`):

- `received_time` (int64, ns)
- `event_time` (int64, ms)
- `symbol` (string)
- `mark_price` (string/float) -> cast float64
- `index_price` (string/float) -> cast float64
- `funding_rate` (string/float) -> cast float64
- `next_funding_time` (int64, ms)

## Ordenacao e "interleaving" (cuidado importante)

Na pratica, nem sempre os Parquets estao armazenados fisicamente ordenados pelo tempo.

Consequencias:

- orderbook pode ter linhas de um mesmo `final_update_id` intercaladas com outros `final_update_id`
- trades podem nao estar monotonicos em `trade_time`

O adapter faz:

- orderbook: detecta (heuristica) e, se necessario, ordena por `final_update_id` para reconstruir mensagens coerentes (`iter_depth_updates`)
- trades: detecta e, se necessario, ordena por `trade_time` (`iter_trades`)

Tradeoff:

- ordenar costuma exigir ler o arquivo inteiro na memoria (por hora, no caso do orderbook)
- isso e mais custoso, mas evita bugs de replay (mensagens quebradas / viagem no tempo)

## Horas faltantes (orderbook)

Nem todo dia tem as 24 horas de `orderbook_{HH}.parquet`.

Para replay robusto:

- use `CryptoHftDayConfig.orderbook_skip_missing=True` (default)
- restrinja `orderbook_hours` para janelas que voce sabe que existem, quando for debug

## Open interest (arquivo parcial)

Em alguns dias, o open interest pode aparecer como `open_interest_parcial.parquet` (multi-dia).
O adapter `iter_open_interest_for_day(...)`:

- tenta `open_interest.parquet` e faz fallback para `open_interest_parcial.parquet` se necessario
- filtra as linhas para o dia solicitado (UTC) com base na coluna `timestamp`
- ordena por `timestamp` apos filtrar

Para evitar lookahead, voce pode atrasar a disponibilidade do snapshot via `CryptoHftDayConfig.open_interest_delay_ms` (ver `docs/btengine/scripts.md`).

## Validacao do dataset

O repo inclui `scripts/validate_s3_dataset.py` para checar existencia, schema, contagem de linhas e range de timestamps.

Ver: `docs/btengine/scripts.md`

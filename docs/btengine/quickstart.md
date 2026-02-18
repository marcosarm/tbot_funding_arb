# Quickstart

## 1) Instalar dependencias

No ambiente virtual do projeto:

```bash
pip install -e .
pip install -e ".[dev]"
```

Rodar testes:

```bash
pytest -q
```

## 1b) Consumir o btengine em outro repositorio

No `pyproject.toml` do projeto consumidor, voce pode instalar por Git pinado:

```bash
pip install "git+https://github.com/marcosarm/btengine.git@<commit-ou-tag>"
```

Durante desenvolvimento local (duas pastas lado a lado), use editable install:

```bash
pip install -e C:\\caminho\\btengine
```

No projeto `tbot_funding_arb`, instale o pacote de estratégia separadamente:

```bash
pip install -e C:\\caminho\\tbot_funding_arb
```

Guia completo de reuso:

- `docs/btengine/reuse_in_other_projects.md`

## 2) Configurar acesso ao S3 (CryptoHFTData)

Use `.env.example` como template e crie um `.env` local.

O tooling de scripts usa um `.env` com chaves como:

```dotenv
AWS_REGION=ap-northeast-1
S3_BUCKET=amzn-tdata
S3_PREFIX=hftdata

# Opcional (se nao usar IAM role/profile):
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

Notas:

- Nao commitar `.env` com segredos.
- O Arrow/AWS SDK suportam a cadeia padrao de credenciais; usar `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` e opcional.

## 3) Validar rapidamente o dataset (S3)

Exemplo: validar `BTCUSDT` no dia `2025-07-01` (UTC) e apenas a hora 12 do orderbook:

```bash
python scripts\\validate_s3_dataset.py --day 2025-07-01 --symbols BTCUSDT --hours 12-12
```

## 4) Replay de dados no motor (sem estrategia)

Exemplo: replay de uma janela (hora 12) e resumo do estado do book/portfolio:

```bash
python scripts\\run_backtest_replay.py --day 2025-07-01 --symbols BTCUSDT --mark-price-symbols BTCUSDT --hours 12-12 --max-events 200000 --include-ticker --include-open-interest --include-liquidations
```

O script deriva automaticamente uma janela `[start,end)` a partir de `--hours` e fatia trades/mark_price/orderbook para a mesma janela.

## 4b) Setup simples com entrada + saida (gera PnL)

Exemplo: 1 round-trip (entrada 30s apos inicio, segura 60s e sai):

```bash
python scripts\\run_backtest_entry_exit.py --day 2025-07-01 --symbol BTCUSDT --hours 12-12 --direction long --qty 0.001 --enter-offset-s 30 --hold-s 60
```

## 4c) Setup simples com MA(9) em candles 5m

Exemplo: price vs MA9 (compra/vende no cruzamento) usando `mark_price` como fonte de preco:

```bash
python scripts\\run_backtest_ma_cross.py --day 2025-07-01 --symbol BTCUSDT --hours 12-13 --tf-min 5 --ma-len 9 --price-source mark --rule cross --mode long_short --qty 0.001
```

## 4d) Batch multi-dia com validacao temporal e guard de book

Para validar mais de um dia e reduzir impacto de horarios com book degradado:

```bash
python scripts\\run_backtest_batch.py --start-day 2025-07-20 --days 5 --symbol BTCUSDT --hours 0-23 --setup ma_cross --tf-min 5 --ma-len 9 --price-source mark --rule cross --mode long_short --qty 0.001 --include-ticker --include-open-interest --include-liquidations --strict-book --out-csv batch_5d.csv
```

Opcoes uteis do strict guard:

- `--strict-book-max-spread` (absoluto)
- `--strict-book-max-spread-bps` (relativo ao mid)
- `--strict-book-max-staleness-ms`
- `--strict-book-cooldown-ms`
- `--strict-book-warmup-depth-updates`

## 4e) Basis + Funding (Perp x Trimestral)

Exemplo de batch dedicado para estrategia de referencia de funding+basis:

```bash
python scripts\\run_backtest_basis_funding.py --start-day 2026-02-01 --days 1 --hours 12-12 --perp-symbol BTCUSDT --future-symbol BTCUSDT_260626 --include-ticker --include-open-interest --include-liquidations --out-csv batch_basis_funding.csv
```

## 5) Rodar um backtest via codigo (exemplo minimo)

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from btengine.broker import SimBroker
from btengine.data.cryptohftdata import (
    CryptoHftDayConfig,
    CryptoHftLayout,
    S3Config,
    build_day_stream,
    make_s3_filesystem,
)
from btengine.engine import BacktestEngine, EngineConfig, EngineContext
from btengine.execution.orders import Order
from btengine.types import DepthUpdate, MarkPrice, Trade


def day_start_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


@dataclass
class DemoStrategy:
    symbol: str
    did_submit: bool = False

    def on_event(self, event: DepthUpdate | Trade | MarkPrice, ctx: EngineContext) -> None:
        # Exemplo: assim que tivermos book, envia uma ordem market (taker).
        if self.did_submit:
            return
        book = ctx.books.get(self.symbol)
        if book is None:
            return

        ctx.broker.submit(
            Order(id="mkt1", symbol=self.symbol, side="buy", order_type="market", quantity=0.001),
            book,
            now_ms=ctx.now_ms,
        )
        self.did_submit = True


def main() -> None:
    bucket = "amzn-tdata"
    prefix = "hftdata"
    fs = make_s3_filesystem(S3Config(region="ap-northeast-1"))
    layout = CryptoHftLayout(bucket=bucket, prefix=prefix)

    d = date(2025, 7, 1)
    start = day_start_ms(d) + 12 * 3_600_000
    end = start + 3_600_000

    cfg = CryptoHftDayConfig(
        exchange="binance_futures",
        include_orderbook=True,
        include_trades=True,
        include_mark_price=True,
        orderbook_hours=range(12, 13),
        orderbook_skip_missing=True,
        stream_start_ms=start,
        stream_end_ms=end,
    )

    events = build_day_stream(layout, cfg=cfg, symbol="BTCUSDT", day=d, filesystem=fs)

    broker = SimBroker()
    engine = BacktestEngine(config=EngineConfig(tick_interval_ms=1000), broker=broker)
    res = engine.run(events, strategy=DemoStrategy(symbol="BTCUSDT"))

    print("fills:", len(res.ctx.broker.fills))
    print("realized_pnl_usdt:", res.ctx.broker.portfolio.realized_pnl_usdt)


if __name__ == "__main__":
    main()
```

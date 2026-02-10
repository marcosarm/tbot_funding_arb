# Conceitos centrais (eventos, tempo, streams)

## Eventos

O `btengine` trabalha com um stream unico de eventos por simbolo (ou multi-simbolo), onde cada evento tem `event_time_ms` (epoch ms, UTC) e tipos principais:

- `DepthUpdate`: delta L2 (bids/asks) agregado por `final_update_id`
- `Trade`: negocio individual (trade tape)
- `MarkPrice`: mark/index/funding_rate (tipicamente 1Hz)
- `Ticker`: ticker agregado (estatisticas tipo 24h rolling window)
- `OpenInterest`: snapshots de open interest (baixa frequencia)
- `Liquidation`: eventos de liquidacao (force orders)

Definicoes (ver `src/btengine/types.py`):

- `received_time_ns`: quando o dado foi recebido/ingestado (ns)
- `event_time_ms`: timestamp do evento na exchange (ms) e o "tempo" do backtest
- `transaction_time_ms` (depth): timestamp adicional (ms), quando disponivel
- `trade_time_ms` (trade): timestamp do trade (ms). No adapter CryptoHFTData, ele vira o `event_time_ms` canonico do Trade
- `next_funding_time_ms` (mark_price): proximo horario de funding (ms)
- `timestamp_ms` (open_interest): timestamp medido do snapshot (ms). `event_time_ms` pode ser igual ou `timestamp_ms + delay` dependendo da configuracao.

No `EngineContext`, os ultimos valores por simbolo sao mantidos em:

- `ctx.mark[symbol] -> MarkPrice`
- `ctx.ticker[symbol] -> Ticker`
- `ctx.open_interest[symbol] -> OpenInterest`
- `ctx.liquidation[symbol] -> Liquidation` (ultimo evento)

## Stream ordering e merge

`btengine.replay.merge_event_streams()` faz um k-way merge de multiplos streams assumindo que cada um esta ordenado por `event_time_ms`.

No adapter CryptoHFTData:

- orderbook: pode precisar ordenar por `final_update_id` para reconstruir a ordem correta do stream (ver `iter_depth_updates`)
- trades: pode precisar ordenar por `trade_time`
- mark_price: assume-se que ja vem ordenado por `event_time`

Se um stream nao estiver ordenado, o merge pode produzir "viagem no tempo" e quebrar invariantes do motor.

## Janelas de tempo (stream vs trading)

Existem dois conceitos de janela:

1) Janela do stream (dados que entram no motor)

- `btengine.data.cryptohftdata.CryptoHftDayConfig.stream_start_ms`
- `btengine.data.cryptohftdata.CryptoHftDayConfig.stream_end_ms`

Quando setados, o `build_day_stream()` fatia trades/orderbook/mark_price antes de fazer o merge (bom para focar em horas especificas).

2) Janela de trading (quando a estrategia pode operar)

- `btengine.engine.EngineConfig.trading_start_ms`
- `btengine.engine.EngineConfig.trading_end_ms`
- `EngineContext.is_trading_time()`

Um padrao comum e:

- alimentar o motor com um periodo de warmup (para construir book/indicadores)
- habilitar operacao apenas depois de `trading_start_ms`

## Ticks (clock discreto)

`EngineConfig.tick_interval_ms` ativa ticks discretos. O engine:

- ancora o primeiro tick no primeiro evento observado
- chama `strategy.on_tick(tick_ms, ctx)` em grade fixa ate o timestamp do evento atual

Isso e util para estrategias que rodam logica periodica (ex: a cada 1s) sem depender de cada evento.

## Ordem de processamento no Engine

No `BacktestEngine.run()` (`src/btengine/engine.py`):

1) atualiza `ctx.now_ms = event.event_time_ms`
2) dispara ticks ate `now_ms` (se habilitado)
3) aplica o evento ao estado:
   - `DepthUpdate`: atualiza book e permite progressao de ordens maker
   - `Trade`: permite fills maker via trade tape
   - `MarkPrice`: atualiza ultimo mark e aplica funding se devido
4) chama `strategy.on_event(event, ctx)` (se existir)

Isso significa que dentro de `on_event` o `ctx.books[symbol]` ja reflete o evento aplicado.

# Modelo de execucao e fills (aproximado)

## Book: `L2Book`

Implementacao: `src/btengine/marketdata/orderbook.py`

Caracteristicas:

- armazena bids/asks em `dict[price] -> qty`
- mantem heaps internas para `best_bid()` / `best_ask()` sem precisar ordenar sempre
- aplica deltas via `apply_depth_update(bid_updates, ask_updates)`

Funcoes importantes:

- `best_bid()`, `best_ask()`, `mid_price()`
- `impact_vwap(side, target_notional)`:
  - calcula VWAP de consumo de liquidez ate atingir um notional alvo
  - retorna `NaN` se profundidade insuficiente

## Ordens: `Order`

Implementacao: `src/btengine/execution/orders.py`

Campos:

- `order_type`: `"market"` ou `"limit"`
- `time_in_force`: `"GTC"` ou `"IOC"` (para limit)
- `post_only`: se `True`, trata como maker (nao cruza spread)

## Semantica de ordens (o que o backtest faz)

O `SimBroker` trata ordens de forma "realista o suficiente" para backtest, mas ainda aproximada.
Entrada e saida usam a mesma logica: muda apenas o `side` (`buy`/`sell`).

Regras principais:

- `market`:
  - executa como taker consumindo o `L2Book` (VWAP) ate preencher a quantidade ou acabar a profundidade
  - pode preencher parcialmente
- `limit` + `IOC`:
  - executa como taker ate o `limit_price` (nao cruza pior que o limite)
  - pode preencher parcialmente (restante e descartado)
- `limit` + `GTC` (nao post-only):
  - se o preco cruza o spread no momento da ativacao, executa como taker ate o `limit_price`
  - se nao cruza, vira maker (ordem resting) e pode preencher via modelo de fila
  - se cruzar e preencher parcialmente, o restante vira uma ordem resting (maker) no `limit_price` (GTC)
- `post_only=True`:
  - se cruzaria o spread, a ordem e rejeitada (nao entra)
  - caso contrario, vira maker e pode preencher via modelo de fila

Observacao:

- A decisao de "cruzar o spread" usa `best_bid()`/`best_ask()` do book atual. Se o book estiver incompleto (sem bid/ask), a estrategia deve evitar enviar ordens ate o book estar formado.

## Broker simulado: `SimBroker`

Implementacao: `src/btengine/broker.py`

Responsabilidades:

- manter `Portfolio` (posicoes, PnL e fees)
- simular fills:
  - taker fill (market / IOC): consome o book L2
  - maker fill (post-only / GTC): modelo de fila aproximado + trade tape
- registrar `fills` (lista de `Fill`)

Taxas:

- `maker_fee_frac` (default `0.0004`)
- `taker_fee_frac` (default `0.0005`)

Realismo (parametros):

- `submit_latency_ms`: delay para ativar ordens apos `submit()`
- `cancel_latency_ms`: delay para aplicar `cancel()`
- `maker_queue_ahead_factor` / `maker_queue_ahead_extra_qty`: tornam o maker mais conservador (assume mais fila a frente)
- `maker_trade_participation`: fator em `(0,1]` para creditar apenas parte do volume de trades no nivel (conservador)

Notas de latencia:

- ordens atrasadas so "entram" quando `SimBroker.on_time(now_ms)` e chamado; o `BacktestEngine` chama isso automaticamente em ticks/eventos

Limites gerais (importantes):

- self-impact e aplicado: ao executar como taker, o book tem sua profundidade reduzida pela nossa execucao (in-memory)
- nossas ordens maker nao entram no book (nao alteram `best_bid/best_ask` nem competem explicitamente na microestrutura)
- `limit GTC` que cruza e preenche parcial mantem automaticamente o restante como ordem resting (ver acima)

### Taker fill (market/IOC)

Implementacao: `src/btengine/execution/taker.py`

Regra:

- BUY consome asks do menor preco ao maior
- SELL consome bids do maior preco ao menor
- `limit_price` (IOC) impede cruzar um preco pior que o limite

Retorno:

- `(avg_price, filled_qty)`; se nao preenche, retorna `(NaN, 0.0)`
- O broker usa `consume_taker_fill(...)` (mutante, com self-impact). A funcao `simulate_taker_fill(...)` existe como versao pura (nao muta o book).

### Maker fill (post-only / GTC)

Implementacao: `src/btengine/execution/queue_model.py` (`MakerQueueOrder`)

Modelo (aproximacao):

1) Ao abrir a ordem, estima-se `queue_ahead_qty` como a quantidade visivel no nivel do book do nosso lado.
2) A fila "anda" quando:
   - trades ocorrem exatamente no mesmo preco, agredindo nosso lado, ou
   - a quantidade visivel no nivel diminui (cancels/executions)
3) Aumentos de quantidade visivel nao aumentam `queue_ahead_qty` (assume que novos entram atras de nos).

Como o motor progride isso:

- `SimBroker.on_depth_update(...)` chama `MakerQueueOrder.on_book_qty_update(...)` quando o nivel e tocado
- `SimBroker.on_trade(...)` chama `MakerQueueOrder.on_trade(trade)` e aplica fills quando a fila foi consumida

Limites do modelo:

- modela delays deterministicas de submit/cancel, mas nao jitter/ack/rejects parciais como numa exchange real
- nao modela prioridade por "age" real (somente queue ahead aproximada)
- depende de trades no mesmo preco (se o dataset nao tiver granularidade suficiente, fills maker podem ficar subestimados)
- ordens `post_only` que cruzariam o spread sao rejeitadas (nao entram no book)

## Portfolio e PnL

Implementacao: `src/btengine/portfolio.py`

O `Portfolio` rastreia:

- `positions[symbol] -> Position(qty, avg_price)` (qty em base; +long / -short)
- `realized_pnl_usdt`
- `fees_paid_usdt`

`apply_fill(...)`:

- atualiza posicao e realiza PnL quando reduz/fecha/flipa direcao
- subtrai fee do PnL

### Funding (perpetuos)

Funding e aplicado no engine via `EngineContext.apply_funding_if_due(MarkPrice)` (`src/btengine/engine.py`).

Semantica adotada:

- funding PnL = `-(qty * mark_price) * funding_rate`
- funding positivo: longs pagam, shorts recebem

O engine aplica funding uma unica vez por `next_funding_time_ms` por simbolo (primeiro evento mark_price em/apos o timestamp).

## Realismo: o que falta (roadmap)

Para aproximar mais o comportamento real:

- overlay de nossas ordens no book (para afetar best bid/ask e competir por fila)
- slippage estocastico adicional
- modelo de spread/impact dinamico (alem do L2 nominal)
- modelagem de "partial fills" maker ao longo do tempo (nao apenas via trade tape no preco exato)
- limites de rate limit e reconexao (mais relevante para live/paper)

O `docs/btengine/implementation_plan.md` descreve um plano por fases.

# Plano de implementacao por fases (com testes de conclusao)

Este plano assume que o objetivo e evoluir `btengine` para um motor de backtest reutilizavel, com simulacao "realista o suficiente" para estrategias baseadas em microestrutura (orderbook + trades + funding).

Cada fase termina com um conjunto de testes/criterios objetivos.

## Fase 0: Higiene e baseline (1-2 dias)

Escopo:

- garantir que a biblioteca e importavel e testavel em CI
- garantir que a documentacao e navegavel

Tarefas:

- (opcional, recomendado) criar `.env.example` com placeholders e remover `.env` do versionamento (manter apenas local)
- adicionar um workflow CI simples (`pytest -q`) (GitHub Actions ou similar)
- revisar `readme.md` para linkar a doc de `docs/btengine/README.md`

Teste de conclusao:

- `pip install -e . && pytest -q` passa em maquina limpa
- repositorio nao contem segredos versionados (checagem manual + `.gitignore` ok)

## Fase 1: Data layer robusta (CryptoHFTData) (2-5 dias)

Escopo:

- leitura confiavel de Parquets (S3/local)
- ordering consistente para replay (sem "viagem no tempo")
- janelas de tempo por hora/dia

Tarefas:

- manter `iter_depth_updates`/`iter_trades` com auto-sort quando necessario
- expor configuracao explicita de janela:
  - `CryptoHftDayConfig.stream_start_ms/stream_end_ms` (ja existe)
  - garantir que scripts usem isso por padrao (ja existe em `run_backtest_replay.py`)
- melhorar validacao de schema (tipos esperados e colunas obrigatorias)
- tooling para listar "cobertura" do dataset (dias/horas existentes por simbolo)

Teste de conclusao:

- `python scripts\\validate_s3_dataset.py --day 2025-07-01 --symbols BTCUSDT --hours 12-12` retorna `OK`
- `python scripts\\run_backtest_replay.py --day 2025-07-01 --symbols BTCUSDT --hours 12-12 --max-events 200000` imprime book com `best_bid` e `best_ask` nao-nulos
- teste unitario para `slice_event_stream` e merge de streams passa (`pytest -q`)

## Fase 2: Book e medidas microestruturais (3-7 dias)

Escopo:

- consolidar o L2Book como base do motor
- garantir corretude de best bid/ask e impacto VWAP

Tarefas:

- adicionar testes de regressao para:
  - deletar nivel (qty=0) e garantir que best bid/ask se atualiza
  - book vazio e `mid_price() -> None`
  - `impact_vwap()` em cenarios com profundidade insuficiente (NaN)
- (performance) avaliar representacao alternativa (arrays/numba) se necessario
- (opcional) suportar snapshot inicial (se dataset suportar), para reduzir warmup

Teste de conclusao:

- suite de testes de `L2Book` cobre deletar/atualizar niveis e impacto VWAP
- benchmark simples (script) replaya 1h de orderbook sem degradar (tempo aceitavel para seu hardware)

## Fase 3: Execucao mais realista (maker/taker) (5-15 dias)

Escopo:

- aproximar fills e custos de forma defensiva

Tarefas:

- manter taker fill por consumo do L2 e respeitar `limit_price` (ja existe)
- evoluir modelo maker:
  - permitir parametrizar `queue_ahead_qty` (ex: fracao do nivel vs inteiro)
  - modelar "queue reset" quando o nivel muda drasticamente (cancels/refresh)
  - opcional: probabilidade de fill quando ha trades no nivel (para reduzir vies)
- adicionar slippage extra (alem do L2 nominal) como modelo pluggable (ex: bps fixo, ou funcao do notional)
- adicionar latencia simples (delay em ms) para submit/cancel (modelo deterministico primeiro)

Teste de conclusao:

- testes unitarios:
  - maker nao preenche com trades do lado errado (semantica `is_buyer_maker`)
  - IOC nao cruza limite
  - fees sao aplicadas e batem com expected
- teste de integracao pequeno:
  - roda 10 minutos de dados e nao gera quantidades negativas impossiveis no book/portfolio

## Fase 4: Engine/Strategy API e metricas (3-10 dias)

Escopo:

- facilitar reuso do motor em outros projetos

Tarefas:

- definir uma interface de estrategia "recomendada" (exemplo) e padronizar callbacks
- adicionar um coletor de metricas (PnL por trade, exposicao, turnover, slippage estimada)
- suportar multi-simbolo com utilitarios para "asof join" de mark_price e sincronizacao de clocks

Teste de conclusao:

- exemplo multi-simbolo roda (perp + future) com merge de streams
- metricas basicas sao geradas sem excecoes (ex: por janela de 1h)

## Fase 5: Backtest completo (nao so orderbook) + estrategia exemplo (opcional) (5-20 dias)

Escopo:

- validar o motor com uma estrategia end-to-end (ex: basis + funding)

Tarefas:

- implementar uma estrategia "reference" minimal:
  - calcula mid/impact em ambos simbolos
  - gera sinais simples e executa com guardrails
- adicionar dataset extra (ticker/open_interest/liquidations) como features (opcional)

Teste de conclusao:

- roda 1 dia inteiro (ou 1 semana) em batch sem memory leak/excecoes
- gera um relatorio simples:
  - PnL/fees/funding
  - numero de trades
  - drawdown max


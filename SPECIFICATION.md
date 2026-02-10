# 📘 SPECIFICATION.md: Sistema de Arbitragem de Funding & Basis (Grand Master Edition)

**Versão:** 1.5.0 (Implementation Ready)
**Estado:** Desenvolvimento / Congelado para Codificação
**Data:** Fevereiro 2026
**Autor:** Marcosarm / Gemini Architect

---

## 1. Visão Geral do Sistema

O sistema é um robô de trading quantitativo de alta frequência (HFT/Mid-frequency) para **Binance Futures (USDT-Margined)**. A estratégia é **Delta Neutral Bi-Direcional**, arbitrando a curva de futuros e capturando Funding Rate, com adaptação dinâmica à volatilidade e proteção de microestrutura.

### 1.1 Objetivos Core
1.  **Arbitragem Estatística:** Identificar anomalias no *Basis Teórico* (Mid-Price) e executar no *Basis Real* (Impact Price).
2.  **Funding Extraction (Carry Trade):**
    * **Standard Carry:** Short Perp / Long Futuro (Ganha Funding Positivo).
    * **Reverse Carry:** Long Perp / Short Futuro (Ganha Funding Negativo).
3.  **Segurança:** Operar alavancado (3x) sob Margem de Portfólio, com execução atômica e verificação profunda de liquidez.

---

## 2. Parâmetros Globais e Constantes (Env Config)

### 2.1 Convenções (Unidades e Representação)

- Taxas/percentuais são representados como **fração** (ex: `0.0001` = `0.01%` = 1 bp).
- `price`: USDT por unidade do ativo base (ex: BTC).
- `qty`: unidade do ativo base (ex: BTC).
- `notional`: USDT.

| Parâmetro | Valor Padrão | Unidade/Representação | Descrição |
| :--- | :--- | :--- | :--- |
| `AWS_REGION` | `ap-northeast-1` | - | Tóquio (Latência < 10ms). |
| `IMPACT_NOTIONAL` | `25000` | USDT | Notional alvo para cálculo do Impact VWAP (e tamanho padrão de ordem, por perna, se não houver sizing dinâmico). |
| `FUNDING_THRESHOLD` | `0.0001` | fração (0.01%) | Funding mínimo (módulo) para autorizar entrada no modo correspondente. |
| `MAX_SLIPPAGE` | `0.0005` | fração (5 bps = 0.05%) | Slippage máximo aceitável ao completar como Taker (IOC). |
| `ENTRY_SAFETY_MARGIN` | `0.0002` | fração (2 bps = 0.02%) | Buffer adicional para cobrir erro de modelo/spread/latência ao validar gatilho financeiro. |
| `LIQUIDITY_MIN_RATIO` | `5.0` | x | Multiplicador sobre o tamanho da ordem para aprovar liquidez. |
| `LIQUIDITY_DEPTH_PCT` | `0.001` | fração (0.1%) | Profundidade relativa (em torno do Mid) para cálculo do Score de Liquidez. |
| `Z_WINDOW` | `1440` | min | Janela de lookback para Média/Desvio e Z-Score (24h), em amostras por minuto. |
| `Z_EXIT_EPS` | `0.2` | - | Tolerância para considerar convergência (`abs(Z) <= Z_EXIT_EPS`). |
| `Z_HARD_STOP` | `4.0` | - | Hard stop por evento extremo (`abs(Z) >= Z_HARD_STOP`). |
| `VOL_RATIO_WINDOW` | `60` | min | Janela curta (min) para cálculo de volatilidade relativa. |
| `ASOF_TOLERANCE_MS` | `100` | ms | Tolerância do ASOF JOIN entre Orderbook e Mark Price. |
| `MAKER_WAIT_SEC` | `5` | s | Tempo máximo tentando Maker antes do fallback para Taker. |
| `ENTRY_COOLDOWN_SEC` | `30` | s | Cooldown após rejeição por liquidez/erro operacional para evitar overtrading. |
| `LEGGING_CHECK_DELAY_MS` | `200` | ms | Delay após envio das pernas antes da reconciliação de posições (legging). |
| `HEDGE_EPS_BASE` | `0.001` | base (ex: BTC) | Tolerância máxima de desbalanceamento entre pernas antes de acionar hedge de emergência. |
| `WS_MAX_PROCESSING_LATENCY_MS` | `50` | ms | Latência máxima de processamento (local) antes de reiniciar o pipeline/WS. |
| `WS_LAST_MSG_TIMEOUT_MS` | `5000` | ms | Timeout sem mensagens de WS antes de entrar em modo de segurança. |
| `KILL_SWITCH_DRAWDOWN_FRAC` | `0.03` | fração (3%) | Drawdown diário máximo antes do kill switch global. |
| `RATE_LIMIT_SOFT_WEIGHT_PER_MIN` | `1200` | weight/min | Soft limit local para evitar ban (limite Binance maior). |
| `FEE_MAKER_FRAC` | `0.0004` | fração (0.04%) | Taxa de Maker usada em simulação/backtest e buffers financeiros. |
| `FEE_TAKER_FRAC` | `0.0005` | fração (0.05%) | Taxa de Taker usada em simulação/backtest e buffers financeiros. |

---

## 3. Arquitetura e Infraestrutura

### 3.1 Infraestrutura AWS
* **Tipo de Instância:** `c5.large` ou `c6i.large` (Compute Optimized).
* **Sistema Operativo:** Amazon Linux 2023 ou Ubuntu 22.04 LTS.
* **Rede:**
    * **Enhanced Networking (ENA):** Ativado obrigatoriamente.
    * **Elastic IP:** Associado para whitelisting na Binance.
* **Relógio:** Serviço `chrony` configurado com pool da AWS (`169.254.169.123`) para precisão de microssegundos.

### 3.2 Stack de Software
* **Linguagem:** Python 3.10+.
* **Core Libs:** `ccxt` (Pro/Async), `pandas`, `numpy`, `pyarrow`, `boto3`.
* **Process Manager:** `systemd` (para auto-restart e logs via journalctl).

---

## 4. Engenharia de Dados (Input)

### 4.1 Tratamento de Dados S3 (Backtest)
* **Bucket:** `s3://amzn-tdata`
* **Prefixo:** `hftdata`
* **Formato:** Parquet (Snappy/Zstd).

**Mapeamento e Normalização:**
1.  **Orderbook (`orderbook_{HH}.parquet`):**
    * Schema: `received_time` (int64, epoch ms), `bids` (list<list<float>>), `asks` (list<list<float>>).
    * *Ação:* Flattening dos arrays para cálculo vetorial ou iteração rápida.
2.  **Mark Price (`mark_price.parquet`):**
    * Colunas: `index_price`, `funding_rate`, `next_funding_time`.
    * *Sync:* Realizar "ASOF JOIN" (merge by nearest timestamp) com o Orderbook, tolerância de `ASOF_TOLERANCE_MS`.

### 4.2 Dados em Tempo Real (Live)
* **Conexão:** `ccxt.pro` (Async WebSocket).
* **Streams Obrigatórios:**
    1.  `btcusdt@depth20@100ms`: Orderbook top 20 (Perp).
    2.  `btcusdt_260626@depth20@100ms`: Orderbook top 20 (Futuro Trimestral, exemplo; o contrato deve ser selecionado pela lógica de "contract picker").
    3.  `btcusdt@markPrice`: Monitoramento de Funding/Index.
* **Watchdog:** Se `latency_processamento_ms > WS_MAX_PROCESSING_LATENCY_MS` ou `last_msg_age_ms > WS_LAST_MSG_TIMEOUT_MS`, reiniciar conexão.

---

## 5. Lógica Matemática (Precision Core)

### 5.1 Algoritmo de Impact Price (VWAP com Partial Fill)
Calcula o custo exato para executar um volume financeiro, considerando que o último nível de preço pode ser preenchido parcialmente.

**Notas:**
* Para BUY, passe o lado `asks` ordenado por preço ascendente.
* Para SELL, passe o lado `bids` ordenado por preço descendente.
* `target_notional_usdt` deve estar na mesma unidade do `price` (USDT).

```python
def calculate_impact_vwap(book_side, target_notional_usdt):
    """
    book_side: Lista ordenada [[price, qty], ...]
    Retorna: Preço Médio Ponderado (Float) ou NaN se liquidez insuficiente.
    """
    remaining_notional = target_notional_usdt
    total_qty_acquired = 0.0
    cost_accumulator = 0.0
    
    for price, qty in book_side:
        level_notional = price * qty
        
        if level_notional <= remaining_notional:
            # Consome nível inteiro
            execute_notional = level_notional
            execute_qty = qty
        else:
            # Partial Fill: Consome apenas o necessário deste nível
            execute_notional = remaining_notional
            execute_qty = remaining_notional / price
            
        cost_accumulator += (execute_qty * price)
        total_qty_acquired += execute_qty
        remaining_notional -= execute_notional
        
        if remaining_notional <= 1e-6: # Tolerância float
            break
            
    if remaining_notional > 1e-6:
        return float('nan') # Liquidez Insuficiente
        
    return cost_accumulator / total_qty_acquired
```

### 5.2 Definição de Basis (Dual Metrics)

O sistema deve distinguir estritamente "Sinal Estatístico" de "Custo de Execução".

**A. Basis de Sinal (Estatístico - Z-Score):**
Utiliza o `MidPrice` para pureza estatística, evitando ruído de bid-ask spread.
$$Mid = \frac{BestBid + BestAsk}{2}$$
$$Basis_{Signal} = \frac{Mid_{Futuro} - Mid_{Perp}}{Mid_{Perp}}$$

**B. Basis de Execução (Financeiro - PnL Real):**
Utiliza o `ImpactPrice` (`IMPACT_NOTIONAL`) para garantir a viabilidade financeira da entrada.
* **Standard Entry (Short Perp / Long Fut):**
    $$Cost_{Std} = \frac{ImpactAsk_{Futuro} - ImpactBid_{Perp}}{ImpactBid_{Perp}}$$
* **Reverse Entry (Long Perp / Short Fut):**
    $$Cost_{Rev} = \frac{ImpactBid_{Futuro} - ImpactAsk_{Perp}}{ImpactAsk_{Perp}}$$

### 5.3 Z-Score Adaptativo (Adaptive Threshold)
Ajusta a agressividade da entrada baseada na volatilidade relativa do mercado.

1.  **Z-Score Base (sobre `Basis_Signal`):**
    $$\mu_t = Mean(Basis_{Signal}, Z\_WINDOW)$$
    $$\sigma_t = StdDev(Basis_{Signal}, Z\_WINDOW)$$
    $$Z_t = \frac{Basis_{Signal,t} - \mu_t}{\sigma_t}$$
    *Regra:* Se $\sigma_t$ for muito pequeno (ex: sem variação), bloquear entrada ou tratar $Z_t = 0$ para evitar divisão instável.

2.  **Cálculo do VolRatio (regime):**
    Comparar a volatilidade recente com a volatilidade média do dia.
    $$VolNow_t = StdDev(Basis_{Signal}, VOL\_RATIO\_WINDOW)$$
    $$VolRef_t = Mean(StdDev(Basis_{Signal}, VOL\_RATIO\_WINDOW), Z\_WINDOW)$$
    $$VolRatio_t = \frac{VolNow_t}{VolRef_t}$$

3.  **Limiar Dinâmico ($DynamicZ$):**
    * Se $VolRatio < 0.8$ (Mercado Calmo) $\to$ **1.5** (Entrada Agressiva).
    * Se $VolRatio > 1.5$ (Mercado Agitado) $\to$ **3.0** (Entrada Defensiva).
    * Caso contrário $\to$ **2.0** (Padrão).

### 5.4 Filtro de Liquidez (Microestrutura)
* **Range de Análise:** Profundidade relativa $\pm LIQUIDITY\_DEPTH\_PCT$ em torno do Mid.
* **Unidade Recomendada:** notional (USDT), para ficar consistente com `IMPACT_NOTIONAL`.
* **OrderNotional (padrão):** `OrderNotional = IMPACT_NOTIONAL`.
* **Regra (por perna):** a perna BUY deve ter liquidez suficiente no lado Ask, e a perna SELL deve ter liquidez suficiente no lado Bid.
* **Regra de Bloqueio:**
    $$Se (LiquidezSideNotional < OrderNotional \times LIQUIDITY\_MIN\_RATIO): Rejeitar$$
    *Ação:* Logar "Insufficient Liquidity Depth" e pausar entradas por `ENTRY_COOLDOWN_SEC`.

### 5.5 Definição de `Funding_Proj` (Fonte e Unidade)
`Funding_Proj` é a taxa de funding estimada para o **próximo evento de funding**, expressa como fração.

* **Live:** obter via stream `@markPrice` ou REST (`premiumIndex`/equivalente), usando o valor mais recente.
* **Backtest:** usar a coluna `funding_rate` do `mark_price.parquet` via ASOF JOIN.

Regras de operação:
* **Standard:** operar somente se `Funding_Proj > +FUNDING_THRESHOLD`.
* **Reverse:** operar somente se `Funding_Proj < -FUNDING_THRESHOLD`.

---

## 6. Máquina de Estados e Execução

### 6.1 Tabela de Decisão

| Modo | Gatilho Estatístico | Gatilho Financeiro | Ação |
| :--- | :--- | :--- | :--- |
| **ENTRADA STANDARD**<br>(`Funding_Proj > +FUNDING_THRESHOLD`) | `Z < -DynamicZ` | `Cost_Std <= Media - (Custos + ENTRY_SAFETY_MARGIN)` | **Vender Perp / Comprar Fut** |
| **ENTRADA REVERSE**<br>(`Funding_Proj < -FUNDING_THRESHOLD`) | `Z > +DynamicZ` | `Cost_Rev >= Media + (Custos + ENTRY_SAFETY_MARGIN)` | **Comprar Perp / Vender Fut** |
| **SAÍDA (Mean Reversion)** | `abs(Z) <= Z_EXIT_EPS` | N/A (Executar a Mercado) | **Zerar Posições** |
| **STOP LOSS (Z Extreme)** | `abs(Z) >= Z_HARD_STOP` | N/A (Executar a Mercado) | **Zerar Posições** |

Onde:
* `Media`: média móvel de `Basis_Signal` na janela `Z_WINDOW` (mesma usada no Z-Score).
* `Custos`: estimativa conservadora de custos de entrada/saída (fees + slippage). Por padrão, usar `FEE_TAKER_FRAC * 2 + MAX_SLIPPAGE` como aproximação (2 pernas).
* `Cost_Std` e `Cost_Rev`: definidos na seção 5.2 (Basis de Execução).

### 6.2 Pipeline de Execução (Maker $\to$ Taker)

O sistema deve tentar prover liquidez (Maker) antes de tomar liquidez (Taker) para economizar taxas, mas garantir a execução.

```python
async def execute_leg(symbol, side, qty, price_maker, price_taker):
    """
    Pseudocódigo.
    Observação: Post-Only depende do conector (ex: ccxt pode usar `postOnly=True` ou `timeInForce='GTX'`).
    """
    maker_order_id = None

    # 1. Tenta MAKER (Post-Only)
    try:
        maker = await exchange.create_order(
            symbol, 'LIMIT', side, qty, price_maker,
            params={'postOnly': True}
        )
        maker_order_id = maker['id']
    except ExchangeError:
        maker_order_id = None

    filled = 0.0
    if maker_order_id:
        await asyncio.sleep(MAKER_WAIT_SEC)
        status = await exchange.fetch_order(maker_order_id, symbol)
        filled = float(status.get('filled') or 0.0)
        if filled < qty:
            await exchange.cancel_order(maker_order_id, symbol)

    remaining = qty - filled

    # 2. Completa como TAKER (IOC - Immediate or Cancel) se necessário
    if remaining > 0:
        await exchange.create_order(
            symbol, 'LIMIT', side, remaining, price_taker,
            params={'timeInForce': 'IOC'}
        )
```
### 6.3 Recuperação de Legging (Hedge-on-Leg)
Como a Binance não garante atomicidade de execução entre pares diferentes (Perpétuo vs Futuro), o risco de ficar "Pato Manco" (Legging Risk) deve ser tratado como um estado de erro crítico.

* **Trigger:** Após o envio do Batch Order, aguardar `LEGGING_CHECK_DELAY_MS` e consultar o saldo das posições (`fetch_positions`).
* **Lógica de Reconciliação (Hedge Imediato):**
    * Se `abs(Posicao_Perp) > abs(Posicao_Futuro)`:
        * **Cenário:** O Perpétuo executou, mas o Futuro falhou.
        * **Ação:** Enviar ordem `MARKET` no **Futuro** para cobrir a diferença de delta imediatamente, ignorando slippage.
    * Se `abs(Posicao_Futuro) > abs(Posicao_Perp)`:
        * **Cenário:** O Futuro executou, mas o Perpétuo falhou.
        * **Ação:** Enviar ordem `MARKET` no **Perpétuo** para cobrir a diferença imediatamente.
* **Tolerância:** Se `abs(abs(Posicao_Perp) - abs(Posicao_Futuro)) <= HEDGE_EPS_BASE`, considerar hedge OK.
* **Log:** Emitir alerta `CRITICAL_LEGGING_EVENT` com detalhes do desbalanceamento.

---

## 7. Gestão de Risco e Segurança

Esta seção tem precedência absoluta sobre a lógica de trading.

### 7.1 Kill Switch Global
* **Regra:** Se `Total_Equity < Equity_Inicio_Dia * (1 - KILL_SWITCH_DRAWDOWN_FRAC)`.
* **Sequência de Emergência:**
    1. `cancel_all_orders(symbol)` (Cancelar pendentes).
    2. `close_all_positions(market)` (Zerar a mercado).
    3. Enviar notificação de Pânico.
    4. `sys.exit(1)` (Encerrar o processo do robô para evitar reabertura).

### 7.2 Funding Flip Protection
* **Risco:** Estar posicionado para receber Funding (ex: Short Perp) e a projeção virar negativa (ter que pagar).
* **Regra:** Se o sinal do `Funding_Proj` inverter enquanto estiver posicionado.
* **Ação:** Encerrar a posição imediatamente. A estratégia é estritamente de *recebimento* de taxas.

### 7.3 Rate Limits
* **Soft Limit:** `RATE_LIMIT_SOFT_WEIGHT_PER_MIN` weight/min (metade do limite da Binance).
* **Ação:** Se atingido, pausar todas as requisições não-críticas (ex: checks de saldo, fetches auxiliares) por 60 segundos.

### 7.4 Hedge Check (Paridade)
Mesmo após a entrada, deve haver reconciliação periódica para evitar drift direcional.

* **Frequência:** a cada 60 segundos.
* **Regra:** se `abs(abs(Posicao_Perp) - abs(Posicao_Futuro)) > HEDGE_EPS_BASE`, acionar hedge imediato (ordem `MARKET`) para zerar o delta.
* **Ação:** logar `CRITICAL_HEDGE_DRIFT` e aplicar cooldown de entradas.

### 7.5 WebSocket Watchdog (Modo Seguro)
Se o feed estiver atrasado, o robô não pode manter ordens "soltas" no book.

* **Regra:** se `last_msg_age_ms > WS_LAST_MSG_TIMEOUT_MS`:
    * cancelar ordens pendentes,
    * pausar novas entradas,
    * reiniciar conexão com backoff.

---

## 8. Plano de Testes (Tiered QA)

O pipeline de testes deve ser cumprido antes do deploy.

### 8.1 Tier 1: Unit Tests (Lógica Pura - CI)
* `test_impact_price_math`:
    * Input: Book simulado `[[100, 1], [101, 1]]`, Target `150 USDT`.
    * Check: Deve calcular o VWAP considerando preenchimento parcial no nível 101.
* `test_zscore_adaptive`:
    * Input: Array de spreads com alta variância recente.
    * Check: Validar se `DynamicZ` altera automaticamente de 2.0 para 3.0.
* `test_z_exit_threshold`:
    * Input: `Z=0.19` e `Z_EXIT_EPS=0.2`.
    * Check: Deve disparar condição de saída por convergência.
* `test_z_hard_stop`:
    * Input: `Z=4.1` e `Z_HARD_STOP=4.0`.
    * Check: Deve disparar stop loss imediato.
* `test_liquidity_reject`:
    * Input: Book com volume total baixo.
    * Check: Garantir que a função retorna `False` e impede o trade.

### 8.2 Tier 2: Integration (S3 e API)
* `test_parquet_read`:
    * Ler uma amostra do S3, validar tipos (garantir float64 em preços) e conversão correta de timestamps (ms para ns).
* `test_symbol_mapping`:
    * Validar a normalização de strings (ex: converter `BTCUSDT_260626` para o ID interno correto da exchange).
* `test_exchange_connectivity`:
    * Conectar WebSocket na Testnet e validar o recebimento de pelo menos uma mensagem de heartbeat/ticker.

### 8.3 Tier 3: System (Backtest e Dry Run)
* **Backtest:**
    * Rodar o dia `2025-07-01` completo.
    * **Critério:** PnL > 0 após descontar taxas simuladas (0.04% Maker / 0.05% Taker).
* **Paper Trading:**
    * Rodar 48h na Testnet da Binance Futures.
    * **Critério:** Zero erros de "Insufficient Margin" e reconexão automática de WebSocket bem sucedida após interrupção forçada.        

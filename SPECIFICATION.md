# 📘 SPECIFICATION.md: Sistema de Arbitragem de Funding & Basis (Bi-Direcional)

**Versão:** 1.1.0 (Master Unified)
**Estado:** Produção / Crítico
**Data:** Fevereiro 2026
**Autor:** Marcosarm / Gemini Architect

---

## 1. Visão Geral do Sistema

O sistema é um robô de trading de alta frequência (HFT/Mid-frequency) projetado para operar na **Binance Futures (USDT-Margined)**. A estratégia é **Delta Neutral Bi-Direcional**, capaz de lucrar tanto em mercados de alta (Bull) quanto de baixa (Bear), explorando a direção do Funding Rate.

### 1.1 Objetivos Core
1.  **Arbitragem de Spread (Basis):** Comprar o spread quando estatisticamente descontado (Z-Score < -2σ) e vendê-lo quando caro (Z-Score > +2σ).
2.  **Funding Extraction (Carry Trade):**
    * **Standard Carry:** Short Perp / Long Futuro (Ganha Funding Positivo).
    * **Reverse Carry:** Long Perp / Short Futuro (Ganha Funding Negativo).
3.  **Segurança:** Operar alavancado (3x) sob o regime de Margem de Portfólio (Portfolio Margin), com execução atómica.

---

## 2. Arquitetura e Stack Tecnológico

### 2.1 Infraestrutura (AWS)
* **Região Obrigatória:** `ap-northeast-1` (Tokyo) - Latência < 10ms para `fapi.binance.com`.
* **Tipo de Instância:** `c5.large` ou superior (Compute Optimized).
* **Sistema Operativo:** Amazon Linux 2023 ou Ubuntu 22.04 LTS.
* **Rede:** Enhanced Networking (ENA) ativado. IP Elástico associado.
* **Relógio:** Sincronização via `chrony` (precisão de microssegundos).

### 2.2 Stack de Software
* **Linguagem:** Python 3.10+.
* **Bibliotecas Core:**
    * `ccxt` (versão Pro/Async): Conectividade WebSocket e REST.
    * `pandas` & `numpy`: Cálculos vetoriais e séries temporais.
    * `pyarrow` / `fastparquet`: Leitura eficiente dos dados do S3.
    * `boto3`: Integração com AWS S3.
* **Gestão de Processos:** `systemd` (para auto-restart) ou Docker.

---

## 3. Engenharia de Dados (Input)

O sistema opera em modo híbrido: **Backtest** (Dados S3) e **Live** (WebSockets).

### 3.1 Estrutura de Dados S3 (Backtest)
* **Bucket:** `s3://amzn-tdata`
* **Prefixo:** `hftdata`
* **Formato:** Parquet (Snappy/Zstd).

**Mapeamento de Ficheiros Críticos:**
1.  **Orderbook (L2):**
    * Path: `.../orderbook/binance_futures/{SYMBOL}/{YYYY}/{MM}/{DD}/orderbook_{HH}.parquet`
    * Schema: `received_time`, `bids` (array[price, qty]), `asks` (array[price, qty]).
    * *Uso:* Reconstrução de liquidez e cálculo de Preço de Impacto.
2.  **Mark Price:**
    * Path: `.../mark_price/.../mark_price.parquet`
    * Schema: `index_price`, `funding_rate`, `next_funding_time`.
    * *Uso:* Cálculo do Premium Index histórico.

### 3.2 Dados em Tempo Real (Live/WebSocket)
Conexão via `ccxt.pro`. Streams obrigatórios:
1.  `btcusdt@depth20@100ms`: Orderbook top 20 níveis (Perpétuo).
2.  `btcusdt_260626@depth20@100ms`: Orderbook top 20 níveis (Futuro Trimestral).
3.  `btcusdt@markPrice`: Para monitorar o Funding Rate projetado pela exchange.

---

## 4. Lógica Matemática (O "Core")

### 4.1 Cálculo de Preço de Execução (Impact Price)
**Regra Rígida:** JAMAIS utilizar `last_price`. O robô deve calcular o **VWAP de Impacto** para um lote nocional de **$25.000 USD**.

* **Função `calculate_impact_price(book, side, notional_target)`:**
    1.  Iterar sobre as ordens do book (lado oposto: se quer comprar, analisa *asks*).
    2.  Acumular volume até `sum(price * qty) >= 25.000`.
    3.  Retornar média ponderada: $\frac{\sum (price \times qty)}{\sum qty}$.

### 4.2 Definição do Spread (Basis)
$$Spread\% = \frac{\text{ImpactAsk}_{Futuro} - \text{ImpactBid}_{Perp}}{\text{ImpactBid}_{Perp}}$$
*(Nota: Esta fórmula representa o custo real de entrar na operação Standard).*

### 4.3 Indicador Z-Score
Utilizado para identificar desvios estatísticos.
* **Janela (Lookback):** 1440 minutos (24 horas).
* **Cálculo:**
    $$Z = \frac{Spread_{Atual} - \text{Média}(Spread_{1440})}{\text{DesvioPadrão}(Spread_{1440})}$$

### 4.4 Projeção de Funding (Shadow Funding)
O robô deve antecipar o funding rate antes do fecho (00:00, 08:00, 16:00).
* **Fórmula:** Recalcular o *Premium Index* minuto a minuto usando dados do Orderbook.
    $$PremiumIndex = \frac{\text{ImpactAsk}_{Perp}(25k) + \text{ImpactBid}_{Perp}(25k)}{2} - \text{IndexPrice}$$
* **Decisão:** O sinal (+ ou -) define o MODO de operação.

---

## 5. Máquina de Estados (Estratégia Bi-Direcional)

O sistema verifica qual regime de mercado (Regime Switch) está ativo antes de buscar gatilhos.

### 5.1 Seleção de Contrato (Dynamic Hedge)
* **Standard Mode (Bull):** Escolher Futuro com menor Premium (mais barato).
* **Reverse Mode (Bear):** Escolher Futuro com maior Premium (mais caro), para vender caro.

### 5.2 Tabela de Decisão (Gatilhos)

| Modo | Condição Lógica (Gatilho) | Ação (Execução) |
| :--- | :--- | :--- |
| **ENTRADA STANDARD**<br>(Funding Positivo) | `Z-Score < -2.0` (Futuro Barato)<br>**AND** `Funding_Proj > 0.01%` | **LONG BASIS:**<br>1. Vender (Short) Perpétuo<br>2. Comprar (Long) Futuro |
| **ENTRADA REVERSE**<br>(Funding Negativo) | `Z-Score > +2.0` (Futuro Caro)<br>**AND** `Funding_Proj < -0.01%` | **SHORT BASIS:**<br>1. Comprar (Long) Perpétuo<br>2. Vender (Short) Futuro |
| **SAÍDA (Lucro)** | `Z-Score convergiu para 0` | **TAKE PROFIT:**<br>Zerar ambas as posições (Standard ou Reverse). |
| **SAÍDA (Seca)** | Funding inverteu o sinal ou foi a 0. | **STOP TIME:**<br>Zerar posições pois a vantagem matemática acabou. |
| **STOP LOSS** | `Z-Score < -4.0` (Standard)<br>`Z-Score > +4.0` (Reverse) | **HARD STOP:**<br>Zerar imediatamente. |

---

## 6. Sistema de Execução (Execution Engine)

### 6.1 Atomicidade
* Utilizar o endpoint `privatePostBatchOrders` da Binance.
* **Crítico:** As ordens da perna A e perna B devem ser enviadas no mesmo pacote JSON.

### 6.2 Gestão de Ordens
* **Entrada:** Tentar `LIMIT POST-ONLY` (Maker) no topo do book durante 5 segundos. Se não preencher, agredir com `LIMIT IOC` (Taker) calculando slippage máximo de 0.05%.
* **Saída:** Prioridade total para execução. Usar `MARKET` ou `LIMIT IOC` agressivo.

### 6.3 Verificação de Saldo (Hedge Check)
A cada 1 minuto, verificar:
```python
if abs(position_perp_amt) != abs(position_future_amt):
    trigger_rebalance() # Rebalancear para evitar risco direcional
```
---

## 7. Gestão de Risco e Segurança (Safety)

Esta secção tem precedência absoluta sobre qualquer lógica de lucro. O robô deve ser paranoico em relação à preservação de capital.

### 7.1 Kill Switch Global (Disjuntor)
* **Monitorização:** Calcular o `Total_Equity` (Saldo + PnL não realizado) a cada 1 minuto.
* **Gatilho:** Se `Total_Equity < Equity_Inicio_Dia * 0.97` (Drawdown > 3%).
* **Sequência de Emergência:**
    1. Enviar ordem `MARKET` para fechar todas as posições abertas imediatamente.
    2. Cancelar todas as ordens pendentes (`cancel_all_orders`).
    3. Enviar alerta crítico (Telegram/SNS).
    4. Encerrar o processo (`sys.exit(1)`).

### 7.2 Proteção de Inversão de Funding (Flip Protection)
* **Risco:** Estar posicionado em *Reverse Carry* (Long Perp) e o Funding virar Positivo, ou vice-versa.
* **Regra:** Se o sinal do `Funding_Proj` inverter enquanto estiver posicionado (ex: de Negativo para Positivo), acionar **Saída Imediata**.
* **Motivo:** Nunca pagar funding. A estratégia baseia-se em *receber* taxas.

### 7.3 Controlo de Rate Limits (Pesos da API)
* **Implementação:** Manter um contador local de "Weight" da Binance (reseta a cada minuto).
* **Limite Soft:** 1200 por minuto (O limite da Binance é 2400).
* **Ação:** Se atingir 1200, pausar novas requisições não-críticas por 60 segundos.

### 7.4 Watchdog de WebSocket
* **Monitorização:** Guardar o timestamp da última mensagem recebida de *qualquer* stream.
* **Timeout:** Se `Time_Now - Last_Msg_Time > 5 segundos`:
    * Considerar conexão "Zombie".
    * Cancelar ordens abertas imediatamente (Safety Cancel via REST API).
    * Iniciar rotina de reconexão exponencial.

### 7.5 Verificação de Paridade (Hedge Check)
* **Frequência:** A cada 60 segundos.
* **Lógica:** Verificar se `abs(Posição_Perp) == abs(Posição_Futuro)`.
* **Ação:** Se houver desbalanceamento > 0.001 BTC (Legging Risk), acionar rebalanceamento a mercado para zerar o delta.

---

## 8. Plano de Testes (QA)

O código só pode ser promovido para produção após passar por todos os estágios abaixo.

### 8.1 Testes Unitários (Math Core)
* **Teste de Impact Price:**
    * Criar um Orderbook fictício em memória (ex: `[[100, 1], [101, 1]]`).
    * Validar se a função retorna o VWAP correto para um target de volume específico.
* **Teste de Z-Score:**
    * Passar um array fixo de spreads conhecidos e validar se o desvio padrão calculado bate com a biblioteca `numpy`.

### 8.2 Teste de Lógica Reversa (Simulation)
* **Cenário:** Simular inputs onde `Funding = -0.05%` e `Z-Score = +2.5`.
* **Validação:** O robô deve gerar ordem de **COMPRA no Perpétuo** e **VENDA no Futuro**.
* **Critério de Falha:** Se o robô tentar vender o Perpétuo (Short) neste cenário, o teste falha (pois pagaria funding).

### 8.3 Teste de Integração (S3 Data)
* **Performance:** Ler 24 horas de ficheiros Parquet do bucket `s3://amzn-tdata`.
* **Integridade:** Verificar se não há gaps temporais nos dados carregados e se o uso de RAM se mantém estável (< 2GB).

### 8.4 Backtest de Rentabilidade
* **Dataset:** Amostra `2025/07`.
* **Configuração:** Taxas 0.08% (round-trip) + Slippage calculado pelo book.
* **Critério de Aprovação:** Lucro Líquido Final > 0 e Drawdown Máximo < 5%.

### 8.5 Paper Trading (Dry Run)
* **Ambiente:** Binance Futures Testnet.
* **Duração:** 48 horas ininterruptas.
* **Checklist:**
    * [ ] Zero erros de execução crítica (ex: "Insufficient Margin").
    * [ ] Reconexão de WebSocket funcionando.
    * [ ] Logs de auditoria gravando corretamente cada decisão de entrada/saída.
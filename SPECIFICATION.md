# 📘 SPECIFICATION.md: Sistema de Arbitragem de Funding & Basis (Adaptive Bi-Directional)

**Versão:** 1.2.0 (Master Unified - Adaptive Logic)
**Estado:** Produção / Crítico
**Data:** Fevereiro 2026
**Autor:** Marcosarm / Gemini Architect

---

## 1. Visão Geral do Sistema

O sistema é um robô de trading de alta frequência (HFT/Mid-frequency) projetado para operar na **Binance Futures (USDT-Margined)**. A estratégia é **Delta Neutral Bi-Direcional**, capaz de lucrar tanto em mercados de alta (Bull) quanto de baixa (Bear), adaptando-se à volatilidade e liquidez do momento.

### 1.1 Objetivos Core
1.  **Arbitragem de Spread (Basis) Adaptativa:** Comprar/Vender o spread baseando-se em desvios estatísticos ajustados pela volatilidade real do mercado.
2.  **Funding Extraction (Carry Trade):**
    * **Standard Carry:** Short Perp / Long Futuro (Ganha Funding Positivo).
    * **Reverse Carry:** Long Perp / Short Futuro (Ganha Funding Negativo).
3.  **Proteção de Microestrutura:** Validar a profundidade do Orderbook (L2) antes de qualquer execução para mitigar slippage.

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

### 4.3 Indicador Z-Score Base
Utilizado para identificar desvios estatísticos brutos.
* **Janela (Lookback):** 1440 minutos (24 horas).
* **Cálculo:**
    $$Z_{Base} = \frac{Spread_{Atual} - \text{Média}(Spread_{1440})}{\text{DesvioPadrão}(Spread_{1440})}$$

### 4.4 Ajuste Dinâmico de Volatilidade (Adaptive Threshold)
O limiar de entrada deixa de ser fixo (2.0) e adapta-se ao regime de mercado.

* **1. Volatilidade Relativa ($VolRatio$):**
    Comparar a volatilidade da última hora com a média do dia.
    $$VolRatio = \frac{StdDev(Spread_{Last60min})}{Avg(StdDev(Spread_{Last24h}))}$$

* **2. Definição do Limiar Dinâmico ($DynamicZ$):**
    * Se $VolRatio < 0.8$ (Mercado Lateral/Calmo): $DynamicZ = 1.5$ (Mais agressivo).
    * Se $VolRatio > 1.5$ (Mercado Tendência/Pânico): $DynamicZ = 3.0$ (Mais conservador/Seguro).
    * Caso contrário (Normal): $DynamicZ = 2.0$.

### 4.5 Filtro de Liquidez (Liquidity Score)
Antes de aceitar um sinal, o robô deve medir a saúde do book para evitar slippage.
* **Cálculo:** Somar o volume disponível nos primeiros **0.1%** de profundidade do book (Bid e Ask).
* **Regra de Bloqueio:**
    $$Se (LiquidezDisponivel < TamanhoOrdem \times 5): Rejeitar$$

### 4.6 Projeção de Funding (Shadow Funding)
O robô deve antecipar o funding rate antes do fecho.
* **Fórmula:** Recalcular o *Premium Index* minuto a minuto usando dados do Orderbook.
    $$PremiumIndex = \frac{\text{ImpactAsk}_{Perp}(25k) + \text{ImpactBid}_{Perp}(25k)}{2} - \text{IndexPrice}$$
* **Decisão:** O sinal (+ ou -) define o MODO de operação (Standard vs Reverse).

---

## 5. Máquina de Estados (Estratégia Adaptativa)

O sistema verifica qual regime de mercado está ativo antes de buscar gatilhos.

### 5.1 Seleção de Contrato (Dynamic Hedge)
* **Standard Mode (Bull):** Escolher Futuro com menor Premium (mais barato).
* **Reverse Mode (Bear):** Escolher Futuro com maior Premium (mais caro).

### 5.2 Tabela de Decisão (Gatilhos Atualizada)

| Modo | Condição Lógica (Gatilho) | Ação (Execução) |
| :--- | :--- | :--- |
| **ENTRADA STANDARD**<br>(Funding Positivo) | `Z-Score < -DynamicZ` (Barato)<br>**AND** `Funding_Proj > 0.01%`<br>**AND** `Liquidity_Check == OK` | **LONG BASIS:**<br>1. Vender (Short) Perpétuo<br>2. Comprar (Long) Futuro |
| **ENTRADA REVERSE**<br>(Funding Negativo) | `Z-Score > +DynamicZ` (Caro)<br>**AND** `Funding_Proj < -0.01%`<br>**AND** `Liquidity_Check == OK` | **SHORT BASIS:**<br>1. Comprar (Long) Perpétuo<br>2. Vender (Short) Futuro |
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
* **Guardrail de Liquidez:** Se a liquidez secar durante a tentativa Maker (Liquidez < 3x Ordem), cancelar e não agredir.

### 6.3 Verificação de Saldo (Hedge Check)
A cada 1 minuto, verificar:
```python
if abs(position_perp_amt) != abs(position_future_amt):
    trigger_rebalance() # Rebalancear para evitar risco direcional
```

## 7. Gestão de Risco e Segurança (Safety)

Esta seção tem precedência absoluta sobre qualquer lógica de lucro. O robô deve operar sob o princípio de "Preservação de Capital Primeiro".

### 7.1 Kill Switch Global (Disjuntor)
* **Monitoramento:** O sistema deve calcular o `Total_Equity` (Saldo em Carteira + PnL não realizado) a cada 1 minuto.
* **Gatilho:** Se `Total_Equity < Equity_Inicio_Dia * 0.97` (Drawdown Diário > 3%).
* **Sequência de Emergência (Atômica):**
    1.  Enviar ordem `MARKET` para fechar todas as posições abertas imediatamente.
    2.  Cancelar todas as ordens pendentes (`cancel_all_orders`).
    3.  Enviar alerta crítico (Telegram/SNS/Log).
    4.  Encerrar o processo (`sys.exit(1)`).

### 7.2 Proteção de Inversão de Funding (Flip Protection)
* **Risco:** Estar posicionado em uma direção (ex: *Standard Carry*) e a projeção do Funding inverter o sinal bruscamente.
* **Regra:** Se o sinal do `Funding_Proj` cruzar de Positivo para Negativo (ou vice-versa) enquanto houver posição aberta, acionar **Saída Imediata**.
* **Motivo:** A estratégia baseia-se estritamente em *receber* o funding. Pagar taxas destrói a vantagem matemática.

### 7.3 Execution Liquidity Guard (Microestrutura)
* **Camada de Proteção:** Mesmo que o Z-Score indique entrada, a execução deve ser bloqueada se o book estiver "fino".
* **Lógica:** Se a função `Liquidity_Check` retornar `False` (Liquidez < 5x Tamanho da Ordem):
    1.  **Não** enviar a ordem para a exchange.
    2.  Logar o evento: *"Sinal ignorado por falta de profundidade no book"*.
    3.  Pausar novas tentativas de entrada por 30 segundos (Cool-down).

### 7.4 Controle de Rate Limits (Pesos da API)
* **Implementação:** Manter um contador local de "Weight" da Binance (reseta a cada minuto).
* **Limite Soft:** 1200 por minuto (O limite da Binance é 2400).
* **Ação:** Se atingir 1200, pausar todas as requisições não-críticas (ex: consultas de saldo, updates de ticker) por 60 segundos.

### 7.5 Watchdog de WebSocket
* **Monitoramento:** Guardar o timestamp da última mensagem recebida de *qualquer* stream assinado.
* **Timeout:** Se `Time_Now - Last_Msg_Time > 5 segundos`:
    * Considerar conexão morta/zumbi.
    * Cancelar ordens abertas imediatamente via REST API (Safety Cancel).
    * Iniciar rotina de reconexão exponencial.

### 7.6 Verificação de Paridade (Hedge Check)
* **Frequência:** A cada 60 segundos.
* **Lógica:** Verificar se `abs(Posição_Perp) == abs(Posição_Futuro)`.
* **Ação:** Se houver desbalanceamento > 0.001 BTC (Risco Direcional / Legging Risk), acionar rebalanceamento a mercado para zerar o delta imediatamente.

---

## 8. Plano de Testes (QA)

O código só pode ser promovido para produção após passar por todos os estágios abaixo (Pipeline de CI/CD).

### 8.1 Testes Unitários (Math Core)
* **Teste de Impact Price:**
    * Criar um Orderbook fictício em memória (ex: `[[100, 1], [101, 1]]`).
    * Validar se a função retorna o VWAP correto para um target de volume de $25k.
* **Teste de Volatilidade Adaptativa:**
    * Passar uma série de preços com alta variância simulada.
    * Validar se o parâmetro `DynamicZ` sobe automaticamente de 2.0 para 3.0.

### 8.2 Testes de Lógica de Negócio (Simulation)
* **Cenário A (Reverso):** Simular inputs onde `Funding = -0.05%` e `Z-Score = +2.5`.
    * *Validação:* O robô deve gerar ordem de **COMPRA no Perpétuo** e **VENDA no Futuro**.
* **Cenário B (Book Fino):** Criar book com apenas 1 BTC de profundidade total e tentar enviar uma ordem de 10 BTC.
    * *Validação:* O sistema deve **REJEITAR** a ordem internamente e não chamar a API da exchange.

### 8.3 Teste de Integração (Data Engineering)
* **Performance:** Ler 24 horas de arquivos Parquet do bucket `s3://amzn-tdata`.
* **Integridade:** Verificar se não há gaps temporais nos dados carregados e se o consumo de RAM se mantém estável (< 2GB).

### 8.4 Backtest de Rentabilidade
* **Dataset:** Amostra `2025/07`.
* **Comparativo:** Rodar a estratégia em dois modos: "Fixo 2.0" vs "Dinâmico (Adaptive)".
* **Critério de Aprovação:** O modo Dinâmico deve apresentar menor Drawdown Máximo e melhor Sharpe Ratio. Lucro líquido deve ser positivo após taxas (0.08%).

### 8.5 Paper Trading (Dry Run)
* **Ambiente:** Binance Futures Testnet.
* **Duração:** 48 horas ininterruptas.
* **Checklist:**
    * [ ] Zero erros críticos de execução (ex: "Insufficient Margin", "Invalid Order").
    * [ ] Reconexão automática de WebSocket funcionando após simulação de queda de rede.
    * [ ] Logs de auditoria gravando corretamente o motivo de cada entrada/saída.
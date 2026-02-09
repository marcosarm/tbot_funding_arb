# 🤖 QuantBot: USDT-M Basis & Funding Arbitrage

## 📋 Sobre o Projeto
Este projeto implementa um robô de **Arbitragem Estatística (StatArb)** e **Extração de Funding Rate** na Binance Futures (USDT-Margined).

O sistema opera na modalidade **Delta Neutral**, explorando ineficiências entre o contrato Perpétuo (`BTCUSDT`) e os contratos Futuros Trimestrais (`BTCUSDT_CurrentQuarter` / `NextQuarter`).

### 🚀 Estratégia Core
O robô busca capturar lucro de duas fontes simultâneas:
1.  **Basis Trading (Spread):** Compra o spread quando ele está estatisticamente descontado (Z-Score < -2) e vende quando retorna à média.
2.  **Funding Rate Farming:** Mantém posição Short no Perpétuo (recebendo taxa) e Long no Futuro (Hedge) enquanto o custo do carregamento for favorável.

## 🏗️ Arquitetura
- **Linguagem:** Python 3.10+
- **Infraestrutura:** AWS EC2 (Tóquio - `ap-northeast-1`)
- **Dados:** S3 Data Lake (Parquet) + WebSockets (Binance Stream)
- **Execução:** CCXT Pro (Async) com Portfolio Margin.

## 📂 Estrutura de Dados (S3)
O sistema consome dados históricos proprietários armazenados no S3 com particionamento Hive:
- `trades/`: Execuções tick-a-tick.
- `orderbook/`: Snapshots L2 (Depth) para cálculo de impacto.
- `mark_price/`: Histórico de Funding Rates e Index Price.
- `ticker/`: Métricas agregadas.

## ⚙️ Instalação

1. **Clone o repositório:**
   ```bash
   git clone https://github.com/4mti/bot_funding_arb.git
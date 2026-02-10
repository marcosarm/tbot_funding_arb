# 🤖 QuantBot: USDT-M Basis & Funding Arbitrage

## 📋 Sobre o Projeto
Este projeto implementa um robô de **Arbitragem Estatística (StatArb)** e **Extração de Funding Rate** na Binance Futures (USDT-Margined).

O sistema opera na modalidade **Delta Neutral**, explorando ineficiências entre o contrato Perpétuo (`BTCUSDT`) e os contratos Futuros Trimestrais (`BTCUSDT_CurrentQuarter` / `NextQuarter`).

### 🚀 Estratégia Core
O robô busca capturar lucro de duas fontes simultâneas:
1.  **Basis Trading (Spread):** Compra o spread quando ele está estatisticamente descontado (Z-Score < -2) e vende quando retorna à média.
2.  **Funding Rate Farming:** Mantém posição Short no Perpétuo (recebendo taxa) e Long no Futuro (Hedge) enquanto o custo do carregamento for favorável.

## 📌 Status deste repositório
Neste diretório, o foco atual é a **documentação** + um **motor de backtest genérico** (reutilizável) para replay de dados parquet.

- Documento fonte (arquitetura/estratégia/risco/testes): `SPECIFICATION.md`
- Motor de backtest (biblioteca): `src/btengine`
- Documentação do motor (btengine): `docs/btengine/README.md`
- Arquivos de configuração local: `.env` (não deve ser versionado com segredos)

## 🏗️ Arquitetura
- **Linguagem:** Python 3.10+
- **Infraestrutura:** AWS EC2 (Tóquio - `ap-northeast-1`)
- **Dados:** S3 Data Lake (Parquet) + WebSockets (Binance Stream)
- **Execução:** CCXT Pro (Async) com Portfolio Margin.

## 📂 Estrutura de Dados (S3)
O sistema consome dados históricos proprietários armazenados no S3 com particionamento Hive:
- `trades/`: Execuções tick-a-tick.
- `orderbook/`: Atualizações L2 (Depth) para reconstrução do book e cálculo de impacto.
- `mark_price/`: Histórico de Funding Rates e Index Price.
- `ticker/`: Métricas agregadas.

## ⚙️ Instalação

1. **Clone o repositório:**
   ```bash
   git clone https://github.com/marcosarm/tbot_funding_arb.git
   cd tbot_funding_arb
   ```

## 🔐 Configuração (variáveis de ambiente)
Use variáveis de ambiente (ou um arquivo `.env`) para configurar credenciais e recursos. Exemplo:

```dotenv
BINANCE_API_KEY=...
BINANCE_SECRET=...
AWS_REGION=ap-northeast-1
S3_BUCKET=amzn-tdata
```

Notas:
- Não commit/registre segredos no Git. Use Secrets Manager/SSM em produção.
- Para AWS, prefira a cadeia padrão de credenciais (IAM Role, `~/.aws/credentials`, etc.) em vez de keys hardcoded.

## 🧪 Testes e validação
O plano de QA (unitário, simulação, integração, backtest e paper trading) está descrito em `SPECIFICATION.md` na seção **8. Plano de Testes (QA)**.

Para desenvolvimento do motor (`btengine`):

```bash
pytest -q
```

Scripts úteis (exemplo com arquivo local):

```bash
python scripts\\inspect_orderbook_parquet.py C:\\Users\\marco\\Downloads\\orderbook_00.parquet
python scripts\\replay_orderbook.py C:\\Users\\marco\\Downloads\\orderbook_00.parquet --max-messages 2000
```

Scripts úteis (S3 / CryptoHFTData):

```bash
python scripts\\validate_s3_dataset.py --day 2025-07-01 --symbols BTCUSDT --hours 12-12
python scripts\\run_backtest_replay.py --day 2025-07-01 --symbols BTCUSDT --mark-price-symbols BTCUSDT --hours 12-12 --max-events 200000
```

## ⚠️ Aviso de risco
Este projeto envolve execução em mercados alavancados. Não execute em conta real sem:
- backtests consistentes (incluindo taxas/slippage),
- paper trading,
- kill switch e guardrails validados,
- revisão de risco operacional (rede, rate limits, reconexão, auditoria).

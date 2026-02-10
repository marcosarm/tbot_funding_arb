# Amostras CryptoHFTData

Este arquivo traz pequenas amostras reais dos arquivos no S3 para apoiar analises externas de estrategias.
As amostras usam exchange `binance_futures` e simbolo `BTCUSDT`, com datas mais proximas de 2025-07-01.
Os timestamps estao em epoch (ms/us/ns) e devem ser convertidos para UTC.

## Estrutura dos arquivos

Formato:
- Arquivos Parquet
- Compressao: Zstd level 3

Particionamento no S3 (prefixo via `S3_PREFIX`):
- Trades: `s3://{bucket}/{S3_PREFIX}/trades/{exchange}/{symbol}/YYYY/MM/DD/trades.parquet`
- Orderbook: `s3://{bucket}/{S3_PREFIX}/orderbook/{exchange}/{symbol}/YYYY/MM/DD/orderbook_{HH}.parquet` (um arquivo por hora)
- Ticker: `s3://{bucket}/{S3_PREFIX}/ticker/{exchange}/{symbol}/YYYY/MM/DD/ticker.parquet`
- Mark price: `s3://{bucket}/{S3_PREFIX}/mark_price/{exchange}/{symbol}/YYYY/MM/DD/mark_price.parquet`
- Open interest: `s3://{bucket}/{S3_PREFIX}/open_interest/{exchange}/{symbol}/YYYY/MM/DD/open_interest.parquet`
- Liquidations: `s3://{bucket}/{S3_PREFIX}/liquidations/{exchange}/{symbol}/YYYY/MM/DD/liquidations.parquet`

Observacoes:
- O particionamento e diario (YYYY/MM/DD); orderbook e dividido por hora.
- Os arquivos `orderbook_00.parquet` sao filtrados para manter apenas linhas do dia alvo.
- Open interest pode gerar arquivo parcial `open_interest_parcial.parquet` quando o retorno contem outros dias.
- Nem sempre os Parquets estao fisicamente ordenados por tempo (ou por ids). Nao assuma que "primeira/ultima linha" = min/max.
- No orderbook, as linhas podem estar intercaladas entre diferentes `final_update_id` (mensagens). Para replay, e comum ordenar por `final_update_id` e agrupar.
- No trades, pode ser necessario ordenar por `trade_time`.

## trades

Descricao: Execucoes de negocios (trades) com preco e quantidade por evento.

Possiveis usos:
- microestrutura, agressao e intensidade de fluxo
- volatilidade intradiaria e slippage
- sinais de tendencia de curto prazo

Arquivo amostrado: `s3://amzn-tdata/hftdata/trades/binance_futures/BTCUSDT/2025/07/01/trades.parquet`
Linhas no arquivo: 2044106
Colunas: received_time, event_time, symbol, trade_id, price, quantity, trade_time, is_buyer_maker, order_type

Amostra (primeiras 3 linhas):
```text
      received_time    event_time  symbol   trade_id     price quantity    trade_time  is_buyer_maker order_type
1751328000147764818 1751328000019 BTCUSDT 6440230568 107087.30    0.002 1751328000018            True     MARKET
1751328004573109108 1751328004441 BTCUSDT 6440230663 107079.20    0.001 1751328004439            True     MARKET
1751328004572260294 1751328004441 BTCUSDT 6440230636 107082.50    0.001 1751328004439            True     MARKET
```

## orderbook

Descricao: Atualizacoes do livro (L2) por hora, com side/preco/quantidade e ids de update.

Possiveis usos:
- imbalanco de livro e resistencia/suporte
- medidas de liquidez e profundidade
- detectar spoofing e absorcao

Arquivo amostrado: `s3://amzn-tdata/hftdata/orderbook/binance_futures/BTCUSDT/2025/07/01/orderbook_12.parquet`
Linhas no arquivo: 2820821
Colunas: received_time, event_time, transaction_time, symbol, event_type, first_update_id, final_update_id, prev_final_update_id, last_update_id, side, price, quantity, order_count

Amostra (primeiras 3 linhas):
```text
      received_time    event_time  transaction_time  symbol event_type  first_update_id  final_update_id  prev_final_update_id  last_update_id side     price quantity  order_count
1751373693665452410 1751373693536     1751373693536 BTCUSDT     update    7921380229047    7921380235586         7921380228828             NaN  ask 100000.00    0.000          NaN
1751374446003159640 1751374445874     1751374445873 BTCUSDT     update    7921442432963    7921442438455         7921442432855             NaN  ask 100000.00    0.000          NaN
1751374105893599026 1751374105765     1751374105763 BTCUSDT     update    7921415557712    7921415563960         7921415557543             NaN  ask 100037.00    0.000          NaN
```

## ticker

Descricao: Resumo de mercado (precos, volumes e estatisticas) por evento.

Possiveis usos:
- momentum agregado e volatilidade
- filtros de regime (range vs trend)

Arquivo amostrado: `s3://amzn-tdata/hftdata/ticker/binance_futures/BTCUSDT/2025/07/01/ticker.parquet`
Linhas no arquivo: 83150
Colunas: received_time, event_time, symbol, price_change, price_change_percent, weighted_average_price, last_price, last_quantity, open_price, high_price, low_price, base_asset_volume, quote_asset_volume, statistics_open_time, statistics_close_time, first_trade_id, last_trade_id, total_trades

Amostra (primeiras 3 linhas):
```text
      received_time    event_time  symbol price_change price_change_percent weighted_average_price last_price last_quantity open_price high_price low_price base_asset_volume quote_asset_volume  statistics_open_time  statistics_close_time  first_trade_id  last_trade_id  total_trades
1751328001048197874 1751328000024 BTCUSDT     -1222.50               -1.129              107613.40  107087.30         0.002  108309.80  108783.90 106686.40        114681.388     12341254004.54         1751241600000          1751328000018      6438365167     6440230568       1865097
1751328006093987565 1751328005962 BTCUSDT     -1232.20               -1.138              107613.30  107077.60         0.027  108309.80  108783.90 106686.40        114702.026     12343464017.74         1751241600000          1751328004693      6438365167     6440230725       1865254
1751328007095299719 1751328006821 BTCUSDT     -1230.50               -1.136              107613.18  107079.30         0.004  108309.80  108783.90 106686.40        114728.422     12346290647.12         1751241600000          1751328005657      6438365167     6440230987       1865516
```

## mark_price

Descricao: Mark price e metrics usadas em risco/liquidacao, tipicamente por segundo.

Possiveis usos:
- custo de carrego e risco
- inputs para modelos de margem

Arquivo amostrado: `s3://amzn-tdata/hftdata/mark_price/binance_futures/BTCUSDT/2025/07/01/mark_price.parquet`
Linhas no arquivo: 86400
Colunas: received_time, event_time, symbol, mark_price, index_price, estimated_settle_price, funding_rate, next_funding_time

Amostra (primeiras 3 linhas):
```text
      received_time    event_time  symbol      mark_price     index_price estimated_settle_price funding_rate  next_funding_time
1751328000193508361 1751328000000 BTCUSDT 107096.57111594 107141.75586957        107112.70619197   0.00001053      1751328000000
1751328001187026559 1751328001000 BTCUSDT 107096.74133333 107141.92608696        107112.70301185   0.00001053      1751356800000
1751328002178086037 1751328002000 BTCUSDT 107096.74133333 107141.92608696        107112.68359251   0.00001053      1751356800000
```

## open_interest

Descricao: Snapshot agregado de open interest e valor do open interest.

Possiveis usos:
- posicionamento agregado
- confirmacao de tendencia

Arquivo amostrado: `s3://amzn-tdata/hftdata/open_interest/binance_futures/BTCUSDT/2025/07/01/open_interest.parquet`
Linhas no arquivo: 288
Colunas: received_time, symbol, sum_open_interest, sum_open_interest_value, timestamp

Amostra (primeiras 3 linhas):
```text
      received_time  symbol sum_open_interest sum_open_interest_value     timestamp
1751382485727228872 BTCUSDT    76425.99000000     8185783816.79351900 1751328000000
1751328543525658820 BTCUSDT    76382.28200000     8179458942.49951000 1751328300000
1751356986151756418 BTCUSDT    76376.00600000     8187179426.37420000 1751328600000
```

## liquidations

Descricao: Eventos de liquidacao com lado, preco, quantidades e status de ordem.

Possiveis usos:
- stress events e clusters de forca
- sinais contrarian ou momentum

Arquivo amostrado: `s3://amzn-tdata/hftdata/liquidations/binance_futures/BTCUSDT/2025/07/01/liquidations.parquet`
Linhas no arquivo: 962
Colunas: received_time, event_time, symbol, side, order_type, time_in_force, quantity, price, average_price, order_status, last_filled_quantity, filled_quantity, trade_time

Amostra (primeiras 3 linhas):
```text
      received_time    event_time  symbol side order_type time_in_force quantity     price average_price order_status last_filled_quantity filled_quantity    trade_time
1751328515389424305 1751328515266 BTCUSDT  BUY      LIMIT           IOC    0.001 107565.20     107125.20       FILLED                0.001           0.001 1751328515261
1751328522455448284 1751328522332 BTCUSDT  BUY      LIMIT           IOC    0.004 107539.10     107125.20       FILLED                0.004           0.004 1751328522328
1751328788853195115 1751328788724 BTCUSDT  BUY      LIMIT           IOC    0.053 107700.10     107269.40       FILLED                0.053           0.053 1751328788720
```

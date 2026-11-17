# Data dictionary

## Processed files (`data/processed/`)

### `indice_mensual.csv` — official index (UPRA)

| Field | Description | Unit |
|---|---|---|
| `fecha` | First day of the month | Date |
| `T_Índice` | National total index (target variable) | January 2021 = 100 |
| `T_Fertilizantes`, `T_Simples`, `T_Compuestos` | Fertilizer sub-indices | January 2021 = 100 |
| `T_Plaguicidas`, `T_Herbicidas`, `T_Fungicidas`, `T_Insecticidas` | Pesticide sub-indices | January 2021 = 100 |
| `T_Otros` | Other inputs; available from January 2021 (earlier months are missing) | January 2021 = 100 |

### `exogenas_mensual.csv` — external variables

| Field | Description | Unit |
|---|---|---|
| `trm` | Monthly average of the representative market exchange rate | COP per USD |
| `urea_usd` | International urea price (World Bank Pink Sheet) | USD per metric ton |
| `dap_usd` | International DAP price (World Bank Pink Sheet) | USD per metric ton |

### `mercado_mensual.csv` — market view

| Field | Description | Unit |
|---|---|---|
| `trm` | Monthly average exchange rate | COP per USD |
| `urea_usd`, `dap_usd`, `kcl_usd`, `tsp_usd` | International prices of urea, DAP, potassium chloride and TSP | USD per metric ton |

### `sipsa_precios.csv` — input prices (DANE SIPSA-I)

| Field | Description |
|---|---|
| `fecha` | First day of the month |
| `grupo` | Fertilizantes, Fungicidas, Herbicidas or Insecticidas |
| `producto`, `presentacion` | Product and package size as published by SIPSA-I; 15 products per group |
| `departamento` | Department, or `Nacional` for the national summary |
| `precio` | Median of the published average prices, COP |
| `n_registros` | SIPSA-I records used in the median |

### `producto_pronostico.csv` and `producto_resumen.csv` — product forecasts

| Field | Description |
|---|---|
| `horizonte`, `fecha`, `valor`, `li80`, `ls80` | Estimated price and 80% interval for each of the next six months, COP |
| `precio`, `fecha_precio` | Latest national median price |
| `minimo_24m`, `maximo_24m`, `posicion_24m` | 24-month range and position of the current price (0 = minimum, 1 = maximum) |
| `plazo`, `var_12m_pct`, `var_plazo_pct` | Recommendation horizon (3 months for food, 6 for inputs), observed 12-month change and expected change |
| `modelo`, `mae_modelo`, `mae_persistencia` | Published model (`naive` or `ets`) and validation errors |
| `recomendacion`, `motivo` | `comprar` (buy), `esperar` (wait) or `normal`, and the reason |
| `kg_nutriente`, `precio_kg_nutriente` | Kilograms of N + P2O5 + K2O per unit and price per kilogram of nutrient |

### `alimentos_precios.csv`

Same fields as `sipsa_precios.csv` for 64 food products (wholesale prices, COP per kilogram; eggs per unit,
oil per litre).

### `sipsa_contraste.csv`

Chained index per group built from the median monthly change of comparable SIPSA-I series. Used to check
the trend of the UPRA sub-indices; it is not an official index.

### Food prices (`food_*.csv`, built by `src/food_prices.py`)

| File | Content |
|---|---|
| `food_daily.csv` | DANE daily bulletin: `price_date`, `food_group`, `product`, `market`, `city`, `price` ($/kg), last 45 days |
| `food_monthly_national.csv` | National monthly median price of every product since 2018 (`month`, `price`, `n_markets`) |
| `food_monthly_market.csv` | Monthly price by `market`, `city` and `department`, last 13 months |
| `food_forecast.csv` | 3-month forecast per product: `horizon`, `month`, `value`, `lo80`, `hi80` |
| `food_outlook.csv` | Latest price, 24-month range and position, 3-month change, model, errors, `recommendation` (buy / wait / normal) and `reason` |

The same data is loaded every day into the database tables `food_product`, `food_price`,
`food_forecast` and `food_outlook`. Shopping lists are stored in `shopping_list` and `shopping_item`.

## Reports (`reports/`)

| File | Content |
|---|---|
| `calidad_datos.json` | Data quality report: period, missing values, basket changes, outliers, comparison with Datos Abiertos |
| `backtest.csv` | Every historical forecast: model, block, origin, target month, horizon, actual, forecast, MASE scale |
| `metricas.csv` | MAE, RMSE and MASE by model, block and horizon (0 = all horizons) |
| `resultados.json` | Selected model, summary metrics, Diebold-Mariano tests, interval coverage, six-month forecast |
| `crisp/evidence.json` | Before/after samples, stationarity tests, partitions, tuning grids, fit gap, bias |
| `crisp/experiments.csv` | Experiment log (backtests and tuning) |
| `crisp/monitoring_test.json`, `crisp/monitoring_test_log.jsonl` | Initial monitoring test with **simulated** data |
| `crisp/domain_review.json` | Domain review: reviewer, date and feedback |

## Information system database (schema `agrocast`)

The tables of the web information system (users, farms, purchase plans, inferences, drift checks,
incidents, operational log, model versions) are described in `docs/technical_manual.md`.

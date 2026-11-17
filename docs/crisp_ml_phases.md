# AGROCAST — CRISP-ML phases

Data cut-off: **August 2026** (latest UPRA index published; checked on 16 November 2026).
Every number in this document is produced by the scripts named in each section; the published
application shows the same results under **CRISP-ML** in the top menu.

## Phase 1. Business and data understanding

**Problem.** Fertilizers and pesticides are a large share of crop costs in Colombia and most are imported.
The official agricultural input price index (UPRA, January 2021 = 100) rose above 220 points in 2022 and fell
again during 2023. Producers cannot easily tell whether to buy inputs now or wait.

**Stakeholders.** Small and medium producers (main beneficiaries), associations and cooperatives, technical
assistants and agronomists, data providers (UPRA, DANE, Ministry of Agriculture) and the project team.

| Business objective | Machine learning objective | Measure |
|---|---|---|
| Anticipate input cost changes | Forecast the total, fertilizer and pesticide index 1–6 months ahead | MAE, RMSE, MASE by horizon |
| Decide whether to buy now or wait | Classify the six-month change with a ±3% band | Decision per plan vs actual price |
| Avoid false certainty | Calibrated 80% and 95% intervals | Coverage on the test block |
| Use the forecast in daily work | Serve a versioned model on the server | Response time, logged inferences, drift |

**Learning task.** Supervised regression on a monthly time series (multi-step forecasting, horizon 6).

**Data.** 93 monthly observations (December 2018 – August 2026). Target `T_Índice`; sub-indices for
fertilizers and pesticides. External variables: TRM, urea and DAP prices (World Bank Pink Sheet).
SIPSA-I input prices (DANE) are used for external validation and for product prices in the plans.

**Quality.** No missing months or target values; annex vs Datos Abiertos maximum difference 0.005 points;
12-month change of the SIPSA-I contrast index correlates 0.87–0.98 with the UPRA sub-indices; the basket
changes of January and September 2024 are not atypical. The series is short, so parsimonious models are used.

**Success criteria.** From the approved proposal: beat persistence in validation and keep the improvement on
the independent test. Added for operation: 80% interval coverage ≥ 80% on test; inference under 3 seconds,
logged with the model version.

## Phase 2. Data engineering

Script: `src/prepare.py` (run by `src/pipeline.py`); evidence: `src/evidence.py` → `reports/crisp/`.

| Step | Transformation | Justification | Fitted on |
|---|---|---|---|
| 1 | Monthly frequency (first day of month), one row per month | Gaps become visible | Fixed rule |
| 2 | Zeros → missing values (156 cells) | Zero means "no data" in the annex | Fixed rule |
| 3 | Natural logarithm | Proportional changes, stable variance, positive forecasts | Fixed rule |
| 4 | First difference of the log (d = 1) | Removes the trend (KPSS rejects stationarity of the log, p = 0.01, but not of the log change, p ≥ 0.10; ADF with one lag rejects a unit root in the log change, p = 0.014) | Inside SARIMA |
| 5 | Lagged log changes 1, 2, 3, 6, 12 | Momentum and annual change (Ridge) | Past values only |
| 6 | TRM, urea, DAP log changes 1, 3, 6 | Imported cost drivers (Ridge) | Past values only |
| 7 | Month as sine/cosine | Cyclical encoding of seasonality | Fixed rule |
| 8 | StandardScaler | Ridge penalty needs comparable scales | Training rows of each origin |

Outliers (robust z > 3, eleven months in 2021–2023 and April–May 2026) are kept: they are real market moves.
No duplicates were found. Figures: `reports/crisp/figures/before_after_zeros.png`, `transformations.png`.

**Partitions** (rolling origin, expanding window, minimum 36 months of training):

| Block | Months | Use |
|---|---|---|
| Initial training | 2018-12 to 2021-11 | First fit |
| Validation | 40 origins, 2021-11 to 2025-02 (240 forecasts per model) | Comparison, tuning, selection |
| Independent test | 12 origins, 2025-03 to 2026-02; targets 2025-04 to 2026-08 (72 forecasts) | Reported once |
| Final fit | 2018-12 to 2026-08 | Saved model |

At every origin, models, scaler, Ridge penalty and SARIMA/ETS orders use only months up to that origin.

## Phase 3. Model engineering

Script: `src/models.py`, `src/evaluate.py`; log: `reports/crisp/experiments.csv`.

| Model | Configuration |
|---|---|
| Persistence | Last value |
| Seasonal naive | Value 12 months earlier |
| ETS | Log; additive error; trend none/additive/damped by AICc |
| SARIMA | Log; d = 1; p, q ∈ {0, 1, 2}; optional seasonal AR(1)₁₂; AICc at each origin |
| Direct Ridge | One model per horizon; 16 features; alpha ∈ {0.1, 1, 10, 100, 1000} by TimeSeriesSplit (3 × 6 months) |

With all data, SARIMA selects (1,1,1) without a seasonal term (AICc −471.5). Domain knowledge used: imported
inputs (exchange rate and international prices tested), proportional price changes (log), purchase cycle
(six-month horizon), planting seasons (seasonal terms offered, not supported by the data), shocks kept.

## Phase 4. Model evaluation

| Model | MASE val. | MAE val. | RMSE val. | MASE test | MAE test | RMSE test |
|---|---:|---:|---:|---:|---:|---:|
| Persistence | 4.321 | 12.781 | 18.841 | 1.715 | 5.063 | 9.498 |
| Seasonal naive | 15.677 | 48.827 | 58.057 | 3.531 | 10.706 | 12.426 |
| ETS | 3.545 | 10.948 | 20.744 | 1.837 | 5.458 | 9.195 |
| **SARIMA (selected)** | **3.324** | 10.297 | 18.907 | 1.837 | 5.456 | 9.195 |
| Direct Ridge | 4.420 | 14.118 | 21.079 | 2.138 | 6.402 | 9.610 |

- Selection rule fixed before the test: lowest validation MASE; persistence if no candidate beats it.
- Independent test: SARIMA does not beat persistence (MASE 1.837 vs 1.715); Diebold-Mariano at six months
  p = 0.947. Validation p = 0.761. The second success criterion is **not met**.
- 80% / 95% interval coverage on test: 91.7% / 100%.
- Overfitting: in-sample one-step MASE 0.515 vs validation 0.788 (SARIMA); Ridge, the most flexible
  model, loses to persistence in both blocks. SARIMA residuals: Ljung-Box p = 0.057 (lag 6), 0.109 (lag 12).
- The domain review is recorded in `reports/crisp/domain_review.json` and shown in the application.

## Phase 5. Model deployment

- Artifact: `models/sarima_v1.1.0.joblib` + model card `models/sarima_v1.1.0.json` (`src/train_model.py`).
- Inference: `src/inference.py` (same preprocessing; frozen parameters; Kalman filter over the records).
- Public demonstration: `/crisp-ml/deployment` (series, horizon and up to three new monthly values).
- Integration: purchase-plan evaluation, forecast lab and `POST /api/v1/forecast` in the information system.
- Procedures: `docs/user_manual.md`, `docs/training_guide.md`, `docs/support_procedures.md`.

## Phase 6. Monitoring and maintenance

See `docs/monitoring_maintenance.md` for indicators, thresholds, responsibilities and the update procedure.
Initial test with **SIMULATED** data: `python -m src.monitoring_test` → `reports/crisp/monitoring_test.json`
and `monitoring_test_log.jsonl`. Stable scenario: one-step MAPE 1.27%, no incidents. Shock (+12%):
the consecutive-error rule opens a performance incident in the second month; the drift check stays "ok",
so a shorter drift window is planned for the next version.

## References

- Hewamalage, H., Ackermann, K., & Bergmeir, C. (2023). Forecast evaluation for data scientists: Common pitfalls and best practices. *Data Mining and Knowledge Discovery, 37*, 788–832. https://doi.org/10.1007/s10618-022-00894-5
- Hyndman, R. J., & Athanasopoulos, G. (2021). *Forecasting: Principles and practice* (3rd ed.). OTexts. https://otexts.com/fpp3/
- Studer, S., et al. (2021). Towards CRISP-ML(Q): A machine learning process model with quality assurance methodology. *Machine Learning and Knowledge Extraction, 3*(2), 392–413. https://doi.org/10.3390/make3020020
- UPRA. Índice de precios de insumos agrícolas. https://www.datos.gov.co/Agricultura-y-Desarrollo-Rural/-ndice-de-precios-de-insumos-agr-colas/gwbi-fnzs

# AGROCAST — Monitoring and maintenance plan

## 1. What is logged

| Signal | Where | How |
|---|---|---|
| Errors | `event_log` (`http.error`, level error) | global error handler with exception type and message |
| Inference requests | `inference` + `event_log` (`inference.completed`) | user, input, output, model version, response time (ms) |
| Rejected inputs | `event_log` (`inference.rejected`) | validation message (gaps, horizon, non-positive values) |
| Response times | `inference.response_ms`, `X-Response-Time-ms` header, `http.slow_request` | slow requests (> 3 s) stored |
| Security | `auth.failed_login`, `auth.locked`, `auth.forbidden` | |

The **Model monitoring** page summarizes the last 30 days: number of inferences, average and p95
response time, server errors, rejected inputs and forecasts awaiting actual values.

## 2. Comparing predictions with actual outcomes

Every stored inference keeps its series, target month, forecast and 80% interval. UPRA publishes a
new index month around the third week of the following month. When the analyst registers that month
in **Index records**, the system joins it with every stored forecast for that month and shows, in
**Forecast vs actual**: forecast, actual value, percentage error, whether the actual value fell inside
the 80% interval, horizon and model version. From these rows the page computes MAPE and empirical
80% coverage. At the business level, purchased plans store the actual unit prices, so planned,
projected and actual costs can be compared per plan and in the Indicators page.

## 3. Input data and performance checks

**Run drift & performance check** (monthly, after registering the new UPRA month):

- Mean shift: z-score of the mean of the last 12 monthly log changes against the training distribution.
- Volatility: ratio between the recent standard deviation and the training standard deviation.
- Performance: MAPE of forecasts whose target month is already observed (active version).

| Status | Rule | Action |
|---|---|---|
| ok | \|z\| ≤ 2, ratio 0.67–1.5, MAPE ≤ 10% | none |
| warning | \|z\| > 2, ratio > 1.5, or MAPE > 10% | review with the analyst; communicate wider uncertainty to users |
| alert | \|z\| > 3 or ratio outside 0.33–2 | retrain and evaluate a new version |

## 4. Versioning, updates and retraining

- A version is identified by semantic versioning: `MAJOR` = different algorithm or features,
  `MINOR` = retraining with new data, `PATCH` = packaging fix.
- Each version produces `models/sarima_v<version>.joblib` and a model card `.json` with the data cut-off,
  data fingerprint (SHA-256 of the index file), specification, parameters and evaluation metrics.
- Versions are registered in `model_version`; a unique partial index guarantees a single active version.
  Every inference stores `model_version_id`, so results can always be traced to the version that produced them.
- Retraining schedule: quarterly, or immediately after an *alert* status or two consecutive *warnings*.
- Procedure:
  1. `python -m src.pipeline --actualizar-fuentes` (download, prepare and re-evaluate ETS, SARIMA and Ridge).
  2. Check that the selected model still beats the persistence baseline in validation.
  3. `python -m src.train_model --version 1.1.0`, `python -m pytest`.
  4. Commit the artifact, deploy, `flask --app app seed` (registers the version as inactive).
  5. Activate it in Admin → Model versions. Plans evaluated with the previous version must be re-evaluated before a decision.
  6. Rollback = activate the previous version (artifacts are never deleted).

## 5. Initial operational review (16 November 2026)

Performed on the production database with the three test accounts:

| Check | Result |
|---|---|
| Database persistence | records created through the interface (farms, plans, items, inferences, incidents) are read back after a new session |
| Authentication | registration, login, logout and failed login logged; role field injected in registration ignored (account created as producer) |
| Role permissions | producer → 403 in admin and analyst modules; analyst → 403 in admin modules; admin cannot change own role |
| Business workflow | plan draft → evaluated → approved (decision against the model with justification) → purchased; deferred plan kept open |
| Model inference | 5 inferences stored with user, input, result and version; average response 600 ms (first call 1.36 s while the artifact is loaded), 0 rejected inputs, 0 server errors |
| Response times | 10 requests above 3 s, all caused by database round trips from a client in Colombia to the US-East database (many small lazy-loaded queries per page) |
| Action taken | eager loading of plan items, products, users and model versions and a cached active-model lookup: dashboard 2.3 s → 1.1 s, plan list 1.2 s → 0.6 s in the same conditions; the Render service is placed in Virginia, the same region as the database |
| Operational log | 52 events recorded (authentication, business operations, inferences, monitoring, slow requests) |
| Drift check | total, fertilizers and pesticides: status ok |
| Forecast vs actual | pending: no target month observed yet (first comparison when UPRA publishes September 2026) |

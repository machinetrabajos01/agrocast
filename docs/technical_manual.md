# AGROCAST — Technical manual

## 1. Architecture

| Layer | Technology | Location |
|---|---|---|
| Presentation | Jinja2 templates, Bootstrap 5, Chart.js | `web/templates`, `web/static` |
| Application | Flask 3, gunicorn | `app.py`, `agrosys/` |
| Model | statsmodels SARIMAX, joblib artifact | `src/inference.py`, `models/` |
| Persistence | PostgreSQL 17 (Supabase), SQLAlchemy 2, psycopg 3 | schema `agrocast` |
| Offline pipeline | pandas, scikit-learn, statsmodels | `src/pipeline.py`, `src/train_model.py` |
| Hosting | Render web service | `render.yaml` |

The browser talks only to Flask. Flask connects to PostgreSQL through the Supabase Supavisor pooler
(TLS) with the role `agrocast_app`, which has privileges only on the `agrocast` schema. Row level
security is enabled on every table and the only policy belongs to that role; the schema is not
exposed through the Supabase Data API.

## 2. Data model

| Table | Content | Key relationships |
|---|---|---|
| `app_user` | accounts, scrypt hash, role, active flag | — |
| `farm` | production units | `owner_id → app_user` |
| `product_price` | latest SIPSA-I price per input product | — |
| `purchase_plan` | plan header, status, recommendation, decision | `owner_id`, `farm_id`, `last_inference_id` |
| `plan_item` | quantity, planned and actual unit price | `plan_id`, `product_price_id` |
| `index_observation` | UPRA monthly index by series | `entered_by → app_user` |
| `model_version` | registered artifacts, metrics, active flag (unique partial index) | — |
| `inference` | every model execution with input and output JSON | `user_id`, `plan_id`, `model_version_id` |
| `drift_check` | results of the input drift and performance checks | `model_version_id`, `run_by` |
| `incident` | incidents and feedback | `reporter_id`, `assigned_to` |
| `event_log` | authentication, business and inference events, errors, response times | `user_id` |

CHECK constraints enforce the domain values (roles, statuses, categories, horizons, positive
prices and quantities). All foreign keys are indexed.

## 3. Security

- Passwords: `werkzeug.security.generate_password_hash(method="scrypt")`, minimum 8 characters with
  letters and numbers.
- Sessions: signed cookie, HttpOnly, SameSite=Lax, Secure in production, 8-hour lifetime; the session
  is invalidated when the user's role or active flag changes.
- CSRF: per-session token required for every POST (form field `_csrf` or header `X-CSRF-Token`).
- Authorization: `@permission_required(<permission>)` on every operational route, using the matrix in
  `agrosys/security.py`. Ownership is checked for farms, plans, inferences and incidents.
- Privilege escalation: registration ignores any `role` field; only admins change roles, never their own.
- Login throttling: five failed attempts per session block further attempts; failures are logged.
- Headers: `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`.

## 4. Roles and permissions

| Permission | producer | analyst | admin |
|---|:-:|:-:|:-:|
| Market views, dashboard, own history, report incidents | ✔ | ✔ | ✔ |
| Manage own farms and purchase plans, evaluate plans | ✔ | | ✔ |
| View all plans and all inferences, indicators, CSV export | | ✔ | ✔ |
| Index records CRUD, forecast lab, API, monitoring | | ✔ | ✔ |
| Manage incidents | | ✔ | ✔ |
| Users and roles, model versions, operational log, run pipeline | | | ✔ |

## 5. Model integration

- Artifacts: `models/sarima_v1.0.0.joblib` and `models/sarima_v1.1.0.joblib` (active), each with a model card `.json`. Contains, for the total, fertilizer
  and pesticide series, the SARIMA order, seasonal order, fitted parameters, training window, AICc and
  the reference distribution of monthly log changes, plus the Activity 1 selection metrics.
- Preprocessing preserved: monthly frequency (month start), natural logarithm, d = 1, same
  specification grid. Inputs are validated (positive values, no gaps, at least 36 months).
- Inference: `src/inference.forecast()` builds the SARIMAX with the stored specification, runs the
  Kalman filter with the frozen parameters (`filter`, no re-estimation) over the records in
  `index_observation` and returns the six-month path with 80% and 95% intervals.
- Decision rule (`recommend`): change ≥ +3% → buy now; ≤ −3% → defer; otherwise buy as planned.
  Confidence is "low" when the 80% interval of the change contains zero.
- Traceability: version 1.1.0 reproduces the pipeline forecast exactly (index 149.628 for February
  2027 in `reports/resultados.json`); version 1.0.0 gives the same value.

### Inference API

```
POST /api/v1/forecast          (analyst or admin session, header X-CSRF-Token)
{"series": "fertilizantes", "horizon": 6, "manual_values": {"2026-09": 191.5}}
→ 200 {"inference_id": 12, "origin_month": "2026-09", "target_month": "2027-03",
       "predicted_value": ..., "change_pct": ..., "path": [...], "recommendation": {...}}
→ 422 {"error": "..."} when the input violates the training structure
```

## 6. Logging and monitoring

`event_log` stores: `auth.*` (login, failed login, logout, register, forbidden), business events
(`farm.*`, `plan.*`, `data.index_*`, `incident.*`), `inference.completed` with response time,
`inference.rejected`, `http.slow_request` (> 3 s), `http.error` with exception type, `model.*`.
The response time of each request is also returned in the `X-Response-Time-ms` header.
See `docs/monitoring_maintenance.md` for the monitoring procedures.

## 7. Testing

`python -m pytest` runs 22 tests: 15 pipeline tests (temporal leakage, metrics, models) and 7
integration tests of the information system (protected routes, CSRF, role escalation, complete
workflow, business rules, analyst API, admin restrictions).

## 8. Deployment and operations

See README sections *Local setup* and *Deployment*. Health check: `GET /health` returns the database
status. Database backups are handled by Supabase (daily). To rotate the server role password run
`alter role agrocast_app password '...'` and update `DATABASE_URL` in Render.

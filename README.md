# AGROCAST — Agricultural Input Purchase Planning

Web information system for producers and technical teams in Colombia. It manages farms and input
purchase plans and evaluates every plan with the SARIMA model selected in Activity 1, which forecasts
the official UPRA agricultural input price index six months ahead.

**Published system:** https://agrocast-ay0u.onrender.com (health check: `/health`)

- Public homepage with the project documentation and a **CRISP-ML** menu with the six phases
  (business and data understanding, data engineering, model engineering, evaluation, deployment,
  monitoring), each with its actual work, figures and results.
- Public model demonstration (`/crisp-ml/deployment`) that accepts new monthly values and returns a forecast.
- Food prices from DANE SIPSA for 375 products (daily bulletin for fresh produce, monthly series for the
  rest) with a 3-month forecast per product, a price calculator for shopping lists (cheapest market,
  median cost, saving) and a PDF of the cheapest purchase. Data refreshed every day by GitHub Actions.
- Privacy policy (Law 1581 of 2012), terms of use, cookie policy and notice, self-service download and
  deletion of personal data, CSP with nonces and other security headers.
- Registration, login and logout; passwords stored as salted scrypt hashes; CSRF protection on every form.
- Three roles enforced on the server: **producer**, **analyst** and **admin**.
- Persistent PostgreSQL database (Supabase, schema `agrocast`).
- Complete purchase workflow: farm → plan → products priced with SIPSA-I → model evaluation →
  decision → purchase with actual prices → closed.
- Server-side inference with a versioned model artifact, inference history (date, user, input,
  result, model version, response time) and monitoring (forecast vs actual, drift checks, errors).

Course: Machine Learning · Systems Engineering · University of Cundinamarca, Chía extension.

## Repository structure

```text
app.py                     Flask entry point, public pages and market views
agrosys/                   information system
  db.py                    SQLAlchemy data model
  security.py              authentication, permission matrix, CSRF
  services.py              business rules, inference + history, monitoring, event log
  views_auth.py            register / login / logout / user administration
  views_ops.py             farms, purchase plans, decisions, history, incidents
  views_monitor.py         index records, forecast lab, API, monitoring, indicators, model versions, logs
  views_crisp.py           public CRISP-ML phase pages and model demonstration
  seed.py                  flask CLI: init-db, seed, create-user
src/                       machine learning pipeline
  ingest.py, prepare.py    download official sources, clean and align the monthly data
  models.py, evaluate.py   candidate models, rolling-origin backtest, selection, intervals
  pipeline.py              runs preparation, modeling and evaluation -> reports/
  evidence.py              CRISP-ML evidence (before/after data, partitions, tuning, diagnostics) -> reports/crisp/
  train_model.py           packages the selected SARIMA as models/sarima_v<version>.joblib
  inference.py             server-side inference, recommendation rule, drift check
  monitoring_test.py       initial monitoring test with simulated data -> reports/crisp/
reports/                   data quality, backtest, metrics, results, figures and CRISP-ML evidence
models/                    versioned model artifacts and model cards
supabase/migrations/       database schema (public = pipeline results, agrocast = information system)
supabase/app_role.example.sql   least-privilege login role for the server
web/templates, web/static  user interface
tests/                     pipeline tests and information system integration tests
docs/                      technical manual, user manual, training guide, support and monitoring procedures
render.yaml                deployment blueprint
```

## Reproduce the machine learning project

```bash
python -m src.pipeline --actualizar-fuentes   # download UPRA, TRM, Pink Sheet and SIPSA; prepare; backtest; select
python -m src.evidence                        # CRISP-ML evidence: reports/crisp/evidence.json, experiments.csv, figures
python -m src.train_model --version 1.1.0     # model artifact + model card in models/
python -m src.monitoring_test                 # initial monitoring test (SIMULATED data)
python -m src.food_prices build               # food prices (DANE SIPSA daily + monthly) and their forecasts
python -m pytest -q                           # leakage, pipeline and information system tests
```

The first command needs internet access; the large raw files (SIPSA-I and wholesale prices, about 63 MB)
are downloaded to `data/raw/` and are not stored in the repository. The UPRA annex and the TRM file are
included, so the official index used for training is versioned. Latest official index month: August 2026
(checked on 16 November 2026).

Current results (validation / independent test MASE): persistence 4.321 / 1.715, ETS 3.545 / 1.837,
**SARIMA 3.324 / 1.837 (selected)**, Ridge 4.420 / 2.138. See [CRISP-ML phases](docs/crisp_ml_phases.md).

## Local setup

Requirements: Python 3.13.

```bash
python -m venv venv
venv\Scripts\activate            # Windows  (source venv/bin/activate on Linux/macOS)
pip install -r requirements.txt
copy .env.example .env           # then edit the values
```

### Option A — local SQLite database (quick start)

Leave `DATABASE_URL` empty in `.env`.

```bash
flask --app app seed                                   # creates tables and loads index, prices and model version
flask --app app create-user --email admin@example.com --name "Admin" --role admin
flask --app app run
```

### Option B — Supabase PostgreSQL (production)

1. Apply the migrations in `supabase/migrations/` (the last one creates the `agrocast` schema):
   `supabase db push` or run the SQL files in the Supabase SQL editor.
2. Create the server role with `supabase/app_role.example.sql` (change the password first).
3. Set `DATABASE_URL` in `.env`:
   `postgresql+psycopg://agrocast_app.<project-ref>:<password>@aws-1-us-east-1.pooler.supabase.com:5432/postgres`
4. Run `flask --app app seed` and create the administrator with `flask --app app create-user`.

## Tests

```bash
python -m pytest
```

`tests/test_system.py` uses a temporary SQLite database and checks protected routes, CSRF, role
escalation on registration, the complete purchase workflow, business rules, the inference API and
administrator restrictions.

## Model

The production model is SARIMA (lowest validation MASE among ETS, SARIMA and Ridge in Activity 1).
`python -m src.train_model --version 1.1.0` repeats the specification search used in the evaluation
(log transform, d = 1, AICc grid) on the full series and freezes the parameters. The web server only
applies those parameters to the stored records, so the forecast of version 1.1.0 (index 149.628 for
February 2027) matches `reports/resultados.json`. Version 1.0.0 (first release) is kept for traceability;
1.1.0 was packaged on 16 November 2026 after refreshing the external sources and is the active version.

To publish a new version: run the pipeline, package a new version, `flask --app app seed`
(registers it as inactive), deploy, and activate it in **Admin → Model versions**.

## Deployment (Render)

The repository includes `render.yaml`.

1. In Render: **New → Blueprint**, select this repository (branch `main`).
2. Set `DATABASE_URL` (the Supabase pooler URL above). `FLASK_SECRET_KEY` is generated automatically.
3. Build: `pip install -r requirements.txt` · Start: `gunicorn app:app` · Health check: `/health`.
4. After the first deploy, open `/health` — it must report `"database": "ok"`.

## Daily data update

`.github/workflows/update-prices.yml` runs every day (7:00 a.m. and 4:30 p.m. Colombia) and loads the
latest DANE food prices, forecasts, UPRA index months and SIPSA-I input prices into the database. Add the
repository secret **`DATABASE_URL`** (Settings → Secrets and variables → Actions) with the same value used
in Render. It can also be started by hand from the Actions tab (*Run workflow*).

## Configuration

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection (empty = local SQLite) |
| `FLASK_SECRET_KEY` | session signing key (required in production) |
| `SESSION_COOKIE_SECURE` | `1` when served over HTTPS |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | only for `src/load_supabase.py` (pipeline results) |

No secrets are stored in the repository. Test account credentials are delivered privately.

## Documentation

- [Technical manual](docs/technical_manual.md)
- [User manual](docs/user_manual.md)
- [Training guide](docs/training_guide.md)
- [Support, incidents and feedback](docs/support_procedures.md)
- [Monitoring and maintenance](docs/monitoring_maintenance.md)
- [CRISP-ML phases](docs/crisp_ml_phases.md)
- [Data dictionary](docs/data_dictionary.md)

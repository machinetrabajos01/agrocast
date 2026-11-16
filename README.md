# AGROCAST — Agricultural Input Purchase Planning

Web information system for producers and technical teams in Colombia. It manages farms and input
purchase plans and evaluates every plan with the SARIMA model selected in Activity 1, which forecasts
the official UPRA agricultural input price index six months ahead.

- Public homepage with the project documentation (problem, objectives, CRISP-ML(Q), models, system design).
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
  seed.py                  flask CLI: init-db, seed, create-user
src/                       Activity 1 pipeline (ingestion, preparation, models, evaluation)
  train_model.py           packages the selected SARIMA as models/sarima_v<version>.joblib
  inference.py             server-side inference, recommendation rule, drift check
models/                    versioned model artifacts and model cards
supabase/migrations/       database schema (public = pipeline results, agrocast = information system)
supabase/app_role.example.sql   least-privilege login role for the server
web/templates, web/static  user interface
tests/                     pipeline tests and information system integration tests
docs/                      technical manual, user manual, training guide, support and monitoring procedures
render.yaml                deployment blueprint
```

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
`python -m src.train_model --version 1.0.0` repeats the specification search used in the evaluation
(log transform, d = 1, AICc grid) on the full series and freezes the parameters. The web server only
applies those parameters to the stored records, so the forecast of version 1.0.0 matches the one
reported by the Activity 1 pipeline.

To publish a new version: run the pipeline, package a new version, `flask --app app seed`
(registers it as inactive), deploy, and activate it in **Admin → Model versions**.

## Deployment (Render)

The repository includes `render.yaml`.

1. In Render: **New → Blueprint**, select this repository (branch `main`).
2. Set `DATABASE_URL` (the Supabase pooler URL above). `FLASK_SECRET_KEY` is generated automatically.
3. Build: `pip install -r requirements.txt` · Start: `gunicorn app:app` · Health check: `/health`.
4. After the first deploy, open `/health` — it must report `"database": "ok"`.

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
- Activity 1 methodology: `docs/metodologia_crisp_ml.md`, `docs/diccionario_datos.md`

# AGROCAST — Agricultural Price Intelligence

Professional Flask platform for an academic machine-learning project focused on six-month agricultural input price forecasting in Colombia.

## What changed in this version

- Professional AgriTech/SaaS visual identity.
- Public landing page separated from the private intelligent platform.
- Dedicated Project menu: Problem & Objectives, CRISP-ML(Q), Documentation.
- Dedicated Models menu: Overview, ETS, SARIMA and Ridge Regression.
- Each model has its own explanation, real evaluation metrics and implementation evidence.
- Intelligent Platform separated into dashboard, buying signals, market views and price evolution.
- Forecast & Monitoring separated into six-month forecast, alerts, reports and model performance.
- Flask backend connects the UI to the existing real forecasting pipeline and processed datasets.
- Local academic demo login works without requiring Supabase for the classroom demonstration.
- Existing Supabase migrations and data pipeline are preserved for later integration.

## Academic demo login

Email: `demo@agrocast.local`

Password: `Agro2026!`

Change these values with `DEMO_EMAIL`, `DEMO_PASSWORD` and `FLASK_SECRET_KEY` environment variables before any real deployment.

## Run locally

```bash
python -m venv venv
# Windows PowerShell
.\\venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`.

## Run the real modeling pipeline

The original project engine remains under `src/`. To regenerate the processed results with the files already included:

```bash
python -m pytest
python -m src.pipeline --sin-preparar
```

For a fresh source download, when the source files and internet access are available:

```bash
python -m src.pipeline --actualizar-fuentes
```

The web application reads the resulting `reports/resultados.json` and processed CSV files.

## Models

- **ETS** — exponential smoothing state-space model.
- **SARIMA** — autoregressive integrated moving average with optional seasonality.
- **Ridge Regression** — direct regression by horizon with lagged index changes and external variables.
- **Naive / Seasonal Naive** — reference baselines used for evaluation.

The selection rule is defined in the evaluation pipeline: candidates are compared on temporal validation and the final test block remains separate.

## Project structure

```text
agroinsumos-pronostico/
├── app.py
├── data/
│   ├── raw/
│   └── processed/
├── src/
├── models/
├── reports/
├── docs/
├── supabase/
├── tests/
├── web/
│   ├── templates/
│   └── static/
└── requirements.txt
```

## Important academic limitation

The national index is an aggregate planning signal. It is not a guaranteed transaction price for a specific farmer, crop, region or commercial presentation. Product recommendations are decision-support rules and should be interpreted together with current quotations and local conditions.

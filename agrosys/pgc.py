"""PGC documentation shown on the homepage, the user documentation pages and the project resources.

The same lists are used by the final technical report, so the homepage and the report stay consistent.
"""
from __future__ import annotations

import os
from pathlib import Path

import markdown
from flask import abort, render_template, url_for
from markupsafe import Markup

ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/machinetrabajos01/agrocast"
REPORT_FILE = "docs/AGROCAST_Final_Technical_Report.pdf"   # inside web/static

DOCS = {
    "user-manual": ("User manual", "user_manual.md"),
    "training-guide": ("Training guide", "training_guide.md"),
    "support": ("Incidents and feedback", "support_procedures.md"),
    "technical-manual": ("Technical manual", "technical_manual.md"),
    "crisp-ml": ("CRISP-ML phases", "crisp_ml_phases.md"),
    "monitoring": ("Monitoring and maintenance", "monitoring_maintenance.md"),
    "data-dictionary": ("Data dictionary", "data_dictionary.md"),
}

MEMBERS = {
    "JP": "Jean Pierre González Espinel",
    "DC": "Daniel Camilo Ruiz Rodriguez",
    "DP": "Daniel Páez",
}

# Activity, responsible, planned, actual, deliverable.
SCHEDULE = [
    ("Project proposal and approval", "All members", "Before 2026-11-07", "Completed before 2026-11-07",
     "Approved CRISP-ML(Q) proposal"),
    ("Phase 1: business and data understanding", "Jean Pierre González", "2026-11-07", "2026-11-07",
     "Problem, stakeholders, sources, success criteria"),
    ("Phase 2: data engineering (UPRA, SIPSA-I, TRM, Pink Sheet)", "Daniel Camilo Ruiz", "2026-11-07 to 2026-11-09",
     "2026-11-07; sources refreshed 2026-11-12 and 2026-11-16", "Processed data, quality report"),
    ("Phases 3-4: model engineering and evaluation", "Daniel Camilo Ruiz, Jean Pierre González",
     "2026-11-09 to 2026-11-12", "2026-11-07 to 2026-11-12; evidence and version 1.1.0 on 2026-11-16",
     "Backtest, metrics, experiment log"),
    ("Activity 1 (R3A2): CRISP-ML Flask application and report", "All members", "2026-11-12 to 2026-11-14",
     "2026-11-14 (platform) and 2026-11-16 (phase pages, report)", "R3A2 Traces in Data report, phase pages"),
    ("Activity 2 (R3A3): information system (database, authentication, roles, processes, inference)",
     "Daniel Camilo Ruiz, Daniel Páez", "2026-11-12 to 2026-11-15", "2026-11-16", "Published system, manuals"),
    ("Deployment on Render and Supabase, operational review", "Daniel Camilo Ruiz", "2026-11-15", "2026-11-16",
     "Published URL, health check, review"),
    ("Testing and validation with the three roles", "Daniel Páez", "2026-11-15", "2026-11-16",
     "30 automated tests, end-to-end checks"),
    ("Food price module, legal pages and daily update (added after user testing)", "Daniel Páez, Daniel Camilo Ruiz",
     "Not planned initially", "2026-11-16 to 2026-11-17", "Price calculator, PDF, privacy and cookie policies"),
    ("Activity 3 (R3A4): PGC documentation and final report", "Jean Pierre González", "2026-11-12 to 2026-11-16",
     "2026-11-17", "Homepage documentation, final technical report"),
    ("Presentation video in English (20 minutes)", "All members", "2026-11-16", "Recorded by the team",
     "Video published on the homepage"),
]

# Contributions of each member (also printed in the final report).
CONTRIBUTIONS = {
    "Jean Pierre González Espinel": [
        "Business and data understanding: problem, stakeholders, objectives and success criteria.",
        "Reference framework, bibliographic references and PGC documentation.",
        "Analysis of the evaluation results (validation, independent test, bias and limitations).",
        "Monitoring plan, consolidation of the technical reports.",
    ],
    "Daniel Camilo Ruiz Rodriguez": [
        "Data engineering pipeline (UPRA, SIPSA-I, TRM, Pink Sheet) and leakage-free partitions.",
        "Model engineering: ETS, SARIMA and Ridge, tuning, backtest and model packaging.",
        "Information system back end: database, authentication, roles and server-side inference.",
        "Deployment on Render and Supabase and the daily data update job.",
    ],
    "Daniel Páez": [
        "Web interface and user experience of the information system and the CRISP-ML pages.",
        "Food price module: catalog, price calculator and PDF of the cheapest purchase.",
        "Testing and validation with the three roles; privacy, cookie and terms pages.",
        "User manual, training guide and incident and feedback procedures.",
    ],
}

REFERENCES = [
    "Box, G. E. P., Jenkins, G. M., Reinsel, G. C., & Ljung, G. M. (2015). Time series analysis: Forecasting and control (5th ed.). Wiley.",
    "Congreso de la República de Colombia. (2012). Ley Estatutaria 1581 de 2012, por la cual se dictan disposiciones generales para la protección de datos personales. Diario Oficial No. 48.587.",
    "Departamento Administrativo Nacional de Estadística. (2026a). Sistema de Información de Precios y Abastecimiento del Sector Agropecuario (SIPSA): Precios mayoristas, boletín diario y series históricas [Data set]. https://www.dane.gov.co/index.php/estadisticas-por-tema/agropecuario/sistema-de-informacion-de-precios-sipsa",
    "Departamento Administrativo Nacional de Estadística. (2026b). SIPSA: Insumos y factores asociados a la producción agropecuaria, series históricas por departamento [Data set]. https://www.dane.gov.co",
    "Diebold, F. X., & Mariano, R. S. (1995). Comparing predictive accuracy. Journal of Business & Economic Statistics, 13(3), 253-263. https://doi.org/10.1080/07350015.1995.10524599",
    "Food and Agriculture Organization of the United Nations. (2022). The importance of Ukraine and the Russian Federation for global agricultural markets and the risks associated with the war in Ukraine [Information note]. FAO.",
    "Harvey, D., Leybourne, S., & Newbold, P. (1997). Testing the equality of prediction mean squared errors. International Journal of Forecasting, 13(2), 281-291. https://doi.org/10.1016/S0169-2070(96)00719-4",
    "Hewamalage, H., Ackermann, K., & Bergmeir, C. (2023). Forecast evaluation for data scientists: Common pitfalls and best practices. Data Mining and Knowledge Discovery, 37, 788-832. https://doi.org/10.1007/s10618-022-00894-5",
    "Hoerl, A. E., & Kennard, R. W. (1970). Ridge regression: Biased estimation for nonorthogonal problems. Technometrics, 12(1), 55-67. https://doi.org/10.1080/00401706.1970.10488634",
    "Hyndman, R. J., & Athanasopoulos, G. (2021). Forecasting: Principles and practice (3rd ed.). OTexts. https://otexts.com/fpp3/",
    "Hyndman, R. J., & Koehler, A. B. (2006). Another look at measures of forecast accuracy. International Journal of Forecasting, 22(4), 679-688. https://doi.org/10.1016/j.ijforecast.2006.03.001",
    "Hyndman, R. J., Koehler, A. B., Snyder, R. D., & Grose, S. (2002). A state space framework for automatic forecasting using exponential smoothing methods. International Journal of Forecasting, 18(3), 439-454. https://doi.org/10.1016/S0169-2070(01)00110-8",
    "Jha, G. K., & Sinha, K. (2014). Time-delay neural networks for time series prediction: An application to the monthly wholesale price of oilseeds in India. Neural Computing and Applications, 24(3-4), 563-571.",
    "Makridakis, S., Spiliotis, E., & Assimakopoulos, V. (2018). Statistical and machine learning forecasting methods: Concerns and ways forward. PLoS ONE, 13(3), e0194889. https://doi.org/10.1371/journal.pone.0194889",
    "Makridakis, S., Spiliotis, E., & Assimakopoulos, V. (2020). The M4 Competition: 100,000 time series and 61 forecasting methods. International Journal of Forecasting, 36(1), 54-74. https://doi.org/10.1016/j.ijforecast.2019.04.014",
    "OWASP Foundation. (2021). OWASP Top 10:2021. https://owasp.org/Top10/",
    "Pedregosa, F., Varoquaux, G., Gramfort, A., Michel, V., Thirion, B., Grisel, O., Blondel, M., Prettenhofer, P., Weiss, R., Dubourg, V., Vanderplas, J., Passos, A., Cournapeau, D., Brucher, M., Perrot, M., & Duchesnay, É. (2011). Scikit-learn: Machine learning in Python. Journal of Machine Learning Research, 12, 2825-2830.",
    "Seabold, S., & Perktold, J. (2010). Statsmodels: Econometric and statistical modeling with Python. In Proceedings of the 9th Python in Science Conference (pp. 92-96). https://doi.org/10.25080/Majora-92bf1922-011",
    "Studer, S., Bui, T. B., Drescher, C., Hanuschkin, A., Winkler, L., Peters, S., & Müller, K.-R. (2021). Towards CRISP-ML(Q): A machine learning process model with quality assurance methodology. Machine Learning and Knowledge Extraction, 3(2), 392-413. https://doi.org/10.3390/make3020020",
    "Superintendencia Financiera de Colombia. (2026). Tasa de cambio representativa del mercado (TRM) [Data set]. Datos Abiertos Colombia. https://www.datos.gov.co/resource/mcec-87by",
    "Tashman, L. J. (2000). Out-of-sample tests of forecasting accuracy: An analysis and review. International Journal of Forecasting, 16(4), 437-450. https://doi.org/10.1016/S0169-2070(00)00065-0",
    "Unidad de Planificación Rural Agropecuaria. (2026). Índice de precios de insumos agrícolas [Data set]. Datos Abiertos Colombia. https://www.datos.gov.co/Agricultura-y-Desarrollo-Rural/-ndice-de-precios-de-insumos-agr-colas/gwbi-fnzs",
    "Vovk, V., Gammerman, A., & Shafer, G. (2005). Algorithmic learning in a random world. Springer.",
    "World Bank. (2022). Commodity markets outlook: The impact of the war in Ukraine on commodity markets (April 2022). World Bank.",
    "World Bank. (2026). World Bank commodity price data (The Pink Sheet) [Data set]. https://www.worldbank.org/en/research/commodity-markets",
]


def video_url() -> str:
    """Link of the presentation video, set in the hosting environment (PRESENTATION_VIDEO_URL)."""
    url = (os.getenv("PRESENTATION_VIDEO_URL") or "").strip()
    return url if url.startswith("https://") else ""


def resources():
    report = ROOT / "web" / "static" / REPORT_FILE
    return [
        {"icon": "bi-file-earmark-pdf", "title": "Final technical report (PDF)",
         "text": "Consolidated report of the three activities.",
         "href": url_for("static", filename=REPORT_FILE) if report.exists() else "", "external": False},
        {"icon": "bi-diagram-3", "title": "CRISP-ML evidence",
         "text": "Six phases with data, experiments, evaluation, deployment and monitoring.",
         "href": url_for("crisp_phase", phase="business-data"), "external": False},
        {"icon": "bi-github", "title": "Source code repository",
         "text": "Code, training and evaluation scripts, database setup and README.", "href": REPO_URL, "external": True},
        {"icon": "bi-box-arrow-in-right", "title": "Published system",
         "text": "Sign in to the private information system (accounts per role).", "href": url_for("login"), "external": False},
        {"icon": "bi-journal-text", "title": "User documentation",
         "text": "User manual, training guide, support procedures and technical manual.",
         "href": url_for("user_docs", slug="user-manual"), "external": False},
        {"icon": "bi-play-btn", "title": "Presentation video (English, 20 min)",
         "text": "Team presentation and defense of the project.", "href": video_url(), "external": True},
    ]


def register_routes(app):
    @app.context_processor
    def _pgc_context():
        return {"schedule": SCHEDULE, "references": REFERENCES, "pgc_resources": resources,
                "video_url": video_url, "repo_url": REPO_URL}

    @app.route("/project/pgc")
    def pgc():
        return render_template("pgc/page.html", title="PGC documentation")

    @app.route("/docs/<slug>")
    def user_docs(slug):
        if slug not in DOCS:
            abort(404)
        title, filename = DOCS[slug]
        text = (ROOT / "docs" / filename).read_text(encoding="utf-8")
        html = Markup(markdown.markdown(text, extensions=["tables", "fenced_code"]))
        return render_template("pgc/doc.html", title=title, html=html, docs=DOCS, slug=slug)

"""Package the model selected in Activity 1 (SARIMA) as a versioned artifact for the web system.

The specification search is the same one used during evaluation (src/models.py): log transform,
d = 1, AICc over the non-seasonal grid and the optional (1,0,0,12) seasonal term. The fitted
parameters are frozen in the artifact, so the web application applies the trained model to the
latest records without re-estimating it.

Usage: python -m src.train_model --version 1.0.0
"""
from __future__ import annotations

import argparse
import hashlib
import json
import warnings
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX

from . import config as c
from .models import GRILLA_ARIMA, GRILLA_ESTACIONAL

MODELS_DIR = c.RAIZ / "models"
SERIES_COLUMNS = {"total": "T_Índice", "fertilizantes": "T_Fertilizantes", "plaguicidas": "T_Plaguicidas"}


def load_index() -> pd.DataFrame:
    df = pd.read_csv(c.PROCESSED / "indice_mensual.csv", parse_dates=["fecha"], index_col="fecha").asfreq("MS")
    return df[list(SERIES_COLUMNS.values())].rename(columns={v: k for k, v in SERIES_COLUMNS.items()})


def fit_series(y: pd.Series) -> dict:
    log_y = np.log(y.dropna())
    best, best_aicc, best_spec = None, np.inf, None
    for order in GRILLA_ARIMA:
        for seasonal in GRILLA_ESTACIONAL:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    fit = SARIMAX(log_y, order=order, seasonal_order=seasonal, trend="n",
                                  enforce_stationarity=True, enforce_invertibility=True).fit(disp=False, maxiter=200)
                except Exception:
                    continue
            if np.isfinite(fit.aicc) and fit.aicc < best_aicc:
                best, best_aicc, best_spec = fit, fit.aicc, (order, seasonal)
    changes = log_y.diff().dropna()
    return {
        "order": list(best_spec[0]),
        "seasonal_order": list(best_spec[1]),
        "params": {k: float(v) for k, v in best.params.items()},
        "aicc": round(float(best_aicc), 3),
        "n_obs": int(len(log_y)),
        "train_start": log_y.index[0].strftime("%Y-%m-%d"),
        "train_end": log_y.index[-1].strftime("%Y-%m-%d"),
        # Reference distribution of monthly log changes, used by the input drift check.
        "reference_changes": {"mean": float(changes.mean()), "std": float(changes.std()),
                              "last_values": [round(float(v), 6) for v in changes.tail(36)]},
        "last_value": float(y.dropna().iloc[-1]),
    }


def build(version: str) -> dict:
    index = load_index()
    results = json.loads((c.REPORTES / "resultados.json").read_text(encoding="utf-8"))
    source = c.PROCESSED / "indice_mensual.csv"
    artifact = {
        "version": version,
        "algorithm": "SARIMA",
        "transform": "log",
        "frequency": "MS",
        "horizon": c.HORIZONTE,
        "preprocessing": [
            "Parse month to the first day of the month (frequency MS); keep the last value of duplicated months",
            "Reject gaps, non-positive values and series shorter than 36 months",
            "Natural logarithm of the index; differencing (d = 1) is part of the SARIMA specification",
            "Forecasts and intervals are transformed back with the exponential function",
        ],
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_cutoff": index.dropna(how="all").index[-1].strftime("%Y-%m-%d"),
        "data_fingerprint": hashlib.sha256(source.read_bytes()).hexdigest()[:16],
        "selection": {
            "rule": "Lowest validation MASE among ETS, SARIMA and Ridge (rolling origin, 6-month horizon)",
            "selected": results.get("modelo_seleccionado"),
            "metrics": results.get("resumen", {}).get("sarima", {}),
            "baseline": results.get("resumen", {}).get("naive", {}),
            "coverage_test_pct": results.get("cobertura_prueba_pct", {}),
        },
        "series": {name: fit_series(index[name]) for name in SERIES_COLUMNS},
    }
    return artifact


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="1.0.0")
    args = parser.parse_args()
    MODELS_DIR.mkdir(exist_ok=True)
    artifact = build(args.version)
    path = MODELS_DIR / f"sarima_v{args.version}.joblib"
    joblib.dump(artifact, path)
    card = {k: v for k, v in artifact.items() if k != "series"}
    card["series"] = {k: {kk: vv for kk, vv in v.items() if kk not in ("params", "reference_changes")}
                      for k, v in artifact["series"].items()}
    (MODELS_DIR / f"sarima_v{args.version}.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    print(f"Saved {path.name}")
    for name, spec in artifact["series"].items():
        print(f"  {name}: order={spec['order']} seasonal={spec['seasonal_order']} AICc={spec['aicc']}")


if __name__ == "__main__":
    main()

"""Server-side inference with the versioned SARIMA artifact.

The same preprocessing used in training is applied here: monthly frequency (month start),
natural logarithm of the index and the frozen SARIMA specification and parameters. The model is
not re-estimated; the Kalman filter is run with the trained parameters over the records
available in the system, so new monthly observations update the forecast origin.
"""
from __future__ import annotations

import time
import warnings
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
SERIES_LABELS = {
    "total": "National agricultural input index",
    "fertilizantes": "Fertilizer index",
    "plaguicidas": "Pesticide index",
}
# Product categories of the purchase plans and the index series that represents each one.
CATEGORY_SERIES = {
    "Fertilizantes": "fertilizantes",
    "Fungicidas": "plaguicidas",
    "Herbicidas": "plaguicidas",
    "Insecticidas": "plaguicidas",
}
MIN_OBSERVATIONS = 36
BUY_THRESHOLD_PCT = 3.0     # expected rise that justifies buying in advance
WAIT_THRESHOLD_PCT = -3.0   # expected fall that justifies deferring the purchase


class InferenceError(ValueError):
    """Raised when the input does not satisfy the structure used during training."""


@lru_cache(maxsize=4)
def load_artifact(version: str) -> dict:
    path = MODELS_DIR / f"sarima_v{version}.joblib"
    if not path.exists():
        raise InferenceError(f"Model artifact {path.name} was not found.")
    return joblib.load(path)


def available_versions() -> list[str]:
    return sorted(p.stem.replace("sarima_v", "") for p in MODELS_DIR.glob("sarima_v*.joblib"))


def validate_history(history: pd.Series) -> pd.Series:
    if history is None or len(history) == 0:
        raise InferenceError("No index observations are available for this series.")
    y = history.sort_index().astype(float)
    y.index = pd.to_datetime(y.index).to_period("M").to_timestamp()
    y = y[~y.index.duplicated(keep="last")]
    if (y <= 0).any():
        raise InferenceError("Index values must be strictly positive (the model works on the logarithm).")
    full = y.asfreq("MS")
    if full.isna().any():
        missing = ", ".join(d.strftime("%Y-%m") for d in full[full.isna()].index[:6])
        raise InferenceError(f"The monthly series has gaps ({missing}). Register the missing months first.")
    if len(full) < MIN_OBSERVATIONS:
        raise InferenceError(f"At least {MIN_OBSERVATIONS} consecutive monthly observations are required.")
    return full


def forecast(history: pd.Series, series: str, horizon: int, version: str) -> dict:
    """Return the forecast path, 80/95 % intervals and the change against the last observation."""
    if series not in SERIES_LABELS:
        raise InferenceError("Unknown series.")
    artifact = load_artifact(version)
    if not 1 <= int(horizon) <= artifact["horizon"]:
        raise InferenceError(f"The horizon must be between 1 and {artifact['horizon']} months.")
    spec = artifact["series"][series]
    y = validate_history(history)
    started = time.perf_counter()
    log_y = np.log(y)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = SARIMAX(log_y, order=tuple(spec["order"]), seasonal_order=tuple(spec["seasonal_order"]),
                        trend="n", enforce_stationarity=True, enforce_invertibility=True)
        res = model.filter(pd.Series(spec["params"])[model.param_names].to_numpy())
        fc = res.get_forecast(int(horizon))
        mean = np.exp(fc.predicted_mean)
        ci80 = np.exp(fc.conf_int(alpha=0.20))
        ci95 = np.exp(fc.conf_int(alpha=0.05))
    last = float(y.iloc[-1])
    path = []
    for i, date in enumerate(mean.index):
        path.append({
            "month": date.strftime("%Y-%m"), "horizon": i + 1, "value": round(float(mean.iloc[i]), 3),
            "lo80": round(float(ci80.iloc[i, 0]), 3), "hi80": round(float(ci80.iloc[i, 1]), 3),
            "lo95": round(float(ci95.iloc[i, 0]), 3), "hi95": round(float(ci95.iloc[i, 1]), 3),
        })
    end = path[-1]
    change = (end["value"] / last - 1) * 100
    return {
        "series": series,
        "series_label": SERIES_LABELS[series],
        "model_version": artifact["version"],
        "algorithm": artifact["algorithm"],
        "origin_month": y.index[-1].strftime("%Y-%m"),
        "last_value": round(last, 3),
        "horizon": int(horizon),
        "target_month": end["month"],
        "predicted_value": end["value"],
        "change_pct": round(change, 2),
        "change_lo80_pct": round((end["lo80"] / last - 1) * 100, 2),
        "change_hi80_pct": round((end["hi80"] / last - 1) * 100, 2),
        "path": path,
        "observations_used": int(len(y)),
        "compute_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def recommend(result: dict) -> dict:
    """Translate the forecast into the purchasing decision supported by the system."""
    change, lo, hi = result["change_pct"], result["change_lo80_pct"], result["change_hi80_pct"]
    if change >= BUY_THRESHOLD_PCT:
        code, label = "buy_now", "Buy now"
        reason = (f"The {result['series_label'].lower()} is expected to rise {change:.1f}% by {result['target_month']}. "
                  "Buying in advance protects the plan budget.")
    elif change <= WAIT_THRESHOLD_PCT:
        code, label = "defer", "Defer purchase"
        reason = (f"The {result['series_label'].lower()} is expected to fall {abs(change):.1f}% by {result['target_month']}. "
                  "Waiting may reduce the cost of the plan.")
    else:
        code, label = "as_planned", "Buy as planned"
        reason = (f"The expected change ({change:+.1f}%) is inside the ±{BUY_THRESHOLD_PCT:.0f}% band, "
                  "so the forecast does not justify moving the purchase date.")
    confidence = "low" if lo < 0 < hi else "moderate"
    if confidence == "low":
        reason += " The 80% interval includes both rises and falls, so treat this signal with caution."
    return {"code": code, "label": label, "reason": reason, "confidence": confidence}


def drift_check(history: pd.Series, series: str, version: str, window: int = 12) -> dict:
    """Compare recent monthly log changes with the reference distribution stored at training time."""
    artifact = load_artifact(version)
    ref = artifact["series"][series]["reference_changes"]
    y = validate_history(history)
    changes = np.log(y).diff().dropna()
    new = changes[changes.index > pd.Timestamp(artifact["series"][series]["train_end"])]
    recent = changes.tail(window)
    z = (recent.mean() - ref["mean"]) / (ref["std"] / np.sqrt(len(recent))) if ref["std"] > 0 else 0.0
    vol_ratio = recent.std() / ref["std"] if ref["std"] > 0 else 1.0
    status = "ok"
    if abs(z) > 3 or vol_ratio > 2 or vol_ratio < 0.33:
        status = "alert"
    elif abs(z) > 2 or vol_ratio > 1.5:
        status = "warning"
    return {
        "series": series, "reference_mean": ref["mean"], "reference_std": ref["std"],
        "recent_mean": float(recent.mean()), "recent_std": float(recent.std()),
        "z_score": round(float(z), 3), "volatility_ratio": round(float(vol_ratio), 3),
        "new_months_since_training": int(len(new)), "status": status,
    }

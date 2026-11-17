"""Initial monitoring test with SIMULATED operational data.

No official index value after the data cut-off exists yet, so the next six months are simulated in two
scenarios and passed through the same checks used in operation: one-step forecast error, 80 % interval
coverage and the drift check of the model artifact. Every record is labelled data_type = "SIMULATED".

Usage: python -m src.monitoring_test [--version 1.1.0]
Outputs: reports/crisp/monitoring_test.json and reports/crisp/monitoring_test_log.jsonl
"""
import argparse
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import config as c
from .inference import drift_check, forecast, load_artifact

OUT = c.REPORTES / "crisp"
APE_LIMIT = 5.0            # % one-step error that counts as a miss
CONSECUTIVE_MISSES = 2     # misses in a row that open a performance incident
SEED = 20261116
NOISE_SD = 0.01          # 1 % monthly noise around the model path (simulation assumption)


def simulate(history, version, scenario):
    """Builds six simulated months from the model path plus noise; the shock scenario adds +12 % in month 3."""
    rng = np.random.default_rng(SEED)
    base = forecast(history, "total", 6, version)["path"]
    values = []
    for i, p in enumerate(base):
        v = p["value"] * float(np.exp(rng.normal(0, NOISE_SD)))
        if scenario == "shock" and i >= 2:
            v *= 1.12
        values.append(round(v, 3))
    months = pd.to_datetime([p["month"] + "-01" for p in base])
    return pd.Series(values, index=months)


def run_scenario(history, version, scenario):
    simulated = simulate(history, version, scenario)
    y = history.copy()
    log, misses, incidents = [], 0, []
    for month, actual in simulated.items():
        pred = forecast(y, "total", 1, version)
        p = pred["path"][0]
        y = pd.concat([y, pd.Series([actual], index=[month])]).asfreq("MS")
        drift = drift_check(y, "total", version)
        ape = abs(actual / p["value"] - 1) * 100
        inside = p["lo80"] <= actual <= p["hi80"]
        misses = misses + 1 if ape > APE_LIMIT else 0
        actions = []
        if misses >= CONSECUTIVE_MISSES:
            actions.append("performance incident: review retraining")
        if drift["status"] == "alert":
            actions.append("drift alert: notify analyst and administrator")
        elif drift["status"] == "warning":
            actions.append("drift warning: review at the monthly meeting")
        record = {
            "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "data_type": "SIMULATED", "scenario": scenario, "month": month.strftime("%Y-%m"),
            "model_version": version, "predicted": p["value"], "lo80": p["lo80"], "hi80": p["hi80"],
            "simulated_actual": actual, "ape_pct": round(ape, 2), "inside_80": bool(inside),
            "drift_status": drift["status"], "drift_z": drift["z_score"],
            "volatility_ratio": drift["volatility_ratio"], "compute_ms": pred["compute_ms"],
            "actions": actions or ["none"],
        }
        log.append(record)
        if actions:
            incidents.extend(f"{record['month']}: {a}" for a in actions)
    return log, incidents


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.1.0")
    args = ap.parse_args()
    load_artifact(args.version)
    ind = pd.read_csv(c.PROCESSED / "indice_mensual.csv", parse_dates=["fecha"], index_col="fecha").asfreq("MS")
    history = ind[c.SERIES["total"]]
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {"data_type": "SIMULATED", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "model_version": args.version, "data_cutoff": history.index[-1].strftime("%Y-%m"),
               "simulation": {"noise_sd_log": NOISE_SD, "shock": "+12 % from the third simulated month", "seed": SEED},
               "rules": {"ape_limit_pct": APE_LIMIT, "consecutive_misses": CONSECUTIVE_MISSES,
                         "drift": "alert if |z| > 3 or volatility ratio > 2 or < 0.33; warning if |z| > 2 or ratio > 1.5"},
               "scenarios": {}}
    lines = []
    for scenario in ("stable", "shock"):
        log, incidents = run_scenario(history, args.version, scenario)
        lines.extend(log)
        apes = [r["ape_pct"] for r in log]
        summary["scenarios"][scenario] = {
            "months": len(log), "mape_pct": round(float(np.mean(apes)), 2),
            "coverage_80_pct": round(100 * float(np.mean([r["inside_80"] for r in log])), 1),
            "final_drift_status": log[-1]["drift_status"], "incidents": incidents, "log": log}
    (OUT / "monitoring_test.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (OUT / "monitoring_test_log.jsonl").write_text("\n".join(json.dumps(r) for r in lines) + "\n", encoding="utf-8")
    for s, v in summary["scenarios"].items():
        print(s, {k: v[k] for k in ("mape_pct", "coverage_80_pct", "final_drift_status", "incidents")})


if __name__ == "__main__":
    main()

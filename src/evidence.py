"""Evidence of the CRISP-ML phases: data before and after processing, partitions, tuning, diagnostics.

Run after the pipeline:  python -m src.evidence
Outputs: reports/crisp/evidence.json, reports/crisp/experiments.csv and reports/crisp/figures/*.png
"""
import json
import warnings
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.exponential_smoothing.ets import ETSModel
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.tsa.stattools import adfuller, kpss

from . import config as c
from . import evaluate as ev
from .models import ALFAS, GRILLA_ARIMA, GRILLA_ESTACIONAL, NOMBRES, construir_features

OUT = c.REPORTES / "crisp"
FIG = OUT / "figures"
GREEN, BLUE, RED, GOLD, GREY = "#1f5c4a", "#2f5f8f", "#b8435a", "#c2a14d", "#8a938e"
COLORS = {"naive": GREY, "snaive": GOLD, "ets": BLUE, "sarima": RED, "ridge": GREEN}
SHORT = {"naive": "Persistence", "snaive": "Seasonal naive", "ets": "ETS", "sarima": "SARIMA", "ridge": "Ridge"}

SOURCES = [
    {"name": "Agricultural input price index (UPRA annex and Datos Abiertos API gwbi-fnzs)",
     "provider": "UPRA - Ministry of Agriculture", "role": "Target variable (total, fertilizer and pesticide index)",
     "frequency": "Monthly", "use": "Selected: official, monthly, comparable since 2018"},
    {"name": "Representative market exchange rate (TRM, Datos Abiertos mcec-87by)", "provider": "Superintendencia Financiera",
     "role": "Exogenous feature (Ridge)", "frequency": "Daily, averaged to monthly",
     "use": "Selected: most inputs are imported and priced in dollars"},
    {"name": "World Bank Pink Sheet (urea and DAP prices)", "provider": "World Bank",
     "role": "Exogenous feature (Ridge)", "frequency": "Monthly",
     "use": "Selected: international fertilizer prices lead the domestic index"},
    {"name": "SIPSA-I input prices by department", "provider": "DANE",
     "role": "External validation of the index and product prices for the plans", "frequency": "Monthly",
     "use": "Selected for contrast and for product prices; not a model feature (shorter and irregular)"},
]


def _style(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=.25)


def _save(fig, name):
    fig.tight_layout()
    fig.savefig(FIG / f"{name}.png", dpi=150)
    plt.close(fig)


def raw_annex():
    d = pd.read_excel(c.ANEXO_UPRA, sheet_name="Indice_DetalladoAgricola", header=2)
    d = d.rename(columns={d.columns[0]: "fecha"})
    return d


def data_evidence(ind):
    raw = raw_annex()
    dates = pd.to_datetime(raw["fecha"], errors="coerce")
    numeric = raw.drop(columns="fecha").apply(pd.to_numeric, errors="coerce")
    valid = dates.notna()
    zeros = int((numeric[valid] == 0).sum().sum())
    cols = ["T_Índice", "T_Otros", "Clorpirifos", "Fipronil"]
    sample_dates = ["2020-12-01", "2021-01-01", "2023-12-01", "2024-01-01", "2024-08-01", "2024-09-01"]
    raw_idx = raw[valid].assign(fecha=dates[valid]).set_index("fecha")
    proc = pd.read_csv(c.PROCESSED / "indice_mensual.csv", parse_dates=["fecha"], index_col="fecha")
    before = [{"month": f[:7], **{k: (None if pd.isna(raw_idx.loc[f, k]) else float(raw_idx.loc[f, k])) for k in cols}}
              for f in sample_dates]
    after = []
    for f in sample_dates:
        row = {"month": f[:7]}
        for k in cols:
            v = proc.loc[f, k] if k in proc.columns else raw_idx.loc[f, k]
            row[k] = None if (pd.isna(v) or v == 0) else float(v)
        after.append(row)

    y = ind[c.SERIES["total"]]
    log_y = np.log(y)
    dlog = log_y.diff().dropna()
    # ADF with one lag (the AR order chosen by AICc) and KPSS (null hypothesis: stationary) as a cross-check.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tests = {"level": y, "log": log_y, "log_difference": dlog}
        adf = {name: round(float(adfuller(s.dropna(), maxlag=1, autolag=None)[1]), 4) for name, s in tests.items()}
        kpss_p = {name: round(float(kpss(s.dropna(), regression="c", nlags="auto")[1]), 3) for name, s in tests.items()}
    med = dlog.median()
    z = (dlog - med) / (1.4826 * (dlog - med).abs().median())
    outliers = [{"month": f.strftime("%Y-%m"), "change_pct": round(float(np.expm1(dlog[f]) * 100), 2),
                 "robust_z": round(float(z[f]), 2)} for f in z[z.abs() > 3].index]
    return {
        "raw_rows_with_valid_date": int(valid.sum()),
        "raw_rows_discarded": int((~valid).sum()),
        "raw_columns": int(numeric.shape[1]),
        "zeros_replaced_as_missing": zeros,
        "duplicated_months": int(dates[valid].duplicated().sum()),
        "processed_rows": int(len(ind)),
        "processed_columns": int(ind.shape[1]),
        "period": [ind.index.min().strftime("%Y-%m"), ind.index.max().strftime("%Y-%m")],
        "before_sample": before,
        "after_sample": after,
        "adf_p_values": adf,
        "kpss_p_values": kpss_p,
        "outliers": outliers,
        "describe_total": {k: round(float(v), 3) for k, v in y.describe().items()},
        "describe_log_change_pct": {k: round(float(v) * (100 if k not in ("count",) else 1), 3)
                                     for k, v in dlog.describe().items()},
    }


def partitions(y):
    val, test = ev.origenes(y)
    first_train_end = y.index[val[0]]
    return {
        "observations": int(len(y)),
        "minimum_training_months": c.MIN_ENTRENAMIENTO,
        "horizon": c.HORIZONTE,
        "training_initial": [y.index[0].strftime("%Y-%m"), first_train_end.strftime("%Y-%m")],
        "validation_origins": [y.index[val[0]].strftime("%Y-%m"), y.index[val[-1]].strftime("%Y-%m"), len(val)],
        "test_origins": [y.index[test[0]].strftime("%Y-%m"), y.index[test[-1]].strftime("%Y-%m"), len(test)],
        "test_targets": [y.index[test[0] + 1].strftime("%Y-%m"), y.index[test[-1] + c.HORIZONTE].strftime("%Y-%m")],
        "final_refit": [y.index[0].strftime("%Y-%m"), y.index[-1].strftime("%Y-%m")],
        "forecasts_per_model": {"validation": len(val) * c.HORIZONTE, "test": len(test) * c.HORIZONTE},
    }


def sarima_grid(y):
    rows = []
    log_y = np.log(y)
    for order in GRILLA_ARIMA:
        for seas in GRILLA_ESTACIONAL:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    fit = SARIMAX(log_y, order=order, seasonal_order=seas, trend="n", enforce_stationarity=True,
                                  enforce_invertibility=True).fit(disp=False, maxiter=200)
                    aicc = float(fit.aicc)
                except Exception:
                    aicc = float("nan")
            rows.append({"order": str(order), "seasonal_order": str(seas), "aicc": round(aicc, 3)})
    return sorted(rows, key=lambda r: (np.isnan(r["aicc"]), r["aicc"]))


def ets_grid(y):
    rows = []
    for name, op in [("No trend", dict(trend=None)), ("Additive trend", dict(trend="add", damped_trend=False)),
                     ("Damped additive trend", dict(trend="add", damped_trend=True))]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                aicc = float(ETSModel(np.log(y), error="add", **op).fit(disp=False, maxiter=500).aicc)
            except Exception:
                aicc = float("nan")
        rows.append({"specification": name, "aicc": round(aicc, 3)})
    return sorted(rows, key=lambda r: r["aicc"])


def ridge_grid(y, exo):
    x = construir_features(y, exo.loc[: y.index[-1]])
    log_y = np.log(y).to_numpy()
    out = {}
    for step in (1, 6):
        rows = np.arange(len(y) - step)
        target = log_y[rows + step] - log_y[rows]
        ok = x.iloc[rows].notna().all(axis=1).to_numpy()
        X, z = x.iloc[rows][ok].to_numpy(), target[ok]
        errors = {}
        for alpha in ALFAS:
            acc = []
            for tr, va in TimeSeriesSplit(n_splits=3, test_size=6).split(X):
                m = make_pipeline(StandardScaler(), Ridge(alpha=alpha)).fit(X[tr], z[tr])
                acc.append(np.mean(np.abs(m.predict(X[va]) - z[va])))
            errors[alpha] = float(np.mean(acc))
        out[f"h{step}"] = [{"alpha": a, "cv_mae_log_change": round(e, 5)} for a, e in errors.items()]
        out[f"h{step}_selected"] = min(errors, key=errors.get)
        out["training_rows_h1" if step == 1 else "training_rows_h6"] = int(len(z))
    out["features"] = list(x.columns)
    return out


def in_sample_mase(y):
    """One-step in-sample error of each candidate fitted on the training data available at the last validation origin."""
    scale = y.diff().abs().mean()
    log_y = np.log(y)
    res = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        best = min((SARIMAX(log_y, order=o, seasonal_order=s, trend="n").fit(disp=False, maxiter=200)
                    for o in GRILLA_ARIMA for s in GRILLA_ESTACIONAL), key=lambda f: f.aicc)
        fitted = np.exp(best.fittedvalues.iloc[1:])
        res["sarima"] = float((np.abs(y.iloc[1:] - fitted) / scale).mean())
        ets_fit = min((ETSModel(log_y, error="add", **op).fit(disp=False, maxiter=500)
                       for op in [dict(trend=None), dict(trend="add"), dict(trend="add", damped_trend=True)]),
                      key=lambda f: f.aicc)
        fitted = np.exp(ets_fit.fittedvalues.iloc[1:])
        res["ets"] = float((np.abs(y.iloc[1:] - fitted) / scale).mean())
    res["naive"] = 1.0
    return {k: round(v, 3) for k, v in res.items()}


def residual_diagnostics(y, order, seasonal):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = SARIMAX(np.log(y), order=order, seasonal_order=seasonal, trend="n").fit(disp=False, maxiter=200)
    resid = fit.resid.iloc[1:]
    lb = acorr_ljungbox(resid, lags=[6, 12], return_df=True)
    return fit, resid, {"ljung_box_p_lag6": round(float(lb.loc[6, "lb_pvalue"]), 4),
                        "ljung_box_p_lag12": round(float(lb.loc[12, "lb_pvalue"]), 4),
                        "residual_mean": round(float(resid.mean()), 5), "residual_std": round(float(resid.std()), 5)}


def bias_table(bt):
    g = bt.assign(err_pct=(bt["pred"] / bt["real"] - 1) * 100)
    out = g.groupby(["modelo", "bloque"])["err_pct"].agg(["mean", "median"]).round(2).reset_index()
    return [{"model": r.modelo, "block": "validation" if r.bloque == "validacion" else "test",
             "mean_error_pct": r["mean"], "median_error_pct": r["median"]} for _, r in out.iterrows()]


def figures(ind, raw_total, y, bt, metricas, part, resid):
    FIG.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    raw_total.plot(ax=axes[0], color=GREY, lw=1.6)
    axes[0].set_title("Before: raw annex, column T_Otros (0 = no data)")
    axes[0].set_xlabel("")
    ind["T_Otros"].plot(ax=axes[1], color=GREEN, lw=1.8)
    axes[1].set_title("After: zeros treated as missing (NaN)")
    axes[1].set_xlabel("")
    for a in axes:
        _style(a)
    _save(fig, "before_after_zeros")

    fig, axes = plt.subplots(1, 3, figsize=(11, 3.2))
    for a, (title, s, col) in zip(axes, [("Index level", y, GREY), ("Natural logarithm", np.log(y), BLUE),
                                         ("Monthly log change (d = 1)", np.log(y).diff(), GREEN)]):
        a.plot(s.index, s, color=col, lw=1.6)
        a.set_title(title)
        _style(a)
    _save(fig, "transformations")

    fig, ax = plt.subplots(figsize=(10, 3.4))
    ax.plot(y.index, y, color=GREY, lw=1.5)
    val_o, test_o = ev.origenes(y)
    spans = [(y.index[0], y.index[val_o[0]], GREY, "Initial training window"),
             (y.index[val_o[0]], y.index[val_o[-1]], BLUE, f"Validation origins ({len(val_o)})"),
             (y.index[test_o[0]], y.index[test_o[-1]], RED, f"Test origins ({len(test_o)})"),
             (y.index[test_o[-1]], y.index[-1], GOLD, "Last test targets")]
    for a0, a1, col, lab in spans:
        ax.axvspan(a0, a1, color=col, alpha=.18, label=lab)
    ax.set_ylabel("Index (Jan 2021 = 100)")
    ax.legend(frameon=False, fontsize=8, ncol=4, loc="upper center", bbox_to_anchor=(.5, -.08))
    _style(ax)
    _save(fig, "partitions")

    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8), sharey=True)
    for ax, block, title in zip(axes, ["validacion", "prueba"], ["Validation", "Test"]):
        sub = metricas[(metricas.bloque == block) & (metricas.horizonte > 0)]
        for m, g in sub.groupby("modelo"):
            ax.plot(g.horizonte, g.mase, marker="o", color=COLORS[m], label=SHORT[m])
        ax.set_title(f"MASE by horizon: {title}")
        ax.set_xlabel("Months ahead")
        _style(ax)
    axes[0].legend(frameon=False, fontsize=8)
    _save(fig, "mase_by_horizon")

    fig, ax = plt.subplots(figsize=(10, 3.6))
    order = ["naive", "snaive", "ets", "sarima", "ridge"]
    data, labels, pos = [], [], []
    for i, m in enumerate(order):
        for j, block in enumerate(["validacion", "prueba"]):
            g = bt[(bt.modelo == m) & (bt.bloque == block)]
            data.append(((g["pred"] / g["real"] - 1) * 100).to_numpy())
            pos.append(i * 3 + j)
    bp = ax.boxplot(data, positions=pos, widths=.8, patch_artist=True, showfliers=False)
    for k, patch in enumerate(bp["boxes"]):
        patch.set_facecolor(BLUE if k % 2 == 0 else RED)
        patch.set_alpha(.45)
    ax.set_xticks([i * 3 + .5 for i in range(len(order))])
    ax.set_xticklabels([SHORT[m] for m in order])
    ax.axhline(0, color="black", lw=.8)
    ax.set_ylabel("Forecast error (%)")
    ax.set_title("Error distribution: validation (blue) and test (red), all horizons")
    _style(ax)
    _save(fig, "error_distribution")

    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), sharey=True)
    for ax, h in zip(axes, (1, 6)):
        real = bt[(bt.modelo == "sarima") & (bt.bloque == "prueba") & (bt.horizonte == h)].set_index("fecha")
        ax.plot(pd.to_datetime(real.index), real["real"], color="black", lw=1.8, label="Actual")
        for m in ("sarima", "naive", "ridge"):
            g = bt[(bt.modelo == m) & (bt.bloque == "prueba") & (bt.horizonte == h)]
            ax.plot(pd.to_datetime(g["fecha"]), g["pred"], color=COLORS[m], lw=1.3, marker="o", ms=3, label=SHORT[m])
        ax.set_title(f"Independent test: {h} month{'s' if h > 1 else ''} ahead")
        ax.tick_params(axis="x", labelrotation=30)
        _style(ax)
    axes[0].legend(frameon=False, fontsize=8)
    _save(fig, "test_actual_vs_predicted")

    fig, axes = plt.subplots(1, 2, figsize=(10, 3.2))
    axes[0].plot(resid.index, resid, color=RED, lw=1.2)
    axes[0].axhline(0, color="black", lw=.8)
    axes[0].set_title("SARIMA residuals (log scale)")
    from statsmodels.graphics.tsaplots import plot_acf
    plot_acf(resid, lags=18, ax=axes[1], color=RED, vlines_kwargs={"colors": RED})
    axes[1].set_title("Residual autocorrelation")
    for a in axes:
        _style(a)
    _save(fig, "residuals")


def experiments(metricas, grids, created):
    rows = []
    for _, r in metricas[metricas.horizonte == 0].iterrows():
        rows.append({"experiment": f"backtest-{r.modelo}-{'validation' if r.bloque == 'validacion' else 'test'}", "date": created, "model": SHORT[r.modelo],
                     "configuration": {"naive": "last value repeated", "snaive": "value 12 months earlier",
                                       "ets": "log; additive error; trend by AICc (none/additive/damped)",
                                       "sarima": "log; d=1; p,q in 0..2; seasonal (1,0,0,12) optional; AICc per origin",
                                       "ridge": "direct model per horizon; 16 features; alpha by TimeSeriesSplit"}[r.modelo],
                     "block": "validation" if r.bloque == "validacion" else "test", "forecasts": int(r.n),
                     "mae": round(r.mae, 3), "rmse": round(r.rmse, 3), "mase": round(r.mase, 3)})
    for g in grids["sarima"][:5]:
        rows.append({"experiment": "tuning-sarima-final-origin", "date": created, "model": "SARIMA",
                     "configuration": f"order {g['order']} seasonal {g['seasonal_order']}", "block": "training (AICc)",
                     "forecasts": "", "mae": "", "rmse": "", "mase": "", "aicc": g["aicc"]})
    for g in grids["ets"]:
        rows.append({"experiment": "tuning-ets-final-origin", "date": created, "model": "ETS",
                     "configuration": g["specification"], "block": "training (AICc)", "forecasts": "",
                     "mae": "", "rmse": "", "mase": "", "aicc": g["aicc"]})
    for h in ("h1", "h6"):
        for g in grids["ridge"][h]:
            rows.append({"experiment": f"tuning-ridge-{h}", "date": created, "model": "Ridge",
                         "configuration": f"alpha {g['alpha']}", "block": "inner time-series CV (3 folds)",
                         "forecasts": "", "mae": g["cv_mae_log_change"], "rmse": "", "mase": ""})
    return pd.DataFrame(rows)


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    FIG.mkdir(parents=True, exist_ok=True)
    ind = pd.read_csv(c.PROCESSED / "indice_mensual.csv", parse_dates=["fecha"], index_col="fecha").asfreq("MS")
    exo = pd.read_csv(c.PROCESSED / "exogenas_mensual.csv", parse_dates=["fecha"], index_col="fecha")
    y = ind[c.SERIES["total"]]
    bt = pd.read_csv(c.REPORTES / "backtest.csv", parse_dates=["origen", "fecha"])
    metricas = pd.read_csv(c.REPORTES / "metricas.csv")
    results = json.loads((c.REPORTES / "resultados.json").read_text(encoding="utf-8"))
    created = datetime.now().astimezone().strftime("%Y-%m-%d")   # local date of the run

    val_o, test_o = ev.origenes(y)
    y_val_end = y.iloc[: val_o[-1] + 1]          # data available at the last validation origin
    grids = {"sarima": sarima_grid(y), "sarima_last_validation_origin": sarima_grid(y_val_end)[:5],
             "ets": ets_grid(y), "ridge": ridge_grid(y, exo)}
    best = grids["sarima"][0]
    order, seasonal = eval(best["order"]), eval(best["seasonal_order"])
    _, resid, diag = residual_diagnostics(y, order, seasonal)

    oos_h1 = metricas[(metricas.horizonte == 1)].set_index(["modelo", "bloque"])["mase"]
    ins = in_sample_mase(y_val_end)
    fit_gap = [{"model": SHORT[m], "in_sample_one_step_mase": ins[m],
                "validation_h1_mase": round(float(oos_h1[(m, "validacion")]), 3),
                "test_h1_mase": round(float(oos_h1[(m, "prueba")]), 3)} for m in ("naive", "ets", "sarima")]

    raw = raw_annex()
    raw["fecha"] = pd.to_datetime(raw["fecha"], errors="coerce")
    raw_otros = raw.dropna(subset=["fecha"]).set_index("fecha")["T_Otros"].apply(pd.to_numeric, errors="coerce")
    figures(ind, raw_otros, y, bt, metricas, None, resid)

    selected = results["modelo_seleccionado"]
    test_h = metricas[(metricas.modelo == selected) & (metricas.bloque == "prueba") & (metricas.horizonte > 0)]
    evidence = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_cutoff": results["fecha_corte"][:7],
        "sources": SOURCES,
        "data": data_evidence(ind),
        "partitions": partitions(y),
        "tuning": grids,
        "selected_specification": {"order": best["order"], "seasonal_order": best["seasonal_order"], "aicc": best["aicc"]},
        "residual_diagnostics": diag,
        "fit_gap": fit_gap,
        "bias": bias_table(bt),
        "selected_test_by_horizon": [{"horizon": int(r.horizonte), "mae": round(r.mae, 3), "rmse": round(r.rmse, 3),
                                      "mase": round(r.mase, 3)} for _, r in test_h.iterrows()],
        "model_names": {k: v for k, v in NOMBRES.items()},
    }
    (OUT / "evidence.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    experiments(metricas, grids, created).to_csv(OUT / "experiments.csv", index=False)
    return evidence


if __name__ == "__main__":
    e = run()
    print(json.dumps({k: e[k] for k in ("data_cutoff", "partitions", "selected_specification", "residual_diagnostics",
                                        "fit_gap")}, indent=1))

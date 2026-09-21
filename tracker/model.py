"""
Model, training rules, walk-forward backtest, calibration and metrics.

Training rules and metrics are ported from paper_pipeline/common.py. The one addition is
`fit_predict`, which scores the forecast-origin week itself (the paper only scores weeks whose
outcome is already known).
"""
import numpy as np, pandas as pd
import xgboost as xgb
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score

from .config import XGB_PARAMS, MIN_POS, MIN_NEG


def clf(pw, seed=42, n_jobs=None):
    p = dict(XGB_PARAMS); p["random_state"] = seed
    return xgb.XGBClassifier(scale_pos_weight=pw, n_jobs=n_jobs, **p)


def add_4wk_labels(df):
    """y4, y4_known, n_obs4, konset (first future week 1..4 with a case; NaN if none observed)."""
    df = df.sort_values(["unit", "widx"]).reset_index(drop=True)
    g = df.groupby("unit")["value"]
    fut = [g.shift(-k) for k in (1, 2, 3, 4)]
    F = pd.concat(fut, axis=1)
    nobs = F.notna().sum(axis=1); s4 = F.sum(axis=1, min_count=1)
    df["y4"] = np.where(s4 > 0, 1.0, 0.0)
    df["y4_known"] = ((s4 > 0) | (nobs == 4)).astype(int)
    df["n_obs4"] = nobs
    kon = np.full(len(df), np.nan)
    for k in (4, 3, 2, 1):
        kon[fut[k - 1].values > 0] = k
    df["konset"] = kon
    return df


class Panel:
    """Arrays shared by every fit on one panel."""
    def __init__(self, df, feats):
        self.df = df
        self.t = df.widx.values
        self.q = df["value"].values == 0                   # quiet cohort: no case in the current week
        self.isc = df.is_region.values == 0
        self.X = df[feats].fillna(0).values.astype(np.float32)
        self.y1 = (df.y_count_next.values >= 1).astype(int)
        self.known1 = ~np.isnan(df.y_count_next.values)
        self.y4 = df.y4.values.astype(int)
        self.kon = df.konset.values
        self.nobs = df.n_obs4.values


def training_rows(P: Panel, W, horizon, protocol):
    """Rows (and labels, weights) usable to train a forecast made at week W (paper_pipeline/common.py)."""
    t, q = P.t, P.q
    if horizon == 1:
        tr = q & (t < W) & P.known1
        return tr, P.y1[tr], None
    base = q & (t < W)
    pos_obs = base & ~np.isnan(P.kon) & ((t + P.kon) <= W)
    neg_det = base & ((t + 4) <= W) & np.isnan(P.kon)
    if protocol == "cens":
        tr = pos_obs | neg_det
        return tr, pos_obs.astype(int)[tr], None
    if protocol == "ipcw":
        open_unk = base & ((t + 4) > W) & ~pos_obs
        tr = pos_obs | neg_det | open_unk
        m_obs = np.clip(W - t, 0, 4).astype(float)
        return tr, pos_obs.astype(int)[tr], np.where(open_unk, m_obs / 4.0, 1.0)[tr]
    raise ValueError(protocol)


def _fit(P, tr, ytr, w_tr, n_jobs=None):
    pw = (ytr == 0).sum() / max(ytr.sum(), 1)
    m = clf(pw, n_jobs=n_jobs)
    m.fit(P.X[tr], ytr, sample_weight=w_tr)
    return m


def fit_predict(P: Panel, W, horizon, protocol="cens", n_jobs=None):
    """Train on everything known at week W and score the quiet counties of week W.
    Returns (rows, raw probability, fitted model, n_train, n_pos)."""
    tr, ytr, w_tr = training_rows(P, W, horizon, protocol)
    if ytr.sum() < MIN_POS or (ytr == 0).sum() < MIN_NEG:
        raise RuntimeError(f"too few training events at week {W}: {int(ytr.sum())} positives")
    te = (P.t == W) & P.q & P.isc
    m = _fit(P, tr, ytr, w_tr, n_jobs)
    return te, m.predict_proba(P.X[te])[:, 1], m, int(tr.sum()), int(ytr.sum())


def walkforward(P: Panel, weeks, horizon, protocol="cens", n_jobs=None, log=None):
    """Expanding-window walk-forward over `weeks` (paper protocol). Scores only weeks whose outcome is known."""
    cols = ["unit", "fips5", "state", "widx", "week_label", "ever", "log_pop", "weeks_since", "value"]
    rows = []
    for W in weeks:
        tr, ytr, w_tr = training_rows(P, W, horizon, protocol)
        if ytr.sum() < MIN_POS or (ytr == 0).sum() < MIN_NEG:
            continue
        te = (P.t == W) & P.q & P.isc
        te = te & (P.known1 if horizon == 1 else (P.nobs == 4))
        if te.sum() == 0:
            continue
        m = _fit(P, tr, ytr, w_tr, n_jobs)
        r = P.df.loc[te, cols].copy()
        r["y"] = (P.y1 if horizon == 1 else P.y4)[te]
        r["p"] = m.predict_proba(P.X[te])[:, 1]
        rows.append(r)
        if log: log(f"    backtest h={horizon} week {P.df.week_label.values[te][0]}: onsets {int(r.y.sum())}")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=cols + ["y", "p"])


# ---------------------------------------------------------------- calibration (Platt scaling)
def logit(x, e=1e-6):
    x = np.clip(np.asarray(x, dtype=float), e, 1 - e); return np.log(x / (1 - x))


def fit_platt(p, y):
    lr = LogisticRegression(C=1e6, max_iter=1000).fit(logit(p).reshape(-1, 1), np.asarray(y))
    return {"a": float(lr.coef_[0][0]), "b": float(lr.intercept_[0])}


def apply_platt(p, params):
    z = params["a"] * logit(p) + params["b"]
    return 1.0 / (1.0 + np.exp(-z))


def ece_quantile(p, y, nbins=10):
    p = np.asarray(p); y = np.asarray(y); order = np.argsort(p); ps = p[order]; ys = y[order]
    edges = np.linspace(0, len(p), nbins + 1).astype(int); e = 0.0
    for i in range(nbins):
        lo, hi = edges[i], edges[i + 1]
        if hi > lo: e += (hi - lo) / len(p) * abs(ps[lo:hi].mean() - ys[lo:hi].mean())
    return float(e)


# ---------------------------------------------------------------- heuristics and metrics (paper_pipeline/common.py)
def heuristic_scores(pred):
    d = pred.copy()
    rec = (-d.weeks_since).groupby(d.widx).rank(pct=True)
    pop = d.log_pop.groupby(d.widx).rank(pct=True)
    d["h_recency"] = rec + 1e-6 * pop
    d["h_pop"] = pop
    d["h_recency_pop"] = (rec + pop) / 2
    return d


def recall_at_k(d, k, col="p"):
    vals = []
    for _, g in d.groupby("widx"):
        if g.y.sum() > 0:
            vals.append(g.y.values[np.argsort(-g[col].values)[:k]].sum() / g.y.sum())
    return float(np.mean(vals)) if vals else float("nan")


def caught_at_k(d, k, col="p"):
    vals = [g.y.values[np.argsort(-g[col].values)[:k]].sum() for _, g in d.groupby("widx")]
    return float(np.mean(vals)) if vals else float("nan")


def summarize(d, col="p", ks=(50, 100)):
    if len(d) == 0 or d.y.sum() == 0 or d.y.nunique() < 2:
        return {}
    nweeks = d.widx.nunique(); onsets = int(d.y.sum())
    res = {"weeks": int(nweeks), "first_week": str(d.week_label.min()), "last_week": str(d.week_label.max()),
           "onsets": onsets, "onsets_per_week": round(onsets / nweeks, 2),
           "AUROC": round(float(roc_auc_score(d.y, d[col])), 3),
           "AUPRC": round(float(average_precision_score(d.y, d[col])), 3)}
    for k in ks:
        c = caught_at_k(d, k, col)
        res[f"recall@{k}"] = round(recall_at_k(d, k, col), 3)
        res[f"caught@{k}"] = round(c, 2)
        res[f"precision@{k}"] = round(c / k, 3)
    return res


def week_table(d, k=50, col="p"):
    """Per-week onsets and onsets caught in the top k."""
    rows = []
    for w, g in d.groupby("widx"):
        top = g.y.values[np.argsort(-g[col].values)[:k]].sum()
        rows.append({"week": g.week_label.iloc[0], "scored": len(g), "onsets": int(g.y.sum()), f"caught@{k}": int(top)})
    return pd.DataFrame(rows)

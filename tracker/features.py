"""
Weekly county panel and leak-free features.

A line-for-line port of paper_pipeline/01_build_features.py (steps 2-6b), taking the incident-case
table from the public repo instead of the internal cumulative line list, and keeping the last
(forecast-origin) week, which the paper drops because its label is not yet known.
"""
import os
import numpy as np, pandas as pd
from scipy import sparse

from .config import STATIC, COV, CMT, DIF


def load_static():
    rt = dict(float_precision="round_trip")
    svi = pd.read_csv(os.path.join(STATIC, "svi_2022_county.csv"), dtype={"fips5": str}, **rt)
    cov = pd.read_csv(os.path.join(STATIC, "county_covariates.csv"), dtype={"fips5": str}, **rt)
    edges = pd.read_csv(os.path.join(STATIC, "commuting_edges.csv.gz"), dtype={"src": str, "dst": str}, **rt)
    rpop = pd.read_csv(os.path.join(STATIC, "region_population.csv"), **rt)
    return dict(svi=svi, cov=cov, edges=edges, region_pop=dict(zip(rpop.unit, rpop["pop"])))


def build_panel(inc: pd.DataFrame, origin_week: pd.Timestamp, mmr: pd.DataFrame, st: dict, log=print):
    """inc: [unit, state, is_region, date, value]; origin_week: Monday of the last observed week.
    Returns (panel, info). panel has one row per unit x week from the first reported week to origin_week."""
    svi, cov_static, edges, region_pop = st["svi"], st["cov"], st["edges"], st["region_pop"]

    # ---------------- 1. incident cases -> weeks (Monday start) ----------------
    ll = inc.copy()
    ll["week_start"] = (ll["date"] - pd.to_timedelta(ll["date"].dt.weekday, unit="D")).dt.normalize()
    ll = ll[ll.week_start <= origin_week]
    wk = ll.groupby(["unit", "week_start"])["value"].sum().reset_index().rename(columns={"value": "inc"})
    meta_ll = ll.groupby("unit").agg(state=("state", "first"), is_region=("is_region", "first")).reset_index()

    # ---------------- 2. universe ----------------
    county_units = pd.DataFrame({"unit": svi.fips5, "fips5": svi.fips5, "state": svi.STATE, "is_region": False,
                                 "pop": svi.E_TOTPOP})
    region_units = meta_ll[meta_ll.is_region][["unit", "state"]].copy()
    region_units["pop"] = region_units.unit.map(region_pop)
    region_units["fips5"] = "REGION"; region_units["is_region"] = True
    units = pd.concat([county_units, region_units], ignore_index=True).drop_duplicates("unit")
    dropped = wk[~wk.unit.isin(units.unit)]
    info = {"units": len(units), "counties": int((~units.is_region).sum()), "regions": int(units.is_region.sum()),
            "unmapped_units": sorted(dropped.unit.unique().tolist()), "unmapped_cases": float(dropped.inc.sum())}

    # ---------------- 3. balanced weekly grid ----------------
    weeks = pd.date_range(wk.week_start.min(), origin_week, freq="W-MON")
    grid = pd.MultiIndex.from_product([units.unit, weeks], names=["unit", "week_start"]).to_frame(index=False)
    panel = grid.merge(wk, on=["unit", "week_start"], how="left"); panel["inc"] = panel.inc.fillna(0.0)
    panel = panel.merge(units, on="unit", how="left")
    panel["week"] = panel.week_start.dt.isocalendar().week.astype(int)
    wmap = {w: i for i, w in enumerate(sorted(panel.week_start.unique()))}
    panel["widx"] = panel.week_start.map(wmap); panel["week_label"] = panel.week_start.dt.strftime("%Y-%m-%d")
    panel = panel.sort_values(["unit", "widx"]).reset_index(drop=True).rename(columns={"inc": "value"})

    # ---------------- 4. static covariates (MMR read live) ----------------
    cov = cov_static.merge(mmr[["fips5", "mmr"]], on="fips5", how="outer")
    cov["susc"] = 1 - cov["mmr"]
    f2s = svi.set_index("fips5")["STATE"].to_dict(); cov["state_name"] = cov.fips5.map(f2s)
    state_mean = cov.groupby("state_name")[COV].mean(); nat_mean = cov[COV].mean()
    panel = panel.merge(cov[["fips5"] + COV], on="fips5", how="left")
    panel["covariate_source"] = np.where(panel[COV].isna().any(axis=1), "fallback", "direct_county")
    panel["mmr_direct"] = panel["susc"].notna()
    for c in COV:
        panel[c] = panel[c].fillna(panel["state"].map(state_mean[c])).fillna(nat_mean[c])

    # ---------------- 5. leak-free case features ----------------
    g = panel.copy(); gb = g.groupby("unit")["value"]
    for L in (1, 2, 3, 4): g[f"lag{L}"] = gb.shift(L)
    g["roll4"] = gb.shift(1).rolling(4, min_periods=1).sum().reset_index(0, drop=True)
    g["roll8"] = gb.shift(1).rolling(8, min_periods=1).sum().reset_index(0, drop=True)
    g["cum"] = gb.cumsum() - g["value"]; g["ever"] = (g["cum"] > 0).astype(int); g["active"] = (g["value"] > 0).astype(int)

    def wsl(s):
        out = np.full(len(s), 99); last = -99
        for i, v in enumerate(s.values):
            out[i] = min(i - last, 99) if last >= 0 else 99
            if v > 0: last = i
        return out
    g["weeks_since"] = g.groupby("unit")["active"].transform(lambda s: pd.Series(wsl(s), index=s.index))
    stt = g.groupby(["state", "widx"])["value"].transform("sum"); g["state_other_now"] = stt - g["value"]
    g["state_other_lag1"] = g.groupby("unit")["state_other_now"].shift(1)
    nat = g.groupby("widx")["value"].transform("sum"); g["nat_now"] = nat - g["value"]
    g["nat_lag1"] = g.groupby("unit")["nat_now"].shift(1); g["nat_lag2"] = g.groupby("unit")["nat_now"].shift(2)
    g["woy_sin"] = np.sin(2 * np.pi * g.week / 52); g["woy_cos"] = np.cos(2 * np.pi * g.week / 52)
    g["log_pop"] = np.log1p(g["pop"].fillna(g["pop"].median()))
    g["is_region"] = g["is_region"].astype(int)
    g["y_count_next"] = g.groupby("unit")["value"].shift(-1)

    # ---------------- 6. commuting importation (strictly lagged) ----------------
    cty = g[g.is_region == 0]
    fips = sorted(cty.fips5.unique()); idx = {f: i for i, f in enumerate(fips)}; n = len(fips)
    e = edges[edges.src.isin(idx) & edges.dst.isin(idx)]
    Wm = sparse.coo_matrix((e.workers.values.astype(float), (e.src.map(idx).values, e.dst.map(idx).values)), shape=(n, n)).tocsr()
    Wm = Wm + Wm.T; Wm.setdiag(0); Wm.eliminate_zeros()
    ties = np.asarray(Wm.sum(axis=1)).ravel()
    C = cty.pivot_table(index="widx", columns="fips5", values="value", aggfunc="sum", fill_value=0).reindex(columns=fips, fill_value=0).sort_index()
    Cv = C.values.astype(float); A = (Cv > 0).astype(float)

    def lag(M, k):
        out = np.zeros_like(M); out[k:] = M[:-k]; return out
    prev1 = lag(Cv, 1); prev_roll4 = sum(lag(Cv, k) for k in (1, 2, 3, 4)); act1 = lag(A, 1)
    imp1 = prev1 @ Wm; imp4 = prev_roll4 @ Wm; impa = act1 @ Wm; den = np.maximum(ties, 1.0)
    cm = pd.DataFrame({"widx": np.repeat(C.index.values, n), "fips5": np.tile(fips, len(C)),
                       "cmt_imp_lag1": np.log1p(imp1.ravel()), "cmt_imp_roll4": np.log1p(imp4.ravel()),
                       "cmt_imp_active": np.log1p(impa.ravel()), "cmt_nimp_lag1": np.log1p((imp1 / den).ravel() * 1e3),
                       "cmt_nimp_roll4": np.log1p((imp4 / den).ravel() * 1e3), "cmt_ties": np.log1p(np.tile(ties, len(C)))})
    g = g.merge(cm, on=["fips5", "widx"], how="left")
    for c in CMT: g[c] = g[c].fillna(0.0)
    info["commuting_edges"] = int(len(e))

    # ---------------- 6b. commuting-network diffusion (strictly lagged) ----------------
    P = sparse.diags(1.0 / den) @ Wm
    hop1 = prev1 @ P; hop2 = hop1 @ P; hop3 = hop2 @ P
    acc = {}
    for lam in (0.5, 0.7, 0.9, 0.95):
        E = np.zeros_like(hop1)
        for w in range(1, len(E)): E[w] = lam * E[w - 1] + hop1[w]
        acc[lam] = E
    z = (acc[0.9] - acc[0.9].mean(axis=1, keepdims=True)) / (acc[0.9].std(axis=1, keepdims=True) + 1e-9)
    dif = pd.DataFrame({"widx": np.repeat(C.index.values, n), "fips5": np.tile(fips, len(C)),
                        "dif_hop1": np.log1p(hop1.ravel()), "dif_hop2": np.log1p(hop2.ravel()), "dif_hop3": np.log1p(hop3.ravel()),
                        "dif_acc50": np.log1p(acc[0.5].ravel()), "dif_acc70": np.log1p(acc[0.7].ravel()),
                        "dif_acc90": np.log1p(acc[0.9].ravel()), "dif_acc95": np.log1p(acc[0.95].ravel()), "dif_acc90_z": z.ravel()})
    g = g.merge(dif, on=["fips5", "widx"], how="left")
    for c in DIF: g[c] = g[c].fillna(0.0)

    g = g.sort_values(["unit", "widx"]).reset_index(drop=True)
    info.update({"weeks": int(g.widx.nunique()), "first_week": g.week_label.min(), "origin_week": g.week_label.max(),
                 "cases_in_panel": float(panel.value.sum())})
    return g, info

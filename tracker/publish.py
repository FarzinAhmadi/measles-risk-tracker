"""
Turn one forecast into the open-data files and the site's data file, keep the archive, and score
past forecasts against what was reported afterwards (the prospective scorecard).
"""
import glob, json, os
from datetime import timedelta
import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score

from .config import ARCHIVE, EVAL, LATEST, TIERS, WATCHLIST_K


def tier_of(rank):
    for cut, name in TIERS:
        if rank <= cut:
            return name
    return TIERS[-1][1]


def county_table(df, W, te, p1, p4, cal1, cal4, mmr, svi):
    """One row per county (3,144): status, forecasts, ranks, tiers and recent history."""
    last = df[(df.widx == W) & (df.is_region == 0)].set_index("fips5")
    win = df[(df.widx > W - 4) & (df.widx <= W) & (df.is_region == 0)].groupby("fips5")["value"].sum()
    out = pd.DataFrame(index=last.index)
    out["county"] = svi.set_index("fips5").loc[out.index, "COUNTY"]
    out["state"] = last["state"]
    out["state_abbr"] = svi.set_index("fips5").loc[out.index, "ST_ABBR"]
    out["status"] = np.where(last["value"] > 0, "active", "quiet")
    out["cases_this_week"] = last["value"].astype(int)
    out["cases_last_4_weeks"] = win.reindex(out.index).fillna(0).astype(int)
    out["cumulative_cases"] = (last["cum"] + last["value"]).astype(int)
    out["weeks_since_last_case"] = np.where(last["value"] > 0, 0, last["weeks_since"]).astype(int)
    out.loc[out.cumulative_cases == 0, "weeks_since_last_case"] = -1       # never reported a case
    m = mmr.set_index("fips5")
    out["mmr_coverage"] = m["mmr"].reindex(out.index)
    out["mmr_school_year"] = m["mmr_year"].reindex(out.index)

    sc = df.loc[te, ["fips5"]].copy()
    sc["score_1wk"] = p1; sc["score_4wk"] = p4
    sc["p_1wk"] = cal1(p1); sc["p_4wk"] = cal4(p4)
    sc = sc.set_index("fips5")
    for h in ("1wk", "4wk"):
        out[f"p_{h}"] = sc[f"p_{h}"].reindex(out.index)
        out[f"score_{h}"] = sc[f"score_{h}"].reindex(out.index)
        r = out[f"score_{h}"].rank(ascending=False, method="first")
        out[f"rank_{h}"] = r
        out[f"state_rank_{h}"] = out.groupby("state")[f"score_{h}"].rank(ascending=False, method="first")
        out[f"tier_{h}"] = [tier_of(x) if x == x else "" for x in r]
        mean = out[f"p_{h}"].mean()
        out[f"relative_risk_{h}"] = out[f"p_{h}"] / mean if mean == mean and mean > 0 else np.nan
    out = out.reset_index().rename(columns={"fips5": "fips"})
    return out.sort_values(["rank_1wk", "fips"], na_position="last").reset_index(drop=True)


OPEN_COLUMNS = [
    ("fips", "fips"), ("county", "county"), ("state", "state"), ("state_abbr", "state_abbr"), ("status", "status"),
    ("p_1wk", "prob_case_next_week"), ("rank_1wk", "national_rank_next_week"), ("state_rank_1wk", "state_rank_next_week"),
    ("tier_1wk", "tier_next_week"), ("relative_risk_1wk", "relative_risk_next_week"),
    ("p_4wk", "prob_case_next_4_weeks"), ("rank_4wk", "national_rank_next_4_weeks"), ("state_rank_4wk", "state_rank_next_4_weeks"),
    ("tier_4wk", "tier_next_4_weeks"), ("relative_risk_4wk", "relative_risk_next_4_weeks"),
    ("score_1wk", "model_score_next_week"), ("score_4wk", "model_score_next_4_weeks"),
    ("cases_this_week", "cases_this_week"), ("cases_last_4_weeks", "cases_last_4_weeks"), ("cumulative_cases", "cumulative_cases_since_2025"),
    ("weeks_since_last_case", "weeks_since_last_case"), ("mmr_coverage", "kindergarten_mmr_coverage"), ("mmr_school_year", "mmr_school_year"),
]


def open_csv(tab, meta):
    o = tab[[a for a, _ in OPEN_COLUMNS]].rename(columns=dict(OPEN_COLUMNS)).copy()
    o.insert(0, "target_week_start", meta["target_week_start"])
    o.insert(0, "forecast_origin_week", meta["origin_week"])
    for c in ("prob_case_next_week", "prob_case_next_4_weeks"):
        o[c] = o[c].round(6)
    for c in ("model_score_next_week", "model_score_next_4_weeks"):
        o[c] = o[c].round(6)
    for c in ("relative_risk_next_week", "relative_risk_next_4_weeks"):
        o[c] = o[c].round(2)
    for c in ("national_rank_next_week", "state_rank_next_week", "national_rank_next_4_weeks", "state_rank_next_4_weeks"):
        o[c] = o[c].astype("Int64")
    o["weeks_since_last_case"] = o["weeks_since_last_case"].replace(-1, pd.NA).astype("Int64")
    return o


# ---------------------------------------------------------------- archive and prospective scorecard
def archive_path(target_week):
    return os.path.join(ARCHIVE, "forecasts", f"{target_week}.csv")


def write_archive(o, meta):
    os.makedirs(os.path.join(ARCHIVE, "forecasts"), exist_ok=True)
    o.to_csv(archive_path(meta["target_week_start"]), index=False)
    idx_path = os.path.join(ARCHIVE, "index.csv")
    row = {k: meta[k] for k in ("target_week_start", "origin_week", "as_of", "run_utc", "cases_commit", "mmr_commit",
                                "counties_scored", "expected_onsets_1wk", "expected_onsets_4wk", "mode")}
    idx = pd.read_csv(idx_path, dtype=str) if os.path.exists(idx_path) else pd.DataFrame(columns=list(row))
    idx = idx[idx.target_week_start != meta["target_week_start"]]
    idx = pd.concat([idx, pd.DataFrame([row]).astype(str)], ignore_index=True).sort_values("target_week_start")
    idx.to_csv(idx_path, index=False)


def scorecard(df, k=WATCHLIST_K):
    """Score every archived forecast whose target window has been observed, against the current data."""
    weeks = sorted(df.week_label.unique())
    wpos = {w: i for i, w in enumerate(weeks)}
    cty = df[df.is_region == 0]
    val = cty.pivot_table(index="fips5", columns="week_label", values="value", aggfunc="sum")
    rows, detail = [], []
    for f in sorted(glob.glob(os.path.join(ARCHIVE, "forecasts", "*.csv"))):
        a = pd.read_csv(f, dtype={"fips": str})
        a = a[a.status == "quiet"]
        tw = str(a.target_week_start.iloc[0])
        if tw not in wpos:
            continue
        for h, n, col in (("1wk", 1, "model_score_next_week"), ("4wk", 4, "model_score_next_4_weeks")):
            i0 = wpos[tw]
            if i0 + n - 1 >= len(weeks):
                continue                                   # target window not fully observed yet
            tws = weeks[i0:i0 + n]
            y = (val.reindex(a.fips)[tws].fillna(0).sum(axis=1).values > 0).astype(int)
            s = a[col].values
            order = np.argsort(-s)
            caught = int(y[order[:k]].sum())
            onsets = int(y.sum())
            auc = float(roc_auc_score(y, s)) if 0 < onsets < len(y) else np.nan
            rows.append({"target_week_start": tw, "horizon": h, "window_end": (pd.Timestamp(tws[-1]) + timedelta(days=6)).strftime("%Y-%m-%d"),
                         "counties_scored": len(a), "onsets": onsets, f"caught_top{k}": caught,
                         f"recall_top{k}": round(caught / onsets, 3) if onsets else np.nan,
                         "expected_onsets": round(float(a[f"prob_case_next_{'week' if n == 1 else '4_weeks'}"].sum()), 2)
                         if a[f"prob_case_next_{'week' if n == 1 else '4_weeks'}"].notna().all() else np.nan,
                         "AUROC": round(auc, 3) if auc == auc else np.nan})
            rk = pd.Series(np.empty(len(s)), index=a.fips.values); rk.iloc[order] = np.arange(1, len(s) + 1)
            for fp in a.fips.values[y == 1]:
                r = a[a.fips == fp].iloc[0]
                detail.append({"target_week_start": tw, "horizon": h, "fips": fp, "county": r.county, "state_abbr": r.state_abbr,
                               "rank": int(rk[fp]), "in_top50": bool(rk[fp] <= k)})
    sc = pd.DataFrame(rows); de = pd.DataFrame(detail)
    os.makedirs(EVAL, exist_ok=True)
    sc.to_csv(os.path.join(EVAL, "scorecard.csv"), index=False)
    de.to_csv(os.path.join(EVAL, "scorecard_onsets.csv"), index=False)
    return sc, de


# ---------------------------------------------------------------- site summaries
def national_series(df):
    g = df.groupby("week_label")["value"].sum()
    c = df[df.is_region == 0].sort_values(["unit", "widx"]).copy()
    c["prev"] = c.groupby("unit")["value"].shift(1)
    on = c[(c.prev == 0) & (c.value > 0)].groupby("week_label").size()
    s = pd.DataFrame({"week": g.index, "cases": g.values.astype(int)})
    s["onsets"] = s.week.map(on).fillna(0).astype(int)
    return s


def state_summary(tab, df, W, k=WATCHLIST_K):
    rg = df[(df.is_region == 1) & (df.widx > W - 4) & (df.widx <= W)]
    reg4 = rg.groupby("state")["value"].sum(); reg1 = rg[rg.widx == W].groupby("state")["value"].sum()
    g = tab.groupby(["state", "state_abbr"])
    s = pd.DataFrame({
        "active_counties": g.apply(lambda d: int((d.status == "active").sum())),
        "cases_this_week": g.cases_this_week.sum(),
        "cases_last_4_weeks": g.cases_last_4_weeks.sum(),
        "expected_new_counties_1wk": g.p_1wk.sum().round(2),
        "expected_new_counties_4wk": g.p_4wk.sum().round(2),
        "counties_in_top50": g.apply(lambda d: int((d.rank_1wk <= k).sum())),
        "counties": g.size(),
    }).reset_index()
    s["cases_last_4_weeks"] = s.cases_last_4_weeks + s.state.map(reg4).fillna(0).astype(int)
    s["cases_this_week"] = s.cases_this_week + s.state.map(reg1).fillna(0).astype(int)
    return s.sort_values(["expected_new_counties_1wk"], ascending=False)


def write_json(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, separators=(",", ":"), default=_js)


def _js(x):
    if isinstance(x, (np.integer,)): return int(x)
    if isinstance(x, (np.floating,)): return None if np.isnan(x) else float(x)
    if isinstance(x, (np.bool_,)): return bool(x)
    if x is pd.NA: return None
    return str(x)


def records(d):
    d = d.replace({np.nan: None})
    return d.to_dict(orient="records")

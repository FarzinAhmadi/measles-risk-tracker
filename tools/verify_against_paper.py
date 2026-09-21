"""
tools/verify_against_paper.py — check that the live code is the manuscript's model.

Feeds the internal line list used for the paper (measles_timeseries 4.csv) through the tracker's feature
and model code, then compares with the paper pipeline's saved outputs:
  1. every one of the 37 features and the label, for all 264,604 county-weeks, against paper_pipeline/out/features.parquet
  2. walk-forward predictions for a few weeks at both horizons against pred_wf1_FULLDIF / pred_wf4_CENS_FULLDIF

usage:  python tools/verify_against_paper.py "/path/to/Measles Risk Modeling"
(needs paper_pipeline/out/features.parquet; run paper_pipeline/01_build_features.py first if it is missing,
 and pyarrow to read parquet files)
"""
import os, sys
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from tracker.features import load_static, build_panel          # noqa: E402
from tracker.sources import parse_mmr                           # noqa: E402
from tracker.model import add_4wk_labels, Panel, walkforward    # noqa: E402
from tracker.config import FEATURES                             # noqa: E402

src = sys.argv[1] if len(sys.argv) > 1 else "."
out = os.path.join(src, "paper_pipeline", "out")

# the paper's step 1: cumulative line list -> incident cases
ll = pd.read_csv(os.path.join(src, "measles_timeseries 4.csv"), dtype=str)
ll["date"] = pd.to_datetime(ll["date"], format="%m/%d/%y", errors="coerce")
ll["cases_total"] = pd.to_numeric(ll["cases_total"], errors="coerce")
ll = ll.dropna(subset=["date", "cases_total"]).copy()
ll["fips"] = ll["fips"].fillna("0").str.strip()
ll["is_county"] = ll.fips.str.len().isin([4, 5])
ll["unit"] = np.where(ll.is_county, ll.fips.str.zfill(5), ll["state"].str.strip() + "|" + ll["county"].fillna("Unknown").str.strip())
ll = ll.sort_values(["unit", "date"])
ll["inc"] = ll.groupby("unit")["cases_total"].diff()
ll.loc[ll.inc.isna(), "inc"] = ll.loc[ll.inc.isna(), "cases_total"]
ll["inc"] = ll["inc"].clip(lower=0)
inc = pd.DataFrame({"unit": ll.unit, "state": ll.state, "is_region": ~ll.is_county, "date": ll.date, "value": ll.inc})

# the paper's MMR table (JHU county MMR, archived release)
mraw = pd.read_csv(os.path.join(src, "data_sources", "mmr_nme", "mmr_data_us_counties.csv"), dtype=str)
mraw.columns = [c.strip().lstrip("﻿") for c in mraw.columns]
panel, info = build_panel(inc, pd.Timestamp("2026-08-17"), parse_mmr(mraw), load_static(), log=lambda *a: None)

ref = pd.read_parquet(os.path.join(out, "features.parquet"))
mine = panel.dropna(subset=["y_count_next"])
m = ref.merge(mine, on=["unit", "widx"], suffixes=("_paper", "_live"))
bad = []
for c in FEATURES + ["y_count_next", "value"]:
    a, b = m[c + "_paper"].astype(float).values, m[c + "_live"].astype(float).values
    if np.nanmax(np.abs(a - b)) > 0 or (np.isnan(a) != np.isnan(b)).any():
        bad.append(c)
print(f"1. features: paper {len(ref):,} rows, live {len(mine):,} rows, matched {len(m):,};",
      "all 37 features and the label identical" if not bad and len(m) == len(ref) == len(mine) else f"DIFFERENT: {bad}")

df = add_4wk_labels(panel); P = Panel(df, FEATURES)
for h, f, weeks in [(1, "pred_wf1_FULLDIF", [40, 82]), (4, "pred_wf4_CENS_FULLDIF", [40, 78])]:
    pr = walkforward(P, weeks, h, "cens")
    r = pd.read_parquet(os.path.join(out, f + ".parquet"))
    mm = pr.merge(r[["unit", "widx", "p", "y"]], on=["unit", "widx"], suffixes=("", "_paper"))
    d = float((mm.p - mm.p_paper).abs().max())
    print(f"2. {h}-week walk-forward, weeks {weeks}: {len(mm):,} county-weeks, max |prediction difference| = {d:.2e}, "
          f"labels {'identical' if (mm.y == mm.y_paper).all() else 'DIFFERENT'}")

"""
tools/build_static.py — one-time build of the static (non-weekly) inputs in static/.

Run once from the project folder that holds the paper pipeline inputs:
    python tools/build_static.py "/path/to/Measles Risk Modeling"

It reads exactly the files the paper pipeline (paper_pipeline/01_build_features.py) reads and writes
compact copies, so the live model uses the same static layers as the manuscript:
  static/svi_2022_county.csv       county universe, names, population   <- data_sources/SVI_2022_US_county.csv
  static/county_covariates.csv     SVI sub-indices, density, 2024 vote share, COVID uptake
                                                                          <- model_v2/covariates_county.parquet (minus MMR,
                                                                             which is read live from CSSEGISandData/MMR_data)
  static/commuting_edges.csv.gz    ACS 2016-2020 county-to-county commuting flows (cross-county, directed)
                                                                          <- data_sources/table1.xlsx
  static/region_population.csv     population of health-region / state-unassigned reporting units
                                                                          <- measles_timeseries 4.csv (line list)
"""
import os, sys
import numpy as np, pandas as pd

src = sys.argv[1] if len(sys.argv) > 1 else "."
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "static"); os.makedirs(OUT, exist_ok=True)

# county universe
svi = pd.read_csv(os.path.join(src, "data_sources", "SVI_2022_US_county.csv"))
svi = svi.copy(); svi["fips5"] = svi.FIPS.astype(str).str.zfill(5)
svi["E_TOTPOP"] = pd.to_numeric(svi.E_TOTPOP, errors="coerce")
svi[["fips5", "STATE", "ST_ABBR", "COUNTY", "E_TOTPOP"]].to_csv(os.path.join(OUT, "svi_2022_county.csv"), index=False)

# static covariates (everything except MMR)
cov = pd.read_parquet(os.path.join(src, "model_v2", "covariates_county.parquet"))
keep = ["svi_rpl", "svi_pov", "svi_uninsur", "svi_age17", "svi_minrty", "svi_nohs", "svi_crowd", "log_dens", "gop24", "covid_vax"]
cov = cov[cov[keep].notna().any(axis=1)][["fips5"] + keep]
cov.to_csv(os.path.join(OUT, "county_covariates.csv"), index=False)

# commuting edges (same filters as the paper pipeline)
d = pd.read_excel(os.path.join(src, "data_sources", "table1.xlsx"), sheet_name=0, header=7)
d.columns = ["res_st", "res_cty", "res_stname", "res_ctyname", "wrk_st", "wrk_cty", "wrk_stname", "wrk_ctyname", "workers", "moe"]
for c in ["res_st", "res_cty", "wrk_st", "wrk_cty", "workers"]:
    d[c] = pd.to_numeric(d[c], errors="coerce")
d = d.dropna(subset=["res_st", "res_cty", "wrk_st", "wrk_cty", "workers"]); d = d[d.wrk_st <= 56]
d["src"] = d.res_st.astype(int).map("{:02d}".format) + d.res_cty.astype(int).map("{:03d}".format)
d["dst"] = d.wrk_st.astype(int).map("{:02d}".format) + d.wrk_cty.astype(int).map("{:03d}".format)
d = d[d.src != d.dst]
d[["src", "dst", "workers"]].to_csv(os.path.join(OUT, "commuting_edges.csv.gz"), index=False, compression="gzip")

# region populations from the line list
ll = pd.read_csv(os.path.join(src, "measles_timeseries 4.csv"), dtype=str)
ll["fips"] = ll["fips"].fillna("0").str.strip()
reg = ll[~ll.fips.str.len().isin([4, 5])].copy()
reg["unit"] = reg["state"].str.strip() + "|" + reg["county"].fillna("Unknown").str.strip()
reg["pop"] = pd.to_numeric(reg["population"].astype(str).str.replace(",", ""), errors="coerce")
reg.groupby("unit")["pop"].max().reset_index().to_csv(os.path.join(OUT, "region_population.csv"), index=False)
print("static inputs written to", OUT)
for f in sorted(os.listdir(OUT)):
    print(f"  {f:28s} {os.path.getsize(os.path.join(OUT, f)):>10,} bytes")

"""
Configuration for the live measles risk tracker.

The model settings below are the ones in the early-warning manuscript (draft v0.2) and in
paper_pipeline/common.py. Change them only if the paper changes.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")
DATA = os.path.join(ROOT, "data")
LATEST = os.path.join(DATA, "latest")
ARCHIVE = os.path.join(DATA, "archive")
EVAL = os.path.join(DATA, "evaluation")
# Upstream clones and the Python environment live outside the repo (and outside OneDrive/Dropbox folders).
CACHE = os.environ.get("MRT_CACHE", os.path.join(os.path.expanduser("~"), ".cache", "measles-risk-tracker"))
VENV = os.environ.get("MRT_VENV", os.path.join(os.path.expanduser("~"), ".venvs", "measles-risk-tracker"))
UPSTREAM_DIR = os.path.join(CACHE, "upstream")

TIMEZONE = "America/New_York"
MODEL_VERSION = "ew-v0.2"          # early-warning model as described in manuscript draft v0.2

# ---------------------------------------------------------------- upstream data (public GitHub repos)
SOURCES = {
    "cases": {
        "repo": "https://github.com/CSSEGISandData/measles_data.git",
        "dir": "measles_data",
        "file": "measles_county_all_updates.csv",
        "label": "JHU Measles Tracking Team, county-level case updates",
    },
    "mmr": {
        "repo": "https://github.com/CSSEGISandData/MMR_data.git",
        "dir": "MMR_data",
        "file": "mmr_data_us_counties_v2.csv",
        "older_files": ["mmr_data_us_counties.csv"],      # name before the June 2026 "version 2" release
        "label": "JHU county-level kindergarten MMR coverage",
    },
    "tracker": {
        "repo": "https://github.com/CSSEGISandData/measles_tracker_test.git",
        "dir": "measles_tracker_test",
        "file": "GitHub Ready Version/summary_report.csv",
        "label": "JHU measles tracker dashboard summary (used as a cross-check)",
    },
}

# ---------------------------------------------------------------- feature groups (paper_pipeline/common.py)
CASE = ["roll4", "roll8", "cum", "ever", "weeks_since"]
SPILL = ["state_other_lag1", "nat_lag1", "nat_lag2"]
SEAS = ["woy_sin", "woy_cos"]
DEMO = ["log_pop", "log_dens"]
MMR = ["susc"]
SVI = ["svi_rpl", "svi_pov", "svi_uninsur", "svi_age17", "svi_minrty", "svi_nohs", "svi_crowd"]
POL = ["gop24", "covid_vax"]
CMT = ["cmt_imp_lag1", "cmt_imp_roll4", "cmt_imp_active", "cmt_nimp_lag1", "cmt_nimp_roll4", "cmt_ties"]
DIF = ["dif_hop1", "dif_hop2", "dif_hop3", "dif_acc50", "dif_acc70", "dif_acc90", "dif_acc95", "dif_acc90_z"]
COV = MMR + SVI + DEMO[1:] + POL
BASE = ["log_pop"] + CASE + SPILL + SEAS + ["is_region"] + COV
FULL = BASE + CMT
FEATURES = FULL + DIF               # the paper model: 37 features

# ---------------------------------------------------------------- learner (paper_pipeline/common.py: clf)
XGB_PARAMS = dict(n_estimators=200, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
                  min_child_weight=5, eval_metric="aucpr", tree_method="hist", random_state=42, verbosity=0)
MIN_POS, MIN_NEG = 5, 50

# 4-week training rule. "cens" is the censoring-aware rule in the manuscript's Methods; "ipcw" also keeps rows whose
# window is still open and has no case yet, as negatives with weight m/4 (Table 2's alternative row). The live
# tracker uses "ipcw": under "cens" the last three weeks enter training as positives only, and in September 2026 this
# inflated the live 4-week scores (116 expected newly reporting counties over four weeks, against about 62 implied by
# the next-week model). The two rules rank almost the same (41 of the top 50 in common; Spearman 0.95).
PROTOCOL_4WK = "ipcw"

# ---------------------------------------------------------------- operations
BACKTEST_WEEKS = 26                 # recent walk-forward weeks shown in the site's performance panel
CALIBRATION_WEEKS = 8               # most recent out-of-sample weeks used to fit the Platt calibration
CALIBRATION_MIN_ONSETS = 10         # widen the calibration window if it holds fewer onsets than this
WATCHLIST_K = 50                    # size of the national watchlist (the paper's operating point)
TIERS = [(50, "Top 50"), (150, "51–150"), (500, "151–500"), (10**9, "501+")]

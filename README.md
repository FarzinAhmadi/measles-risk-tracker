# US Measles Risk Tracker

A weekly, county-level forecast of where measles is likely to be reported next in the United States, with an interactive map and open data.

Every Friday, after the JHU Measles Tracking Team updates its public data, one script:

1. pulls the latest county case updates ([CSSEGISandData/measles_data](https://github.com/CSSEGISandData/measles_data)) and county MMR coverage ([CSSEGISandData/MMR_data](https://github.com/CSSEGISandData/MMR_data));
2. rebuilds the county-week panel and retrains the early-warning model from the working paper *Granular surveillance data enable county-level early warning of measles* (draft v0.2);
3. ranks every county with no cases this week by its chance of reporting one **next week** and over the **next four weeks**;
4. scores past forecasts against what was actually reported (the live record);
5. writes the open-data CSVs and the site data, then commits and pushes to GitHub, which republishes the site.

The site is `index.html` in this folder. GitHub Pages serves it at https://farzinahmadi.github.io/measles-risk-tracker/ (repository: https://github.com/FarzinAhmadi/measles-risk-tracker).

## Weekly use

```
python3 update.py
```

Run it any time after the Friday update (Friday evening or over the weekend). It takes 2–5 minutes, most of it for the 26-week backtest that feeds the calibration and the performance panel. It prints the top 10 counties, the expected number of newly reporting counties, and how last week's list did.

| Option | What it does |
|---|---|
| `--as-of 2026-09-11` | Re-create the forecast for an earlier Friday from the data as they were public on that day. |
| `--quick` | Skip the 26-week backtest (about 20 seconds) and reuse the last calibration. |
| `--no-push` / `--no-git` | Commit without pushing / leave git alone. |
| `--allow-stale` | Run even though no Friday update has been published yet (by default the script stops and tells you). |
| `--offline` | Use the cached copies of the upstream repos. |
| `--backfill 2026-05-01` | One-time: archive the real-time forecast for every Friday since that date (about a minute per week). The repo ships with this done from May 1, 2026. |

## First-time setup

1. On GitHub, create an **empty** repository named `measles-risk-tracker`. Do not add a README or license there.
2. Put the files in it, in either of two ways.
   - **With the setup script (recommended).** Move this folder somewhere outside OneDrive, for example `~/Projects/measles-risk-tracker`, because git repositories inside synced folders can get corrupted. Then run, in a terminal in that folder:
     ```
     bash setup.sh https://github.com/FarzinAhmadi/measles-risk-tracker.git
     ```
     This creates a Python environment in `~/.venvs/measles-risk-tracker` (numpy, pandas, scipy, scikit-learn, xgboost), makes the first commit and pushes it. On a Mac, if xgboost cannot load, run `brew install libomp` and then run `bash setup.sh` again.
   - **By uploading in the browser.** On the empty repo's page, choose "uploading an existing file" and drag in everything inside this folder. The web page may skip the hidden `.gitignore` and `.nojekyll`; the site works without them. Then clone the repo to your computer outside OneDrive (`git clone https://github.com/FarzinAhmadi/measles-risk-tracker.git`). Run `bash setup.sh` once inside the clone to create the Python environment. The weekly script must run from this clone, because that is the copy that can push to GitHub.
3. On GitHub: **Settings → Pages → Source: Deploy from a branch → Branch: main, folder: / (root) → Save.** The site is live a minute later, at https://farzinahmadi.github.io/measles-risk-tracker/.

To preview the site locally, run `python3 -m http.server 8000` in this folder and open http://localhost:8000. Opening `index.html` directly from disk does not work, because the browser blocks it from loading the data files.

## What the model is

It is the paper's model. The one choice made here is the IPCW training rule for the 4-week horizon, which is Table 2's alternative row (see Training below). `tools/verify_against_paper.py` checks that the code matches the paper. On the paper's own line list it reproduces all 37 features for all 264,604 county-weeks exactly, and it gives identical walk-forward predictions at both horizons.

- **Question.** Among counties that reported no cases this week, which will report at least one confirmed case next week? The secondary horizon is at least one case in the next four weeks. Counties already reporting cases are not ranked.
- **Unit.** County-week, with weeks starting on Monday and cases dated by report. Cases that jurisdictions report only for a health region, or without a county, are kept as separate units. They count toward state and national activity but are never scored.
- **Learner.** XGBoost: 200 trees, depth 4, learning rate 0.05, subsample and column sample 0.8, minimum child weight 5, positive class weighted by the negative-to-positive ratio.
- **Features (37).**
  - Case history: cases in the last 4 and 8 weeks, cumulative cases, ever affected, weeks since the last case.
  - Activity elsewhere: other cases in the state last week, national cases in the last two weeks.
  - Commuting importation: six features from the ACS 2016–2020 commuting flows.
  - Commuting-network diffusion: eight features.
  - Seasonality.
  - Static county inputs: population, density, MMR susceptibility, seven SVI sub-indices, 2024 vote share and COVID-19 vaccine uptake.

  All case features use only earlier weeks.
- **Training.** Each week the model is retrained on every quiet county-week whose outcome is known. The 4-week model uses the censoring-aware rule with IPCW weights. It keeps:
  - rows whose 4-week window has closed;
  - rows with a case already seen;
  - rows whose window is still open with no case yet, as negatives weighted by the share of the window observed (m/4).

  The plain censoring-aware rule in the Methods drops that third group, so the last three weeks enter training as positives only. In the September 2026 surge this inflated the live 4-week scores: 116 newly reporting counties were expected over four weeks, against about 62 implied by the next-week model. The IPCW rule ranks counties almost the same way (41 of the top 50 in common, Spearman 0.95) without the inflation. Set `PROTOCOL_4WK = "cens"` in `tracker/config.py` to switch back.
- **Probabilities.** Platt scaling, fitted each week on the last 8 weeks of out-of-sample (walk-forward) predictions. The ranking comes from the raw model score, so calibration does not change it. The 8-week window was chosen over 26 weeks in a check on the 30 weeks to mid-September 2026. The expected weekly number of newly reporting counties was within 4.3 of the observed number on average, against 5.4 with 26 weeks. Log loss was also slightly lower. Probabilities still lag when activity changes fast, and the 4-week ones most, because their calibration can only use windows that closed at least four weeks earlier. The live record shows expected against observed counts.
- **What changes from the paper's inputs.** Cases come from the public `measles_data` file instead of the internal line list. On the weeks both cover, 98.6% of county-weeks with cases match exactly; the rest are later revisions. MMR comes from the current `MMR_data` release (v2, school years through 2024–25), which covers about 2,760 counties instead of 2,240.

In the paper's 81-week evaluation, the top 50 counties each week held on average 37% of next-week onsets (2.9 of 8.2 a week), with AUROC 0.84. At four weeks they held 7.4 of 26 a week (precision 15%).

## Output files

| File | Contents |
|---|---|
| `data/latest/forecast.csv` | This week's forecast, one row per county (3,144) |
| `data/latest/site.json` | Everything the web page shows |
| `data/latest/metadata.json` | Run details, including the upstream commits used |
| `data/latest/calibration.json` | This week's Platt parameters |
| `data/archive/forecasts/<week>.csv` | Every published forecast, unchanged, named by the first day of the forecast week |
| `data/archive/index.csv` | One row per published forecast |
| `data/evaluation/scorecard.csv` | Each archived forecast scored against later reports (onsets, caught in top 50, AUROC, expected) |
| `data/evaluation/scorecard_onsets.csv` | Every newly reporting county and the rank it had been given |
| `data/evaluation/backtest_weekly.csv`, `backtest_summary.json` | This week's 26-week walk-forward backtest for the model and two simple rules: onsets and onsets caught in the top 50 per week, AUROC, AUPRC, recall |

Columns of `forecast.csv`:

| Column | Meaning |
|---|---|
| `forecast_origin_week`, `target_week_start` | Monday of the last observed week, and Monday of the forecast week |
| `fips`, `county`, `state`, `state_abbr` | 2022 county geography (Connecticut planning regions) |
| `status` | `quiet` (no cases this week; ranked) or `active` (reporting cases; not ranked) |
| `prob_case_next_week` | Calibrated probability of at least one reported case in the forecast week |
| `national_rank_next_week`, `state_rank_next_week` | Rank among quiet counties (1 = highest) |
| `tier_next_week` | Top 50, 51–150, 151–500 or 501+ |
| `relative_risk_next_week` | Probability divided by the average for quiet counties |
| `…_next_4_weeks` | The same for the four weeks starting with the forecast week |
| `model_score_next_week`, `model_score_next_4_weeks` | Uncalibrated model score, used for ranking |
| `cases_this_week`, `cases_last_4_weeks`, `cumulative_cases_since_2025`, `weeks_since_last_case` | Recent history (empty `weeks_since_last_case` = never reported a case) |
| `kindergarten_mmr_coverage`, `mmr_school_year` | Latest county MMR coverage, where published |

## Folder layout

```
update.py            the weekly script
setup.sh             one-time setup
index.html, site/    the web page (d3 and the county map are included; no external requests)
tracker/             sources.py (upstream data and vintages), features.py, model.py, publish.py, config.py
static/              inputs that do not change weekly: county list and population (SVI 2022), static covariates,
                     ACS commuting flows, region populations. Rebuild with tools/build_static.py.
tools/               build_static.py, verify_against_paper.py
data/                outputs (see above)
```

## Data and credits

- Case data: JHU Measles Tracking Team Data Repository at Johns Hopkins University ([measles_data](https://github.com/CSSEGISandData/measles_data)), CC BY 4.0. The total is cross-checked each week against the dashboard summary in [measles_tracker_test](https://github.com/CSSEGISandData/measles_tracker_test).
- MMR coverage: [MMR_data](https://github.com/CSSEGISandData/MMR_data). Dong E, Saiyed S, Nearchou A, Okura Y, Gardner LM. *JAMA* 2025. doi:10.1001/jama.2025.8952.
- CDC/ATSDR Social Vulnerability Index 2022; ACS 2016–2020 county-to-county commuting flows; MIT Election Data and Science Lab county presidential returns; CDC COVID-19 vaccination data.
- County boundaries: U.S. Census Bureau cartographic boundary files (2023), via us-atlas (ISC license). Charts: d3 (ISC).

## Before making the repository public

- Choose a license for the code (MIT is common) and confirm the CC BY 4.0 statement for the forecasts on the page.
- Confirm the method citation and author line at the end of "How it works" in `site/app.js`.

## Troubleshooting

- **"The Friday update may not be published yet."** The upstream repo has no update for this week. Wait for it, or run with `--allow-stale`.
- **`git push` fails.** Push once by hand (`git push`) to see the error, usually authentication. The GitHub CLI (`gh auth login`) or a personal access token fixes it.
- **xgboost will not load on a Mac.** Run `brew install libomp`.
- **Running it automatically.** The same script can run in GitHub Actions on a Friday-night schedule, so no local run is needed. Ask if you want that set up.

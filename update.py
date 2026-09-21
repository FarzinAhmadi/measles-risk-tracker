#!/usr/bin/env python3
"""
update.py — the weekly update for the US Measles Risk Tracker.

Run it after the JHU measles tracker's Friday update:

    python update.py

It pulls the latest county case updates and MMR coverage from the public JHU GitHub repos, rebuilds the
county-week panel, retrains the early-warning model (manuscript draft v0.2), forecasts which quiet counties
will report a case next week and in the next four weeks, re-scores past forecasts against what was
reported, writes the open-data CSVs and the site's data file, and commits and pushes to GitHub.

Options:
    --as-of YYYY-MM-DD   make the forecast as of that Friday, from the data as they were public then
    --backfill YYYY-MM-DD  also create archived forecasts for every Friday from this date on, each made only from the
                         data that were public that Friday (one-time; about a minute per week)
    --quick              skip the backtest (reuses the last calibration)
    --no-push            commit locally but do not push;   --no-git  do not touch git at all
    --offline            use the cached copies of the upstream repos
    --allow-stale        run even if the upstream repo has no update for the as-of week
"""
import argparse, json, os, subprocess, sys, time
from datetime import date, datetime, timedelta, timezone

# Use the tracker's own Python environment when it exists (created by setup.sh).
_VENV = os.environ.get("MRT_VENV", os.path.join(os.path.expanduser("~"), ".venvs", "measles-risk-tracker"))
_VPY = os.path.join(_VENV, "bin", "python")
if os.path.exists(_VPY) and os.path.realpath(sys.prefix) != os.path.realpath(_VENV):
    os.execv(_VPY, [_VPY] + sys.argv)

try:
    import numpy as np, pandas as pd
    import xgboost  # noqa: F401
    import sklearn  # noqa: F401
except ImportError as e:
    sys.exit(f"Missing package ({e.name}). Run the one-time setup first:  bash setup.sh\n"
             "On a Mac, if xgboost complains about libomp:  brew install libomp")

from tracker import sources, features, model, publish
from tracker.config import (ROOT, LATEST, EVAL, FEATURES, PROTOCOL_4WK, BACKTEST_WEEKS, CALIBRATION_WEEKS,
                            CALIBRATION_MIN_ONSETS, WATCHLIST_K, MODEL_VERSION, SOURCES, TIERS)


def log(msg=""):
    print(msg, flush=True)


def forecast(as_of: date, backtest=True, allow_stale=False, mode="live"):
    """Make the forecast as of Friday `as_of`. Returns everything the publisher needs."""
    t0 = time.time()
    cutoff = sources.vintage_cutoff(as_of)
    raw, cases_sha, cases_time = sources.read_vintage("cases", cutoff)
    mraw, mmr_sha, mmr_time = sources.read_vintage("mmr", cutoff)
    try:
        traw, tr_sha, tr_time = sources.read_vintage("tracker", cutoff)
        tracker_total = sources.parse_tracker_total(traw)
    except Exception:
        tr_sha, tracker_total = None, None

    stale_days = (as_of - cases_time.astimezone(sources.TZ).date()).days
    if stale_days > 2:
        msg = (f"The newest case update on or before Friday {as_of} is from {cases_time:%a %Y-%m-%d} ({stale_days} days earlier).")
        if not allow_stale:
            raise SystemExit(msg + "\nThe Friday update may not be published yet. Re-run after it is, or pass --allow-stale.")
        log("  note: " + msg)

    inc = sources.parse_cases(raw, as_of)
    mmr = sources.parse_mmr(mraw)
    origin_week = pd.Timestamp(as_of - timedelta(days=as_of.weekday()))
    st = features.load_static()
    panel, info = features.build_panel(inc, origin_week, mmr, st)
    total = float(inc.value.sum())
    log(f"  data as of Fri {as_of}: {int(total):,} cases in {inc.unit.nunique()} reporting units "
        f"(measles_data@{cases_sha[:7]}, {cases_time:%Y-%m-%d %H:%M}); panel {info['weeks']} weeks x {info['units']:,} units")
    if tracker_total is not None and tracker_total != int(total) and mode == "live":
        log(f"  note: the tracker dashboard summary reports {tracker_total:,} total cases; the county file sums to {int(total):,}")
    if info["unmapped_units"]:
        log(f"  note: {int(info['unmapped_cases'])} cases in units without a 2022 county code were left out: {', '.join(info['unmapped_units'])}")

    df = model.add_4wk_labels(panel)
    P = model.Panel(df, FEATURES)
    W = int(df.widx.max())

    te1, p1, _, ntr1, npos1 = model.fit_predict(P, W, 1)
    te4, p4, _, ntr4, npos4 = model.fit_predict(P, W, 4, PROTOCOL_4WK)
    assert (te1 == te4).all()
    log(f"  trained: 1-week model on {ntr1:,} county-weeks ({npos1} onsets); 4-week model on {ntr4:,} ({npos4} onsets)")

    perf, weekly, calib = {}, None, None
    if backtest:
        # Walk-forward refits for recent weeks. They give the calibration (last CALIBRATION_WEEKS weeks) and,
        # for the live run, the site's performance panel (last BACKTEST_WEEKS weeks).
        nb = BACKTEST_WEEKS if mode == "live" else CALIBRATION_WEEKS + 4
        log(f"  backtest: refitting the model for each of the last {nb} weeks (walk-forward) ...")
        b1 = model.heuristic_scores(model.walkforward(P, list(range(max(1, W - nb), W)), 1))
        b4 = model.heuristic_scores(model.walkforward(P, list(range(max(1, W - 3 - nb), W - 3)), 4, PROTOCOL_4WK))
        calib = {"computed_for": str(as_of), "weeks": CALIBRATION_WEEKS}
        for h, b in (("1wk", b1), ("4wk", b4)):
            wks = sorted(b.widx.unique())
            n = CALIBRATION_WEEKS
            while n < len(wks) and b[b.widx >= wks[-n]].y.sum() < CALIBRATION_MIN_ONSETS:
                n += 1
            c = b[b.widx >= wks[-n]]
            calib[h] = model.fit_platt(c.p, c.y)
            calib[f"fit_weeks_{h}"] = [c.week_label.min(), c.week_label.max()]
        if mode == "live":
            for h, b in (("1wk", b1), ("4wk", b4)):
                perf[h] = {"model": model.summarize(b, "p"), "rule_recency_population": model.summarize(b, "h_recency_pop"),
                           "rule_recency": model.summarize(b, "h_recency")}
                wt = model.week_table(b, WATCHLIST_K)
                wt["caught_rule"] = model.week_table(b, WATCHLIST_K, "h_recency_pop")[f"caught@{WATCHLIST_K}"].values
                wt["horizon"] = h
                weekly = wt if weekly is None else pd.concat([weekly, wt], ignore_index=True)
            m1 = perf["1wk"]["model"]; m4 = perf["4wk"]["model"]
            log(f"  backtest 1-week: AUROC {m1.get('AUROC')}, top-50 caught {m1.get('caught@50')} of {m1.get('onsets_per_week')} onsets/week; "
                f"4-week: AUROC {m4.get('AUROC')}, caught {m4.get('caught@50')} of {m4.get('onsets_per_week')}")
    else:
        cpath = os.path.join(LATEST, "calibration.json")
        if mode == "live" and os.path.exists(cpath):
            calib = json.load(open(cpath))
            log(f"  quick mode: reusing calibration computed for {calib.get('computed_for')}")

    if calib:
        cal1 = lambda p: model.apply_platt(p, calib["1wk"])
        cal4 = lambda p: model.apply_platt(p, calib["4wk"])
    else:
        cal1 = cal4 = lambda p: np.full(len(p), np.nan)

    tab = publish.county_table(df, W, te1, p1, p4, cal1, cal4, mmr, st["svi"])
    target = origin_week + timedelta(days=7)
    meta = {
        "as_of": str(as_of), "origin_week": origin_week.strftime("%Y-%m-%d"),
        "target_week_start": target.strftime("%Y-%m-%d"), "target_week_end": (target + timedelta(days=6)).strftime("%Y-%m-%d"),
        "target_4wk_end": (target + timedelta(days=27)).strftime("%Y-%m-%d"),
        "run_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": mode,
        "model_version": MODEL_VERSION, "protocol_4wk": PROTOCOL_4WK,
        "cases_commit": cases_sha, "cases_commit_time": cases_time.isoformat(),
        "mmr_commit": mmr_sha, "mmr_commit_time": mmr_time.isoformat(), "tracker_commit": tr_sha,
        "total_cases": int(total), "tracker_total_cases": tracker_total,
        "counties_scored": int(te1.sum()), "counties_active": int((tab.status == "active").sum()),
        "expected_onsets_1wk": round(float(np.nansum(tab.p_1wk)), 2) if calib else None,
        "expected_onsets_4wk": round(float(np.nansum(tab.p_4wk)), 2) if calib else None,
        "train_rows_1wk": ntr1, "train_onsets_1wk": npos1, "train_rows_4wk": ntr4, "train_onsets_4wk": npos4,
        "panel": info, "seconds": round(time.time() - t0, 1),
    }
    return dict(meta=meta, tab=tab, df=df, W=W, perf=perf, weekly=weekly, calib=calib, mmr=mmr)


def publish_latest(r):
    meta, tab, df, W = r["meta"], r["tab"], r["df"], r["W"]
    o = publish.open_csv(tab, meta)
    os.makedirs(LATEST, exist_ok=True)
    o.to_csv(os.path.join(LATEST, "forecast.csv"), index=False)
    publish.write_archive(o, meta)
    if r["calib"]:
        json.dump(r["calib"], open(os.path.join(LATEST, "calibration.json"), "w"), indent=1)
    if r["weekly"] is not None:
        os.makedirs(EVAL, exist_ok=True)
        r["weekly"].to_csv(os.path.join(EVAL, "backtest_weekly.csv"), index=False)
        json.dump(r["perf"], open(os.path.join(EVAL, "backtest_summary.json"), "w"), indent=1)
    perf = r["perf"] or (json.load(open(os.path.join(EVAL, "backtest_summary.json")))
                         if os.path.exists(os.path.join(EVAL, "backtest_summary.json")) else {})
    weekly = r["weekly"] if r["weekly"] is not None else (pd.read_csv(os.path.join(EVAL, "backtest_weekly.csv"))
                                                          if os.path.exists(os.path.join(EVAL, "backtest_weekly.csv")) else pd.DataFrame())
    sc, de = publish.scorecard(df)
    nat = publish.national_series(df)
    states = publish.state_summary(tab, df, W)
    regions = df[(df.is_region == 1) & (df.widx > W - 4) & (df.widx <= W) & (df.value > 0)]
    regions = regions.groupby(["unit", "state"])["value"].sum().reset_index()
    regions["name"] = regions.unit.str.split("|").str[1]

    cols = ["fips", "county", "state_abbr", "status", "p_1wk", "rank_1wk", "p_4wk", "rank_4wk", "cases_this_week",
            "cases_last_4_weeks", "cumulative_cases", "weeks_since_last_case", "mmr_coverage", "state_rank_1wk", "state_rank_4wk"]
    t = tab[cols].copy()
    for c in ("p_1wk", "p_4wk"): t[c] = t[c].round(6)
    t["mmr_coverage"] = t["mmr_coverage"].round(3)
    site = {
        "meta": {k: v for k, v in meta.items() if k != "panel"},
        "sources": {k: {"repo": s["repo"].replace(".git", ""), "file": s["file"], "label": s["label"]} for k, s in SOURCES.items()},
        "tiers": [name for _, name in TIERS], "tier_cuts": [c for c, _ in TIERS[:-1]],
        "performance": perf,
        "backtest_weekly": publish.records(weekly) if len(weekly) else [],
        "scorecard": publish.records(sc) if len(sc) else [],
        "scorecard_onsets": publish.records(de) if len(de) else [],
        "national": publish.records(nat),
        "states": publish.records(states),
        "regions": publish.records(regions[["name", "state", "value"]]),
        "county_columns": cols,
        "counties": t.replace({np.nan: None}).values.tolist(),
    }
    publish.write_json(site, os.path.join(LATEST, "site.json"))
    json.dump({k: v for k, v in meta.items()}, open(os.path.join(LATEST, "metadata.json"), "w"), indent=1, default=str)
    return sc


def git_publish(meta, push=True):
    def g(*a):
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
    if g("rev-parse", "--is-inside-work-tree").returncode != 0:
        log("  git: this folder is not a git repository yet — skipping commit (see README, 'First-time setup').")
        return
    g("add", "data")
    if g("diff", "--cached", "--quiet").returncode == 0:
        log("  git: nothing changed since the last commit.")
        return
    msg = f"Forecast for the week of {meta['target_week_start']} (data through {meta['as_of']})"
    c = g("commit", "-m", msg)
    if c.returncode != 0:
        log("  git commit failed:\n" + c.stderr); return
    log(f"  git: committed \"{msg}\"")
    if push:
        if not g("remote").stdout.strip():
            log("  git: no remote configured — skipping push."); return
        p = g("push")
        log("  git: pushed to GitHub." if p.returncode == 0 else "  git push failed:\n" + p.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", help="forecast as of this Friday (YYYY-MM-DD); default: the most recent Friday")
    ap.add_argument("--backfill", help="also archive real-time forecasts for every Friday from this date on")
    ap.add_argument("--quick", action="store_true", help="skip the backtest and reuse the last calibration")
    ap.add_argument("--no-push", action="store_true"); ap.add_argument("--no-git", action="store_true")
    ap.add_argument("--offline", action="store_true"); ap.add_argument("--allow-stale", action="store_true")
    a = ap.parse_args()

    import shutil
    if not shutil.which("git"):
        sys.exit("git is not installed. On a Mac, run:  xcode-select --install")
    today = datetime.now(sources.TZ).date()
    as_of = date.fromisoformat(a.as_of) if a.as_of else sources.latest_friday(today)
    if as_of.weekday() != 4:
        sys.exit(f"--as-of must be a Friday ({as_of} is a {as_of:%A}).")
    log(f"US Measles Risk Tracker update — forecast as of Friday {as_of}")
    log("1/4 syncing upstream data (CSSEGISandData/measles_data, MMR_data, measles_tracker_test) ...")
    sources.sync(offline=a.offline, log=log)

    if a.backfill:
        d = date.fromisoformat(a.backfill); d = d + timedelta(days=(4 - d.weekday()) % 7)
        log(f"   backfilling archived forecasts from Friday {d} to {as_of - timedelta(days=7)} (real-time data vintages) ...")
        while d < as_of:
            r = forecast(d, backtest=not a.quick, allow_stale=True, mode="backfill")
            publish.write_archive(publish.open_csv(r["tab"], r["meta"]), r["meta"])
            log(f"   archived forecast for the week of {r['meta']['target_week_start']}")
            d += timedelta(days=7)

    log("2/4 building features and training the model ...")
    r = forecast(as_of, backtest=not a.quick, allow_stale=a.allow_stale)
    log("3/4 writing forecasts, archive, scorecard and site data ...")
    sc = publish_latest(r)
    m = r["meta"]
    top = r["tab"].head(10)
    log(f"   forecast for the week of {m['target_week_start']} to {m['target_week_end']}: {m['counties_scored']:,} quiet counties scored, "
        f"{m['counties_active']} active; expected new counties next week ≈ {m['expected_onsets_1wk']}")
    log("   top 10 next week: " + "; ".join(f"{c}, {s}" for c, s in zip(top.county, top.state_abbr)))
    if len(sc):
        last = sc[sc.horizon == "1wk"].tail(1)
        if len(last):
            x = last.iloc[0]
            log(f"   last scored week ({x.target_week_start}): {x.onsets} quiet counties reported a case; {x[f'caught_top{WATCHLIST_K}']} were in the top 50")
    log("4/4 publishing ...")
    if not a.no_git:
        git_publish(m, push=not a.no_push)
    log(f"done in {m['seconds']:.0f} s. Preview the site locally:  python3 -m http.server 8000   then open http://localhost:8000")


if __name__ == "__main__":
    main()

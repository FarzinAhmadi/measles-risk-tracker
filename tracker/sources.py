"""
Upstream data: keep local clones of the public JHU repos under .cache/upstream and read any file
as it stood at a given time (its "vintage"), so every forecast can be reproduced from the exact
data that were public when it was made.
"""
import io, os, subprocess
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

import pandas as pd

from .config import SOURCES, UPSTREAM_DIR, TIMEZONE

TZ = ZoneInfo(TIMEZONE)


def _git(args, cwd=None):
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def sync(offline=False, log=print):
    """Clone or update every upstream repo. With offline=True, use the cached clones as they are."""
    os.makedirs(UPSTREAM_DIR, exist_ok=True)
    for key, s in SOURCES.items():
        path = os.path.join(UPSTREAM_DIR, s["dir"])
        if not os.path.isdir(os.path.join(path, ".git")):
            if offline:
                raise RuntimeError(f"No cached copy of {s['repo']}; run once without --offline.")
            log(f"  cloning {s['repo']}")
            _git(["clone", "--quiet", s["repo"], path])
        elif not offline:
            _git(["fetch", "--quiet", "origin"], cwd=path)
            _git(["reset", "--quiet", "--hard", "origin/HEAD"], cwd=path)


def vintage_cutoff(as_of: date) -> datetime:
    """Upstream commits up to Saturday 12:00 (Eastern) after the as-of Friday count as that Friday's update."""
    return datetime.combine(as_of + timedelta(days=1), datetime.min.time(), TZ) + timedelta(hours=12)


def read_vintage(key, cutoff: datetime):
    """Return (DataFrame, commit sha, commit time) for SOURCES[key]['file'] as of `cutoff`."""
    s = SOURCES[key]
    path = os.path.join(UPSTREAM_DIR, s["dir"])
    out = ""
    for fname in [s["file"]] + s.get("older_files", []):
        out = _git(["log", "-1", "--format=%H %cI", f"--before={cutoff.isoformat()}", "origin/HEAD", "--", fname], cwd=path).strip()
        if out:
            sha, when = out.split(" ", 1)
            try:
                text = _git(["show", f"{sha}:{fname}"], cwd=path)
                break
            except RuntimeError:            # the commit deleted the file; take the version just before it
                sha = _git(["rev-parse", f"{sha}^"], cwd=path).strip()
                text = _git(["show", f"{sha}:{fname}"], cwd=path)
                break
    if not out:
        raise RuntimeError(f"{s['file']} has no version on or before {cutoff:%Y-%m-%d %H:%M %Z}")
    df = pd.read_csv(io.StringIO(text.lstrip("﻿")), dtype=str)
    df.columns = [c.strip().lstrip("﻿") for c in df.columns]
    return df, sha, datetime.fromisoformat(when)


def latest_friday(today: date) -> date:
    return today - timedelta(days=(today.weekday() - 4) % 7)


# ------------------------------------------------------------------ parsing
STATE_ABBR = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
              "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
              "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
              "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
              "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
              "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
              "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
              "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
              "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
              "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming"}


def parse_cases(raw: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """measles_county_all_updates.csv -> incident cases [unit, state, is_region, date, value], dated <= as_of.

    Units follow the paper pipeline: reports with a county FIPS become that county ("01001"); reports for a
    health region or an unassigned part of a state become a region unit ("Utah|Southwest Health District").
    """
    a = raw.copy()
    a = a[a["outcome_type"].str.strip() == "case_lab-confirmed"]
    a["value"] = pd.to_numeric(a["value"], errors="coerce")
    a["date"] = pd.to_datetime(a["date"], errors="coerce")
    a = a.dropna(subset=["value", "date"])
    a["value"] = a["value"].clip(lower=0)
    a = a[a["date"].dt.date <= as_of]
    lid = a["location_id"].fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    parts = a["location_name"].astype(str).str.rsplit(",", n=1)
    cname = parts.str[0].str.strip()
    st = parts.str[-1].str.strip()
    st = st.where(st.str.len() != 2, st.map(STATE_ABBR)).fillna(st)
    is_county = lid.str.fullmatch(r"\d{4,5}")
    out = pd.DataFrame({
        "unit": [f if c else f"{s}|{n}" for f, c, s, n in zip(lid.str.zfill(5), is_county, st, cname)],
        "state": st.values, "is_region": (~is_county).values, "date": a["date"].values, "value": a["value"].values,
    })
    return out.reset_index(drop=True)


def parse_mmr(raw: pd.DataFrame) -> pd.DataFrame:
    """mmr_data_us_counties_v2.csv -> [fips5, mmr, mmr_year] using each county's latest reported school year."""
    m = raw.copy()
    sy = [c for c in m.columns if c.startswith("SY")]
    m[sy] = m[sy].apply(pd.to_numeric, errors="coerce")
    m["mmr"] = m[sy].ffill(axis=1).iloc[:, -1]
    last = m[sy].notna().iloc[:, ::-1].idxmax(axis=1)
    m["mmr_year"] = last.where(m[sy].notna().any(axis=1)).str.replace("SY", "").str.replace("_", "-")
    m = m[pd.to_numeric(m["FIPS"], errors="coerce").notna()].copy()
    m["fips5"] = pd.to_numeric(m["FIPS"]).astype(int).astype(str).str.zfill(5)
    return m[["fips5", "mmr", "mmr_year"]].drop_duplicates("fips5")


def parse_tracker_total(raw: pd.DataFrame):
    try:
        v = raw.set_index("Metric").loc["Total Cases", "Value"]
        return int(float(str(v).replace(",", "")))
    except Exception:
        return None

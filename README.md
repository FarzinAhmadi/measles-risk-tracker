# Maryland Hospital Global Budget Revenue Tracker (site)

This repository publishes one page: a tracker of Maryland's all-payer hospital
global budget system, compiled from Health Services Cost Review Commission
(HSCRC) filings.

**Live page:** https://farzinahmadi.github.io/maryland-gbr-tracker/

**Data:** https://github.com/farzinahmadi/maryland-hospital-data

## What is in here

| File | Purpose |
| --- | --- |
| `index.html` | The tracker. One self-contained file: all styles, scripts and data are inline, nothing is fetched from a CDN. |
| `.nojekyll` | Tells GitHub Pages to serve the files as they are, with no Jekyll processing. |
| `LICENSE` | MIT, covering the page itself. |
| `CITATION.cff` | Citation metadata for the page. Cite the dataset, not the page, for research use. |

This repository holds no data pipeline and no build system on purpose. The page
is generated in the data repository (`src/build_public_page.py`) and the finished
file is copied here.

## Enabling GitHub Pages

1. Create a public repository named `maryland-gbr-tracker`.
2. Upload the contents of this folder to the repository root, on the `main` branch.
3. Settings -> Pages -> Build and deployment -> Source: **Deploy from a branch**;
   Branch: **main**, folder: **/ (root)**. Save.
4. Wait for the first deployment, then open
   https://farzinahmadi.github.io/maryland-gbr-tracker/

## Updating the page

The page is rebuilt in the data project, not here.

```bash
# in the maryland-hospital-data working copy
python3 src/build_public_data.py
python3 src/build_public_geo.py
python3 src/build_public_page.py
cp docs/public.html /path/to/maryland-gbr-tracker/index.html
```

Then commit `index.html` in this repository. Set `GBR_DRAFT=1` before the build
to restore the "Draft. Not published." banner for a private preview.

## Licensing

The page and its markup are MIT licensed. The underlying compiled dataset is
released separately under CC BY 4.0 in the data repository. The source files are
published by HSCRC and CMS; HSCRC is the system of record.

This project is not affiliated with, endorsed by, or speaking for the Health
Services Cost Review Commission.

Compiled by Farzin Ahmadi, Assistant Professor of Healthcare Management,
Towson University. fahmadi@towson.edu

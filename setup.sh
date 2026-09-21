#!/usr/bin/env bash
# One-time setup for the US Measles Risk Tracker.
#
#   bash setup.sh                                                   # Python environment + local git repository
#   bash setup.sh https://github.com/<you>/measles-risk-tracker.git # ...and connect it to your (empty) GitHub repo and push
#
# The Python environment goes to ~/.venvs/measles-risk-tracker, outside this folder, so nothing large
# ends up in OneDrive/Dropbox or in git.
set -euo pipefail
cd "$(dirname "$0")"
VENV="${MRT_VENV:-$HOME/.venvs/measles-risk-tracker}"

echo "1/3 Python environment in $VENV"
python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10 or newer is needed"'
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r requirements.txt
if ! "$VENV/bin/python" -c "import xgboost" 2>/dev/null; then
  echo "xgboost is installed but cannot load. On a Mac this usually means OpenMP is missing:"
  echo "    brew install libomp      (then run: bash setup.sh)"
  exit 1
fi

echo "2/3 git repository"
if [ ! -d .git ]; then git init --quiet -b main; fi
git add -A
git commit --quiet -m "US Measles Risk Tracker: model, site and first forecast" || echo "   (nothing new to commit)"

if [ -n "${1:-}" ]; then
  echo "3/3 connecting to $1 and pushing"
  git remote add origin "$1" 2>/dev/null || git remote set-url origin "$1"
  git push -u origin main
  echo
  echo "Now turn on the website: on GitHub open the repo > Settings > Pages >"
  echo "  Source: 'Deploy from a branch', Branch: main, Folder: / (root) > Save."
else
  echo "3/3 skipped (no GitHub URL given). Later:  git remote add origin <url> && git push -u origin main"
fi
echo
echo "Done. Every Friday after the JHU update, run:   python3 update.py"

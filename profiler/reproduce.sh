#!/bin/sh
# Reproduce ONE cold-start measurement on Nosana (live run).
# Usage:
#   bash reproduce.sh 01-ollama-qwen3-8b-4090
#   bash reproduce.sh --list        # list combos
#
# What it does:
#   1. Checks the market has a free host first (scout.py --once).
#   2. Prints the exact manual steps — creating a deployment needs a
#      Nosana account + API key (nos_...), which this script never
#      asks for and never stores. The deployment itself is created
#      in the Nosana dashboard (or with the Nosana CLI) from the
#      job-definition in profiler/job-definitions/, because the API
#      surface for creating deployments must be verified by hand on
#      the day of the run (see README > Live runs).
#   3. After the run, the operator pastes the timings into
#      profiler/RESULTS.md and saves the raw log to profiler/runs/.
#
# It never invents a timing. If any step is skipped, the row in
# RESULTS.md stays NOT MEASURED.
set -eu
cd "$(dirname "$0")/.."   # repo root (this script lives in profiler/)

if [ "${1:-}" = "--list" ] || [ "${1:-}" = "" ]; then
  python3 profiler/measure.py --list
  echo
  echo "Usage: bash reproduce.sh <combo-id>"
  exit 0
fi

COMBO="$1"
JOB="profiler/job-definitions/${COMBO}.json"
if [ ! -f "$JOB" ]; then
  echo "Unknown combo: $COMBO (no $JOB)"
  python3 profiler/measure.py --list
  exit 1
fi

echo "== 1. Check the market has a free host right now =="
python3 scout.py --once

echo
echo "== 2. Create the deployment (manual — needs your Nosana account) =="
echo "Job definition: $JOB"
python3 -c "import json;d=json.load(open('$JOB'));print('  market:', d['deployment']['market_slug'], d['deployment']['market_address']);print('  gpu:', d['deployment']['gpu_model'], '| model:', d['deployment']['model']);print('  strategy:', d['deployment']['strategy'], '| replicas:', d['deployment']['replica_count'], '| timeout:', d['deployment']['container_timeout_minutes'], 'min')"
echo
echo "In the Nosana dashboard (deploy.nosana.com) or CLI, create the"
echo "deployment from that job-definition file, then record:"
echo "  t0             time you posted the deployment (UTC)"
echo "  t_running      time the deployment first reports RUNNING"
echo "  t_image_ready  time of the first container log line"
echo "  t_model_ready  time the ready marker appears (see profiler/combos.json)"
echo "  t_first_token  time of the first token / first output"
echo "  node           node address that served the job"
echo "  cost_usd       cost shown by Nosana for the run"
echo
echo "== 3. Record it =="
echo "Paste the numbers into profiler/RESULTS.md (Measured table),"
echo "save the raw log to profiler/runs/<combo>-<date>.log, and"
echo "record the run JSON with: python3 profiler/measure.py --dry-run"
echo "(dry-run validates the record shape; timings come from your run only)."

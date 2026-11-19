#!/usr/bin/env bash
# Terminal 1: SDN controller (Member B).   bash scripts/run_controller.sh [baseline]
cd "$(dirname "$0")/.." && mkdir -p logs
# take back files left root-owned by an earlier sudo run (demo.sh, experiments)
[ -n "$(find logs results ! -user "$(id -u)" 2>/dev/null | head -1)" ] && sudo chown -R "$(id -u):$(id -g)" logs results
[ "$1" == "baseline" ] && export NRRS_BASELINE=1 && echo "== BASELINE MODE =="
~/nrrs-env/bin/osken-manager controller/sdn_controller.py

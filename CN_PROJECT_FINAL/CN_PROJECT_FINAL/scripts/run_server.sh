#!/usr/bin/env bash
# Terminal 2: TCP server (Member A) + reservation manager (Member C).   bash scripts/run_server.sh [baseline|mock]
cd "$(dirname "$0")/.." && mkdir -p logs
[ -n "$(find logs results ! -user "$(id -u)" 2>/dev/null | head -1)" ] && sudo chown -R "$(id -u):$(id -g)" logs results
[ "$1" == "baseline" ] && export NRRS_BASELINE=1
[ "$1" == "mock" ] && export NRRS_MOCK=1
~/nrrs-env/bin/python -m server.reservation_server

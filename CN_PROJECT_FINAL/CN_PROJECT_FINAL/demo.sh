#!/usr/bin/env bash
# The whole demo, step by step:   bash demo.sh      (Enter = next step, q + Enter = stop)
# Starts the controller, the Mininet network and the TCP server, then runs 13 steps,
# each showing the real command, its real output and what to say.
cd "$(dirname "$0")"
bash scripts/start_ovs.sh
sudo mn -c > /dev/null 2>&1
trap 'sudo chown -R "$(id -u):$(id -g)" logs results 2> /dev/null' EXIT   # give files back after sudo
sudo python3 demo/terminal_demo.py "$@"

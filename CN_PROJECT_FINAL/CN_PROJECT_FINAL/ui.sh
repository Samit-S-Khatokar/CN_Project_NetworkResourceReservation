#!/usr/bin/env bash
# Web interface with a 3D view:   bash ui.sh   then open http://localhost:8000   (Ctrl+C to stop)
cd "$(dirname "$0")"
bash scripts/start_ovs.sh
sudo mn -c > /dev/null 2>&1
trap 'sudo chown -R "$(id -u):$(id -g)" logs results 2> /dev/null' EXIT   # give files back after sudo
( sleep 18; cmd.exe /c start "" "http://localhost:8000" > /dev/null 2>&1 ) &                # open the browser on WSL
sudo python3 ui/server.py "$@"

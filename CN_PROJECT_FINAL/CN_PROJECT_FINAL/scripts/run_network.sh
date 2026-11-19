#!/usr/bin/env bash
# Terminal 3: Mininet network (Member B), opens the mininet> prompt. Type "exit" to stop.
cd "$(dirname "$0")/.."
sudo mn -c > /dev/null 2>&1
sudo python3 topology/sdn_topology.py

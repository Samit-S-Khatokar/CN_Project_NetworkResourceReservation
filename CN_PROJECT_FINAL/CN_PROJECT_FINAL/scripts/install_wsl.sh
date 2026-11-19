#!/usr/bin/env bash
# One-time setup on WSL Ubuntu: Mininet, Open vSwitch, iperf3 (apt) + a Python venv at ~/nrrs-env
set -e
cd "$(dirname "$0")/.."
sudo apt update
sudo DEBIAN_FRONTEND=noninteractive apt install -y mininet openvswitch-switch iperf3 ethtool iproute2 \
    iputils-ping net-tools curl unzip python3-venv python3-pip
python3 -m venv ~/nrrs-env
~/nrrs-env/bin/pip install --upgrade pip
~/nrrs-env/bin/pip install -r requirements.txt
mn --version && ~/nrrs-env/bin/osken-manager --version && iperf3 --version | head -1
echo "Done. Next: bash scripts/start_ovs.sh (every time you open WSL)"

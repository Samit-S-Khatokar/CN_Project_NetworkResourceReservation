#!/usr/bin/env bash
# Start Open vSwitch (run every time you open WSL). WSL has no OVS kernel module, so the normal
# service script gives up; we start the database and ovs-vswitchd (userspace datapath) ourselves.
running() { sudo ovs-vsctl --timeout=2 show >/dev/null 2>&1 && pgrep -x ovs-vswitchd >/dev/null; }
if running; then echo "Open vSwitch is already running"; exit 0; fi
sudo /usr/share/openvswitch/scripts/ovs-ctl start --no-ovs-vswitchd --system-id=random
pgrep -x ovs-vswitchd >/dev/null || sudo ovs-vswitchd --pidfile --detach --log-file
sleep 1
if running; then echo "Open vSwitch is running"
else echo "Open vSwitch did NOT start - see /var/log/openvswitch/ovs-vswitchd.log"; exit 1; fi

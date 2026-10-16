#!/bin/bash

set -euo pipefail

install -m 644 "${RECIPE_DIR}/files/dhcp.network" /etc/systemd/network/
systemctl enable systemd-networkd.service

# Installing systemd-resolved points `/etc/resolv.conf` to its stub file, which
# must exist so that name resolution keeps working during the installation.
mkdir -p /run/systemd/resolve
cat /etc/resolv.conf >/run/systemd/resolve/stub-resolv.conf
apt-get install -y systemd-resolved
systemctl enable systemd-resolved.service
ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf

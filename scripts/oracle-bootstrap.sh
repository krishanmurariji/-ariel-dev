#!/usr/bin/env bash
# One-time setup of a fresh Oracle Cloud Always Free Ubuntu VM. Run as the ubuntu user.
set -euo pipefail

# Docker + compose plugin
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi

# Oracle's Ubuntu images ship iptables rules that reject everything except SSH,
# even after opening ports in the cloud Security List. Open 80/443 before the REJECT rule.
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo apt-get install -y iptables-persistent >/dev/null
sudo netfilter-persistent save

# Swap as a safety net (SWAP_GB=4 on the 1 GB Micro VM)
if [ ! -f /swapfile ]; then
  sudo fallocate -l "${SWAP_GB:-2}G" /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi
echo "Done. Log out and back in (docker group), then follow docs/ORACLE_DEPLOY.md step 4."

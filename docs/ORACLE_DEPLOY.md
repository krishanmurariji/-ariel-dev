# Deploying to Oracle Cloud Always Free (replaces Render)

**Cost: $0 forever** if you stay inside Always Free: one Ampere A1 VM (up to 4 OCPU / 24 GB RAM), 200 GB block storage, 10 TB/month egress, plus a free DuckDNS domain and free Let's Encrypt HTTPS. Unlike Render's free tier there is no sleep/spin-down and no 90-day database expiry.

## 1. Create the VM
1. Sign up at cloud.oracle.com (card needed for verification only; choose **Always Free** resources and never click "Upgrade").
2. Compute -> Instances -> Create: image **Ubuntu 24.04** (aarch64), shape **VM.Standard.A1.Flex** with **2 OCPU / 12 GB** (up to 4/24 is still free), boot volume 100 GB, upload your SSH public key.
3. If you get "Out of capacity", retry later or try another availability domain; it is a common Oracle issue.
4. Networking -> your VCN -> Security List -> add Ingress rules: TCP **80** and **443** from `0.0.0.0/0`.

## 2. Free domain
At duckdns.org create a subdomain (e.g. `arielhr`) and point it to the VM's public IP.

## 3. Bootstrap the VM
```bash
ssh ubuntu@<VM_PUBLIC_IP>
git clone -b render https://github.com/krishanmurariji/-ariel-dev.git && cd -ariel-dev
./scripts/oracle-bootstrap.sh      # installs Docker, opens 80/443 in iptables, adds swap
exit                                # log back in so the docker group applies
```

## 4. Configure and start
```bash
cd -ariel-dev
cp .env.oracle.example .env.oracle && nano .env.oracle   # set domain + generate the 4 secrets
docker compose -f docker-compose.oracle.yml --env-file .env.oracle up -d --build
docker compose -f docker-compose.oracle.yml --env-file .env.oracle logs -f web
```
The first build on ARM takes ~10 minutes. The entrypoint runs `migrate` and `collectstatic` automatically. Open `https://<your-domain>/login/`.

## 5. Update later
```bash
git pull && docker compose -f docker-compose.oracle.yml --env-file .env.oracle up -d --build
```

## 6. Backups (do this, the VM is your only copy)
```bash
crontab -e   # add:
0 2 * * * /home/ubuntu/-ariel-dev/scripts/oracle-backup.sh
```
Copy `backups/` off the VM occasionally (e.g. `scp`).

## Keeping it free
- Stay on Always Free shapes only (A1.Flex totalling <= 4 OCPU/24 GB, or the AMD Micro).
- Oracle can reclaim Always Free VMs that sit idle for 7 days (CPU, network and memory all under ~20% at the 95th percentile). A real HR app with users won't trip it; if it is a quiet demo, keep light traffic going or convert the account to Pay-As-You-Go (still free within limits; this also removes the idle reclaim).
- Ollama is intentionally not in the stack. The A1 can run it, but leave it out unless you need the AI intake feature.

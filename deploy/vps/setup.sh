#!/usr/bin/env bash
# One-time setup of the Crab Intelligent dispatcher (and an optional Claude worker) on Ubuntu 24.04.
# Run as root:  bash setup.sh app.crbi.io
set -euo pipefail
DOMAIN="${1:?usage: setup.sh <domain>}"
REPO="https://github.com/CrabIntelligent/crab-intelligent.git"
APP=/opt/crab
DATA=/var/lib/crab

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3-venv python3-pip git caddy ufw

id crab >/dev/null 2>&1 || useradd --system --home "$DATA" --shell /usr/sbin/nologin crab
mkdir -p "$DATA/worker" /etc/crab
chown -R crab:crab "$DATA"; chmod 750 "$DATA"

if [ -d "$APP/.git" ]; then git -C "$APP" pull -q; else git clone -q "$REPO" "$APP"; fi
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

# The worker's own Solana keypair; its public key is the only burrow the dispatcher accepts for now.
if [ ! -f "$DATA/worker/burrow-1.json" ]; then
  sudo -u crab "$APP/.venv/bin/python" -c "
import sys; sys.path.insert(0, '$APP')
from pathlib import Path
from worker.client import Keypair
print(Keypair(Path('$DATA/worker/burrow-1.json')).pubkey)" > /dev/null
fi
BURROW=$("$APP/.venv/bin/python" -c "import json; print(json.load(open('$DATA/worker/burrow-1.json'))['pubkey'])")

cat > /etc/crab/dispatcher.env <<ENV
CRAB_DEV_MODE=0
CRAB_PUBLIC_URL=https://$DOMAIN
CRAB_DATA_DIR=$DATA
CRAB_BURROW_ALLOWLIST=$BURROW
CRAB_STRICT_CROSS_FAMILY=0
ENV
[ -f /etc/crab/worker.env ] || cat > /etc/crab/worker.env <<ENV
CRAB_SERVER=http://127.0.0.1:8000
# Paste your Anthropic API key after the = sign, then run: systemctl enable --now crab-worker
ANTHROPIC_API_KEY=
CLAUDE_MODEL=claude-opus-5-5
CLAUDE_EFFORT=high
ENV
chmod 640 /etc/crab/*.env; chown root:crab /etc/crab/*.env

cat > /etc/systemd/system/crab-dispatcher.service <<UNIT
[Unit]
Description=Crab Intelligent dispatcher
After=network-online.target
Wants=network-online.target

[Service]
User=crab
WorkingDirectory=$APP
EnvironmentFile=/etc/crab/dispatcher.env
ExecStart=$APP/.venv/bin/uvicorn server.main:app --host 127.0.0.1 --port 8000 --proxy-headers
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/crab-worker.service <<UNIT
[Unit]
Description=Crab Intelligent burrow worker (Claude)
After=crab-dispatcher.service
Requires=crab-dispatcher.service

[Service]
User=crab
WorkingDirectory=$APP
EnvironmentFile=/etc/crab/worker.env
Environment=PYTHONUNBUFFERED=1
ExecStartPre=/bin/sh -c 'test -n "\$\${ANTHROPIC_API_KEY}\$\${ANTHROPIC_AUTH_TOKEN}" || { echo "No Anthropic key in /etc/crab/worker.env"; exit 1; }'
ExecStart=$APP/.venv/bin/python -m worker --name burrow-1 --key $DATA/worker/burrow-1.json --providers claude
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/caddy/Caddyfile <<CADDY
$DOMAIN {
	encode zstd gzip
	reverse_proxy 127.0.0.1:8000
}
CADDY

ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw --force enable >/dev/null

systemctl daemon-reload
systemctl enable --now crab-dispatcher
systemctl restart caddy
sleep 2
echo "dispatcher: $(systemctl is-active crab-dispatcher)  caddy: $(systemctl is-active caddy)"
echo "burrow pubkey: $BURROW"
curl -s -o /dev/null -w "local api: %{http_code}\n" http://127.0.0.1:8000/api/config
echo "next: bash $APP/deploy/vps/set-key.sh   (paste the Anthropic API key; it checks the key and starts the worker)"

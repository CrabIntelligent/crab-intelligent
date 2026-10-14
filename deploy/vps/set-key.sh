#!/usr/bin/env bash
# Asks for the Anthropic API key (hidden), checks it, writes it to /etc/crab/worker.env and starts the worker.
set -euo pipefail
ENV=/etc/crab/worker.env

check() { # $1 = header line; prints the HTTP status of a models listing
  curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://api.anthropic.com/v1/models \
    -H @<(printf '%s\nanthropic-version: 2023-06-01\n' "$1") || echo 000
}

echo
echo "  Crab Intelligent - pasang API key Anthropic"
echo "  1) Klik di jendela ini   2) Cmd+V   3) tekan Enter"
echo "  Key sengaja tidak terlihat saat ditempel, langsung tekan Enter saja."
echo

while true; do
  read -rsp "  API key: " key; echo
  key=$(printf %s "$key" | tr -d '[:space:]')
  case "$key" in
    sk-ant-*) echo "  Diterima (${#key} karakter). Mengecek ke Anthropic..." ;;
    "") echo "  Kosong. Klik di sini, Cmd+V, lalu Enter."; continue ;;
    *) echo "  Key Anthropic diawali sk-ant-. Tempel lagi."; continue ;;
  esac
  var=ANTHROPIC_API_KEY
  code=$(check "x-api-key: $key")
  if [ "$code" = 401 ] || [ "$code" = 403 ]; then
    code=$(check "Authorization: Bearer $key")
    var=ANTHROPIC_AUTH_TOKEN
  fi
  if [ "$code" = 200 ]; then echo "  Key valid."; break; fi
  if [ "$code" = 401 ] || [ "$code" = 403 ]; then echo "  Key ditolak Anthropic ($code). Cek lagi lalu tempel ulang."; continue; fi
  var=ANTHROPIC_API_KEY
  echo "  Tidak bisa mengecek key sekarang (kode $code). Tetap disimpan."; break
done

tmp=$(mktemp /etc/crab/.worker.env.XXXXXX)
K="$key" V="$var" awk '
  /^ANTHROPIC_API_KEY=/ || /^ANTHROPIC_AUTH_TOKEN=/ { next }
  { print }
  END { print ENVIRON["V"] "=" ENVIRON["K"] }' "$ENV" > "$tmp"
chown root:crab "$tmp"; chmod 640 "$tmp"; mv "$tmp" "$ENV"
unset key K

# Let the worker start with either variable set.
mkdir -p /etc/systemd/system/crab-worker.service.d
cat > /etc/systemd/system/crab-worker.service.d/key.conf <<'UNIT'
[Service]
ExecStartPre=
ExecStartPre=/bin/sh -c 'test -n "$${ANTHROPIC_API_KEY}$${ANTHROPIC_AUTH_TOKEN}" || { echo "No Anthropic key in /etc/crab/worker.env"; exit 1; }'
UNIT
systemctl daemon-reload
systemctl enable crab-worker >/dev/null 2>&1
systemctl restart crab-worker
sleep 6
echo
if systemctl is-active --quiet crab-worker; then
  echo "  Worker AKTIF. Selesai, terminal ini boleh ditutup."
else
  echo "  Worker belum aktif. Log terakhir:"
  journalctl -u crab-worker -n 15 --no-pager
fi
echo

#!/usr/bin/env bash
# One-shot deploy of the web interface to a fresh Ubuntu/Debian VPS.
# Usage (as root):
#   curl -fsSL https://raw.githubusercontent.com/tds2fd8z5d-star/Banshee/main/deploy.sh | bash
set -euo pipefail

DOMAIN="${DOMAIN:-oldd-school.duckdns.org}"
REPO="https://github.com/tds2fd8z5d-star/Banshee"
DIR=/opt/site

echo "==> Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git curl
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh

echo "==> Fetching code"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only; else git clone "$REPO" "$DIR"; fi

echo "==> Building image (takes a few minutes)"
docker build --target web -t site-web "$DIR"

echo "==> Starting container"
docker rm -f site >/dev/null 2>&1 || true

if systemctl list-unit-files 2>/dev/null | grep -q '^caddy\.service'; then
    # Caddy already serves ports 80/443: put the site behind it (automatic HTTPS).
    docker run -d --name site --restart unless-stopped -p 127.0.0.1:5000:5000 site-web
    [ -f /etc/caddy/Caddyfile ] && cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak.$(date +%s)"
    cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN, http://$DOMAIN {
    reverse_proxy 127.0.0.1:5000
}
EOF
    systemctl reload caddy || systemctl restart caddy
else
    docker run -d --name site --restart unless-stopped -p 80:5000 site-web
fi

sleep 5
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5000 || true)
[ "$code" = "000" ] && code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1 || true)
echo "==> Done. Site answers locally with HTTP $code (200 = OK)"

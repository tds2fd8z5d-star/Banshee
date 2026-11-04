#!/usr/bin/env bash
# One-shot deploy of both sites to an Ubuntu/Debian VPS:
#   oldd-school.duckdns.org       <- branch main  (original site)
#   akhi-barbershop.duckdns.org  <- static landing sites/akhi-barbershop
# Usage (as root):
#   curl -fsSL https://raw.githubusercontent.com/tds2fd8z5d-star/Banshee/main/deploy.sh | bash
set -euo pipefail

REPO="https://github.com/tds2fd8z5d-star/Banshee"
MAIN_DOMAIN="${MAIN_DOMAIN:-oldd-school.duckdns.org}"
AKHI_DOMAIN="${AKHI_DOMAIN:-akhi-barbershop.duckdns.org}"

echo "==> Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git curl
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh

# deploy NAME BRANCH PORT
deploy() {
    local name=$1 branch=$2 port=$3 dir=/opt/$1
    echo "==> [$name] fetching branch $branch"
    if [ -d "$dir/.git" ]; then
        git -C "$dir" fetch origin "$branch"
        git -C "$dir" checkout -q -B "$branch" "origin/$branch"
    else
        git clone -b "$branch" "$REPO" "$dir"
    fi
    echo "==> [$name] building image (takes a few minutes)"
    docker build --target web -t "$name-web" "$dir"
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker run -d --name "$name" --restart unless-stopped \
        -p "127.0.0.1:$port:5000" "$name-web"
}

docker rm -f akhi >/dev/null 2>&1 || true   # container from the previous script version
deploy site main 5000
LANDING=/opt/site/sites/akhi-barbershop

echo "==> Configuring Caddy"
CADDYFILE="
$MAIN_DOMAIN, http://$MAIN_DOMAIN {
    reverse_proxy 127.0.0.1:5000
}

$AKHI_DOMAIN, http://$AKHI_DOMAIN {
    root * $LANDING
    file_server
    encode gzip
}
"
if systemctl list-unit-files 2>/dev/null | grep -q '^caddy\.service'; then
    [ -f /etc/caddy/Caddyfile ] && cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak.$(date +%s)"
    printf '%s\n' "$CADDYFILE" > /etc/caddy/Caddyfile
    systemctl reload caddy || systemctl restart caddy
else
    # No system Caddy: run it in Docker on the host network.
    mkdir -p /opt/caddy
    printf '%s\n' "$CADDYFILE" > /opt/caddy/Caddyfile
    docker rm -f caddy >/dev/null 2>&1 || true
    docker run -d --name caddy --restart unless-stopped --network host \
        -v /opt/caddy/Caddyfile:/etc/caddy/Caddyfile \
        -v /opt/site/sites:/opt/site/sites:ro \
        -v caddy_data:/data caddy:2
fi

sleep 8
echo "==> site:    HTTP $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5000 || true) (200 = OK)"
echo "==> landing: HTTP $(curl -s -o /dev/null -w '%{http_code}' -H "Host: $AKHI_DOMAIN" http://127.0.0.1 || true) (200 or 308 = OK)"
echo "==> Done:"
echo "    https://$MAIN_DOMAIN"
echo "    https://$AKHI_DOMAIN"

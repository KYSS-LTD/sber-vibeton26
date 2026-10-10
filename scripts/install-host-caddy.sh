#!/usr/bin/env bash
# Install the same pinned Caddy binary as the Compose proxy. Start separately
# after moving that proxy to its loopback listener.
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo 'Run with sudo' >&2; exit 1; }
cd "$(dirname "$0")/.."
temporary=$(mktemp -d /tmp/prohod-caddy.XXXXXX)
trap 'rm -rf -- "$temporary"' EXIT
docker compose cp proxy:/usr/bin/caddy "$temporary/caddy"
chmod 755 "$temporary/caddy"
"$temporary/caddy" version | grep -E '^v2\.10\.2([[:space:]]|$)'
if [[ -e /usr/bin/caddy ]] && ! cmp -s /usr/bin/caddy "$temporary/caddy"; then
  echo 'A different host Caddy already exists; refusing to overwrite it' >&2
  exit 1
fi
install -m 755 "$temporary/caddy" /usr/bin/caddy
getent group caddy >/dev/null || groupadd --system caddy
id caddy >/dev/null 2>&1 || useradd --system --gid caddy --create-home \
  --home-dir /var/lib/caddy --shell /usr/sbin/nologin caddy
install -d -o caddy -g caddy -m 750 /var/lib/caddy
install -d -o root -g caddy -m 750 /etc/caddy
if [[ -e /etc/caddy/Caddyfile ]]; then
  echo 'An existing host Caddyfile was found; refusing to overwrite it' >&2
  exit 1
fi
install -m 640 -o root -g caddy infra/Caddyfile.production /etc/caddy/Caddyfile
install -m 644 infra/caddy.service /etc/systemd/system/caddy.service
runuser -u caddy -- caddy validate --config /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable caddy
echo 'Caddy installed. Move the Compose proxy to loopback before starting caddy.service.'

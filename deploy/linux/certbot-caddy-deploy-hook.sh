#!/bin/sh
set -eu

# Installed at /etc/letsencrypt/renewal-hooks/deploy/mt5-dashboard-caddy.sh.
# Certbot sets RENEWED_LINEAGE to the currently issued certificate directory.
: "${RENEWED_LINEAGE:?Certbot did not set RENEWED_LINEAGE}"
install -d -o root -g caddy -m 750 /etc/caddy/ip-cert
install -o root -g caddy -m 640 "$RENEWED_LINEAGE/fullchain.pem" /etc/caddy/ip-cert/fullchain.pem
install -o root -g caddy -m 640 "$RENEWED_LINEAGE/privkey.pem" /etc/caddy/ip-cert/privkey.pem
systemctl reload caddy

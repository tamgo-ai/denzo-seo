#!/usr/bin/env bash
# Finalize the seo.tamgo.ai subdomain setup once DNS is in place.
# Steps:
#   1. Verify DNS for seo.tamgo.ai resolves to this server.
#   2. Run certbot to issue a Let's Encrypt cert (nginx auto-mode).
#   3. Reload nginx.
#   4. Restart denzo-seo so it picks up the OAUTH_REDIRECT_URI from .env.
#   5. Smoke-test https://seo.tamgo.ai/.
#
# Idempotent — safe to re-run if anything fails midway.

set -euo pipefail

DOMAIN="${1:-seo.tamgo.ai}"
EMAIL="${EMAIL:-raul@tamgo.ai}"
EXPECTED_IP="${EXPECTED_IP:-31.97.142.91}"

echo "──────────────────────────────────────────────────────────────────────"
echo " FINALIZE: $DOMAIN"
echo "──────────────────────────────────────────────────────────────────────"

# ── Step 1: DNS check ──────────────────────────────────────────────────────────
echo ""
echo "[1/5] Checking DNS for $DOMAIN..."
RESOLVED=""
for resolver in 1.1.1.1 8.8.8.8 9.9.9.9; do
    R=$(dig +short +time=3 +tries=1 "$DOMAIN" @"$resolver" 2>/dev/null | head -1)
    echo "    via $resolver: ${R:-(NXDOMAIN)}"
    [ -n "$R" ] && RESOLVED="$R"
done

if [ -z "$RESOLVED" ]; then
    echo ""
    echo "  ✗ DNS not propagated yet. Add this record at your DNS provider:"
    echo ""
    echo "      Type:  A"
    echo "      Name:  seo"
    echo "      Value: $EXPECTED_IP"
    echo "      TTL:   300"
    echo ""
    echo "  Then re-run this script."
    exit 1
fi

if [ "$RESOLVED" != "$EXPECTED_IP" ]; then
    echo ""
    echo "  ⚠ DNS resolves to $RESOLVED, expected $EXPECTED_IP."
    echo "    Continue anyway? [y/N]"
    read -r ANS
    [ "$ANS" = "y" ] || exit 1
fi

echo "  ✓ DNS resolves: $RESOLVED"

# ── Step 2: certbot ────────────────────────────────────────────────────────────
echo ""
echo "[2/5] Issuing Let's Encrypt certificate..."

if [ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
    echo "  ℹ Certificate already exists for $DOMAIN — skipping issuance."
else
    certbot --nginx \
        -d "$DOMAIN" \
        --email "$EMAIL" \
        --agree-tos \
        --no-eff-email \
        --redirect \
        --non-interactive
fi

# ── Step 3: nginx reload ───────────────────────────────────────────────────────
echo ""
echo "[3/5] Reloading nginx..."
nginx -t
systemctl reload nginx
echo "  ✓ nginx reloaded"

# ── Step 4: restart denzo ──────────────────────────────────────────────────────
echo ""
echo "[4/5] Restarting denzo-seo (to load OAUTH_REDIRECT_URI from .env)..."
systemctl restart denzo-seo
sleep 3
systemctl is-active --quiet denzo-seo || { echo "  ✗ denzo-seo failed to start"; journalctl -u denzo-seo -n 20 --no-pager; exit 1; }
echo "  ✓ denzo-seo running"

# ── Step 5: smoke test ────────────────────────────────────────────────────────
echo ""
echo "[5/5] Smoke-testing https://$DOMAIN/..."
sleep 1
HTTPS_CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://$DOMAIN/")
PRICING=$(curl -s -o /dev/null -w "%{http_code}" "https://$DOMAIN/pricing")
PRIVACY=$(curl -s -o /dev/null -w "%{http_code}" "https://$DOMAIN/privacy")
TERMS=$(curl -s -o /dev/null -w "%{http_code}" "https://$DOMAIN/terms")

echo "    /          → $HTTPS_CODE"
echo "    /pricing   → $PRICING"
echo "    /privacy   → $PRIVACY"
echo "    /terms     → $TERMS"

# Verify HTTP → HTTPS redirect
HTTP_REDIR=$(curl -s -o /dev/null -w "%{http_code} → %{redirect_url}" "http://$DOMAIN/")
echo "    HTTP → HTTPS: $HTTP_REDIR"

echo ""
echo "──────────────────────────────────────────────────────────────────────"
echo " ✓ DONE — $DOMAIN is live with SSL."
echo "──────────────────────────────────────────────────────────────────────"
echo ""
echo "Next steps:"
echo "  1. Visit https://$DOMAIN/ and log in."
echo "  2. Update Google Cloud OAuth Client → Authorized redirect URIs:"
echo "       https://$DOMAIN/oauth/google/callback"
echo "  3. Paste GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET into .env."
echo "  4. systemctl restart denzo-seo"
echo "  5. Try Connect Google Business Profile from any client's settings."

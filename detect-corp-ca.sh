#!/usr/bin/env bash
# detect-corp-ca.sh — detect a corporate TLS-intercepting (MITM) proxy and STAGE its
# root CA for opt-in trust. It never enables trust by itself: review the printed SHA-256
# fingerprint with your IT department, then set CORP_CA_AUTO_TRUST=1 in .env.
#
# Usage: ./detect-corp-ca.sh [probe-host] [port]   (default: huggingface.co 443)
set -euo pipefail
cd "$(dirname "$0")"

PROBE="${1:-huggingface.co}"
PORT="${2:-443}"
DEST="certs/extra-ca"
mkdir -p "$DEST"

if ! command -v openssl >/dev/null 2>&1; then
    echo "ERROR: openssl not found. Install openssl, or stage the CA manually at $DEST/<name>.crt (PEM)." >&2
    exit 1
fi

echo "Probing TLS to ${PROBE}:${PORT} ..."
chain="$(printf '' | openssl s_client -connect "${PROBE}:${PORT}" -servername "${PROBE}" -showcerts 2>/dev/null || true)"
if [ -z "$chain" ]; then
    echo "ERROR: could not establish a TLS connection to ${PROBE}:${PORT} (check connectivity/proxy)." >&2
    exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
printf '%s' "$chain" | awk -v d="$tmp" '/-----BEGIN CERTIFICATE-----/{c++} c{print > (d"/cc-"c".pem")}'
root="$(ls -1 "$tmp"/cc-*.pem 2>/dev/null | tail -1 || true)"
if [ -z "$root" ]; then
    echo "ERROR: no certificate could be extracted from the chain." >&2
    exit 1
fi

subject="$(openssl x509 -in "$root" -noout -subject 2>/dev/null || true)"
# Heuristic: a well-known PUBLIC root => no interception worth staging.
if printf '%s' "$subject" | grep -qiE "DigiCert|Baltimore|ISRG|Let'?s Encrypt|Global ?Sign|Sectigo|USERTrust|Amazon|Google Trust|Microsoft .*Root|Entrust|GoDaddy|Comodo|Starfield|Certum|Thawte|VeriSign"; then
    echo "Top-of-chain root looks public:"
    echo "  $subject"
    echo "No corporate interception detected; nothing staged."
    exit 0
fi

fp="$(openssl x509 -in "$root" -noout -fingerprint -sha256 | sed 's/.*=//; s/://g')"
out="${DEST}/corp-root-${fp:0:16}.crt"
cp "$root" "$out"

echo
echo "Corporate root staged: $out"
echo "  subject: $subject"
echo "  SHA-256: $fp"
echo
echo "NEXT: VERIFY this SHA-256 with your IT department, then set CORP_CA_AUTO_TRUST=1 in .env"
echo "      and rebuild/start (./build-gigaam.sh && ./start.sh). Verification stays ON — the CA is ADDED, not bypassed."

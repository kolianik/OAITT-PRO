#!/bin/sh

CERT_DIR="/etc/letsencrypt/live/transcribe"
mkdir -p "$CERT_DIR"

if [ ! -f "$CERT_DIR/fullchain.pem" ]; then
    echo "SSL Certificate not found. Generating self-signed placeholder certificate..."
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$CERT_DIR/privkey.pem" \
        -out "$CERT_DIR/fullchain.pem" \
        -subj "/CN=localhost"
    echo "Placeholder certificate generated."
fi

# Run Nginx in foreground
exec nginx -g "daemon off;"

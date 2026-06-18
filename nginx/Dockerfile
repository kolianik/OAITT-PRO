# Master process starts as root to bind ports 80/443; workers run as nginx (see nginx.conf).
FROM nginx:alpine

# Proxy (S2) for apk (reads lowercase proxy vars); empty defaults = no change (S1).
# nginx is EXEMPT from corporate-CA injection (S3): its build context is ./nginx (cannot see
# certs/), Alpine apk uses HTTP mirrors, and ACME/TLS runs in the separate certbot image.
# CORP_CA_AUTO_TRUST is declared only to consume the build-arg without a warning.
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG NO_PROXY=""
ARG http_proxy=""
ARG https_proxy=""
ARG no_proxy=""
ARG CORP_CA_AUTO_TRUST=0
ENV http_proxy=$http_proxy https_proxy=$https_proxy no_proxy=$no_proxy \
    HTTP_PROXY=$HTTP_PROXY HTTPS_PROXY=$HTTPS_PROXY NO_PROXY=$NO_PROXY

RUN apk add --no-cache openssl

COPY nginx.conf /etc/nginx/nginx.conf
COPY entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]

FROM python:3.11-slim

WORKDIR /app

RUN groupadd -r -g 1001 oaitt && useradd -r -u 1001 -g oaitt oaitt

# Network scenarios: proxy (S2) + corporate MITM CA (S3). Empty/zero defaults = no change (S1).
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG NO_PROXY=""
ARG http_proxy=""
ARG https_proxy=""
ARG no_proxy=""
ENV HTTP_PROXY=$HTTP_PROXY HTTPS_PROXY=$HTTPS_PROXY NO_PROXY=$NO_PROXY \
    http_proxy=$http_proxy https_proxy=$https_proxy no_proxy=$no_proxy
ARG CORP_CA_AUTO_TRUST=0
COPY certs/extra-ca/ /usr/local/share/ca-certificates/extra/
RUN if [ "$CORP_CA_AUTO_TRUST" = "1" ] && ls /usr/local/share/ca-certificates/extra/*.crt >/dev/null 2>&1; then \
        ( command -v update-ca-certificates >/dev/null 2>&1 || \
          ( apt-get update && apt-get install -y --no-install-recommends ca-certificates ) ) && \
        update-ca-certificates; \
    else \
        rm -f /usr/local/share/ca-certificates/extra/*.crt 2>/dev/null || true; \
    fi
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    GIT_SSL_CAINFO=/etc/ssl/certs/ca-certificates.crt

COPY gateway/requirements.txt .
RUN pip install --no-cache-dir \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org \
    -r requirements.txt

COPY shared/ /app/shared/
COPY gateway/ /app/

RUN mkdir -p /shared_data && chown -R oaitt:oaitt /app /shared_data

USER oaitt

EXPOSE 9000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]

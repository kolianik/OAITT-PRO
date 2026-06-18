# Corporate CA staging (`certs/extra-ca/`)

This directory holds **corporate root CA certificates** for networks that run a transparent
TLS-intercepting proxy (Scenario S3 — see [INSTALL.md §6](../INSTALL.md#6-network-scenarios--proxies-s1--s2--s3)
and [SECURITY.md](../SECURITY.md#corporate-tls-interception-mitm)).

## How it works

1. Run the detector, which probes a real download endpoint and, **only if TLS is intercepted**,
   stages the corporate root here:

   ```bash
   ./detect-corp-ca.sh          # Windows: .\detect-corp-ca.ps1
   ```

   It writes `certs/extra-ca/corp-root-<fingerprint>.crt` and prints the SHA-256 fingerprint.

2. **Verify** the printed fingerprint with your IT department.

3. Set `CORP_CA_AUTO_TRUST=1` in `.env`, then `./build-gigaam.sh && ./start.sh`.

## Rules

- Files **must** use the `.crt` extension and be PEM-encoded — Debian/Alpine
  `update-ca-certificates` ingests `*.crt` and silently ignores `*.pem`.
- The directory is committed (via `.gitkeep`) so the Docker build context always contains it;
  otherwise `COPY certs/extra-ca/ ...` fails the image build for everyone.
- The `.crt` files themselves do **not** need to be committed — the Docker build reads the working
  tree, so staging them locally is enough. A corporate root may reveal your employer, so committing
  is optional and your call.
- Trust is **fail-closed**: with `CORP_CA_AUTO_TRUST=0` (default) nothing here is trusted.

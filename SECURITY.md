# Security Policy

## Supported Versions

| Version | Supported |
|:---|:---|
| 1.3.x   | Yes       |

## Reporting a Vulnerability

If you discover a security issue, please **do not** open a public GitHub issue with exploit details. Contact the maintainers privately with a description, impact, and reproduction steps.

## Secrets You Must Never Commit

- `.env` and any file matching `.env.*` (except `.env.example`)
- `HF_TOKEN`, `ADMIN_KEY`, `POSTGRES_PASSWORD`, `CLOUDFLARE_API_TOKEN`
- SSH passwords, private keys (`*.pem`, `*.key`)
- Production API keys, client tokens, or billing credentials
- Server logs, test run artifacts (`test_results/`), or audio samples that may contain PII (`tets_data/`)

These paths are listed in [`.gitignore`](.gitignore). Run `git check-ignore -v <file>` before adding new files.

## Production Hardening Checklist

Before exposing OAITT-PRO to the internet:

1. **Copy and customize** `.env` from `.env.example`; use long random values for `ADMIN_KEY` and `POSTGRES_PASSWORD`.
2. **Replace the seeded dev client** — on first boot the gateway may insert `default-client-key` if the `clients` table is empty. Create your own API keys in PostgreSQL and deactivate or rotate the default key.
3. **Restrict network access** — firewall the upload port (`PROXY_PORT_HTTP` in `.env`) and admin routes; use TLS everywhere.
4. **Hugging Face** — use a read-only token; accept Pyannote model license terms on huggingface.co.
5. **Do not run** private deploy/diagnostic scripts from the repository root in CI or public forks; keep them outside git (see `.gitignore`).

## Credential Rotation (Required If Secrets Were Exposed)

If passwords or API keys ever appeared in chat logs, backups, or an accidental commit:

| Credential | Action |
|:---|:---|
| SSH server password | Change on the host; prefer SSH key authentication and disable password login |
| `ADMIN_KEY` | Generate a new value in `.env`, restart `gateway-orchestrator` |
| Client API keys | Rotate keys in the `clients` table; revoke compromised keys |
| `HF_TOKEN` | Revoke and reissue at Hugging Face settings |
| `CLOUDFLARE_API_TOKEN` | Rotate in Cloudflare dashboard |
| `POSTGRES_PASSWORD` | Update `.env` and Postgres user password; restart stack |

After rotation, verify with `curl` against `/health` and a test transcription using the new client key.

## Git History

If sensitive files were ever committed, remove them from history with [git-filter-repo](https://github.com/newren/git-filter-repo) or BFG Repo-Cleaner, then force-push only after coordinating with all clones. Rotate all exposed credentials regardless of history cleanup.

## Default Development Values

`docker-compose.yml` and `gateway/db.py` use development defaults (`secure_pass`, `admin-secret-key`, `default-client-key`). These are **not** safe for production. Always override via `.env`.

## Docker Network Segmentation

Production Compose uses three networks:

| Network | Internal | Members |
|:---|:---|:---|
| `edge_net` | No | `front-proxy` (published 80/443) |
| `backend_net` | Yes | `front-proxy`, `gateway-orchestrator`, `postgres` |
| `inference_net` | Yes | `gateway-orchestrator`, `whisperx-service`, `gigaam-service` |

External clients reach only nginx. Nginx talks only to the gateway. Inference services are reachable only from the gateway on `inference_net`. Cleartext HTTP between containers is restricted to these isolated networks; TLS terminates at nginx.

Set `INTERNAL_SERVICE_TOKEN` in `.env` so gateway-to-inference calls require a shared header. Generate with `openssl rand -hex 32`.

To expose the gateway directly on the host for debugging, use `docker compose -f docker-compose.yml -f docker-compose.debug.yml up`.

## Corporate TLS interception (MITM)

On networks with a transparent proxy that re-signs TLS (Scenario S3 in [INSTALL.md](INSTALL.md#6-network-scenarios--proxies-s1--s2--s3)),
OAITT-PRO can trust the corporate root CA so downloads succeed **with verification still enabled** — it
*adds* a trusted root rather than disabling validation (unlike `pip --trusted-host` or
`PREPARE_INSECURE_SSL=1`, which switch checking off).

**This is a deliberate trust decision, not an automatic one.** Whoever holds the intercepting CA's
private key can read and rewrite all TLS traffic from these containers — model weights, the `HF_TOKEN`
in `Authorization` headers, and the Cloudflare API token. Therefore trust is gated by **two factors**,
both required:

1. A staged CA file exists at `certs/extra-ca/*.crt` (produced by `detect-corp-ca.{sh,ps1}`, which only
   stages and prints the SHA-256 fingerprint — it never enables trust).
2. `CORP_CA_AUTO_TRUST=1` is set explicitly in `.env`.

Operator responsibility: **verify the printed fingerprint against the value your IT department
publishes before setting the flag.** With the flag left at `0` (default) no corporate CA is trusted —
fail-closed. The corporate `.crt` is build context, not a secret to embed in code; a corporate root may
nonetheless reveal your employer, so staging it locally (uncommitted) is sufficient and preferred.

The CA is consumed via the standard `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE` / `CURL_CA_BUNDLE` /
`GIT_SSL_CAINFO` env vars (image + runtime) and `update-ca-certificates` at build time, so apt, conda,
pip's non-PyPI hosts, git, HuggingFace, and the GigaAM Sber-CDN `urllib` path all validate against it.

## Logging Policy

- Do not log API tokens, `ADMIN_KEY`, `HF_TOKEN`, or webhook URLs with embedded credentials.
- Log webhook delivery by hostname only, not full URL query strings.
- Return generic error messages to API clients; keep stack traces and engine response bodies in server logs only.

## AppScreener False Positives

Mark the following as **rejected** when triaging static analysis:

| Finding | Location | Rationale |
|:---|:---|:---|
| Hard-coded key | `.env.example` placeholders | Deployment template only; real secrets live in `.env` (gitignored) |
| HTTP cleartext | `docker-compose.yml`, `nginx.conf` upstream | Internal Docker networks only; external traffic uses HTTPS via nginx |
| SSRF | `nginx/nginx.conf` `proxy_pass` | Fixed upstream hostname, not user-controlled URL |
| Path traversal | `tests/test_*.py` `sys.path.insert` | Test harness only |
| Path traversal | `os.remove` on app-generated temp paths | Paths created by the service, not raw user input |
| Information disclosure | Operational `logger.info` | Standard observability; no secrets or PII in messages |

Real issues addressed in code: webhook SSRF (HTTPS-only validation), shared-path validation, non-root containers, SSL verification in `prepare.py`, sanitized API error responses.

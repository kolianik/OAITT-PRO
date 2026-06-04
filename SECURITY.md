# Security Policy

## Supported Versions

| Version | Supported |
|:---|:---|
| 1.1.x   | Yes       |

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
3. **Restrict network access** — firewall upload port (`3000`) and admin routes; use TLS everywhere.
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

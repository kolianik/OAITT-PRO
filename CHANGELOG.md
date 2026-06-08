# Changelog

All notable changes to this project are documented here. Versions align with the gateway OpenAPI version and API specification headers.

## [1.1.1] - 2026-06-08

### Security

- Docker network micro-segmentation: `edge_net`, `backend_net`, `inference_net`; gateway host port removed (optional `docker-compose.debug.yml`).
- HTTPS-only webhook validation with private/metadata IP blocking; `follow_redirects=False` on webhook delivery.
- Shared-path validation for uploads, cleanup, and inference `/local` endpoints.
- `INTERNAL_SERVICE_TOKEN` header between gateway and inference services.
- Non-root container users (UID 1001) for gateway, gigaam, and whisperx.
- `prepare.py`: SSL verification enabled by default; `PREPARE_INSECURE_SSL=1` for dev-only bypass.
- Sanitized API error responses; webhook URLs logged by hostname only.

### Added

- [INSTALL.md](INSTALL.md): full Ubuntu 24.04 LTS installation guide (GPU driver, Docker, toolkit, `prepare.sh`, WhisperX cache, smoke tests).
- `.env` deployment contract: `API_PUBLIC_HOST`, `API_UPLOAD_HOST`, `WHISPERX_BATCH_SIZE`, `INTERNAL_SERVICE_TOKEN`.
- `WHISPERX_BATCH_SIZE` environment variable wired into WhisperX service and Docker Compose.
- `shared/security.py`, `gateway/security.py`, `tests/test_security.py`, `docker-compose.debug.yml`.

### Changed

- Public API documentation uses `.env` placeholders instead of hardcoded ports (e.g. `:3000`) and production hostnames.
- CUDA quality policy: `WHISPERX_COMPUTE_TYPE=float16` only on GPU; unsupported quantizations rejected at WhisperX startup.

### Documentation

- [README.md](README.md), [TROUBLESHOOTING.md](TROUBLESHOOTING.md), [agents.md](agents.md), [API_transcriptions.md](API_transcriptions.md), [API_health.md](API_health.md), [SECURITY.md](SECURITY.md): aligned with `.env` deployment model; OOM guidance uses batch size only (no int8).
- [SECURITY.md](SECURITY.md): network segmentation, logging policy, AppScreener false-positive triage.
- [INSTALL.md](INSTALL.md) §7, [API_transcriptions.md](API_transcriptions.md): webhook HTTPS contract.

## [1.1.0] - 2026-06-04

First public release.

### Added

- Asynchronous transcription API (`POST /v1/audio/transcriptions/async`, `GET /v1/audio/transcriptions/status/{job_id}`) with multiple output formats (`json`, `text`, `srt`, `vtt`, `tsv`).
- Dual ASR engines: WhisperX (`bzikst/faster-whisper-large-v3-russian`) and GigaAM (`v3_e2e_rnnt`).
- Pyannote v4 speaker diarization for both engines.
- Dynamic GPU VRAM management with exclusive model loading and `/unload` coordination.
- PostgreSQL 17-backed multi-client API keys, job queue, and transcription logs.
- Analytics endpoint `GET /api/v1/analytics/summary` with date filters and RUB cost estimates.
- Admin pricing API (`POST/GET /api/v1/admin/pricing`, history) and `GET /api/v1/admin/analytics/by-client`.
- Health endpoint `GET /health` for gateway, database, and inference services.
- Docker Compose stack: gateway, whisperx, gigaam, nginx, postgres, certbot.
- Unit tests for gateway auth/flow and analytics cost calculation.

### Security

- `.env.example` template for secrets; expanded `.gitignore` for env files, test artifacts, and private ops tooling.
- See [SECURITY.md](SECURITY.md) for production hardening.

### Documentation

- API references: `API_transcriptions.md`, `API_analytics.md`, `API_admin_*.md`, `API_health.md`, `agents.md`, `TROUBLESHOOTING.md`.

## [1.0.0] - Internal baseline

Pre-public WhisperX/GigaAM inference services and initial gateway (superseded by 1.1.0 public contract).

# Changelog

All notable changes to this project are documented here. Versions align with the gateway OpenAPI version and API specification headers.

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

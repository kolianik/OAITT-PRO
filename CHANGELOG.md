# Changelog

All notable changes to this project are documented here. Versions align with the gateway OpenAPI version and API specification headers.

## [1.3.0] - 2026-06-18

### Added

- **FEATURE — automatic clean-install handling for three network scenarios (direct / system-proxy / corporate-MITM).** A new shared preflight `scripts/netprep.{sh,ps1}` is sourced by `start.{sh,ps1}` and `build-gigaam.{sh,ps1}`, replacing the old inline loopback-strip blocks with a **discover → probe → decide** pipeline that exports a single canonical proxy contract (`HTTP_PROXY`/`HTTPS_PROXY` + lowercase, a complete `NO_PROXY`, and a diagnostic `OAITT_PROXY_MODE ∈ {direct,passthrough,translated,none}`):
  - **S1 (direct internet):** zero-config; even when a proxy is configured, a proxy-bypassed reachability probe to the real download hosts (`registry-1.docker.io`, `pypi.org`, `huggingface.co`) **disables proxying** when the internet is reachable directly.
  - **S2 (system proxy only):** the system proxy is auto-discovered — including the **Windows WinINET registry / WinHTTP**, not just env vars — and a **loopback** proxy (`http://127.0.0.1:<port>`) is auto-translated to `http://host.docker.internal:<port>` (`OAITT_PROXY_MODE=translated`) so containers/BuildKit can reach it; non-loopback proxies pass through. Proxy now reaches **build** steps too: all four Dockerfiles take `ARG HTTP_PROXY/HTTPS_PROXY/NO_PROXY` (+lowercase) and `docker-compose.yml` passes them as `build.args` with `build.extra_hosts: host.docker.internal:host-gateway` on every building service. `NO_PROXY` now excludes every internal service name + `host.docker.internal` + `::1` (was `localhost,127.0.0.1,gigaam-service`).
  - **S3 (corporate transparent MITM with certificate substitution):** new `detect-corp-ca.{sh,ps1}` probes a real download endpoint and, only when TLS is intercepted, stages the corporate root at `certs/extra-ca/corp-root-<fp>.crt` and prints its SHA-256 fingerprint. A **two-factor gate** (`CORP_CA_AUTO_TRUST=1` **and** a staged `*.crt`) then injects the CA into image builds (apt/conda/pip non-PyPI hosts/git/DeepFilterNet bake via `update-ca-certificates`), container runtime (HuggingFace + the GigaAM Sber-CDN `urllib` path via `REQUESTS_CA_BUNDLE`/`SSL_CERT_FILE`), and host-side `prepare.py`. Verification stays **on** (the CA is added, not bypassed); default is fail-closed.
- **`docker-compose.bootstrap.yml`** — opt-in first-run egress override that temporarily attaches `gigaam-service` to a non-internal `bootstrap_net` so the initial model download succeeds under S1 (where `inference_net`'s `internal: true` otherwise blocks egress), without weakening the production segmentation.
- Tests: `tests/test_netprep_proxy.py`, `tests/test_compose_proxy_ca.py`, `tests/test_corp_ca_injection.py`; `tests/test_gigaam_docs_consistency.py` extended for the new doc invariants.

### Changed

- `prepare.py`: corporate-CA-aware via `REQUESTS_CA_BUNDLE`/`SSL_CERT_FILE` (verification stays on); `PREPARE_INSECURE_SSL=1` demoted to a dev-only last resort.
- `.gitignore`: un-ignores `certs/extra-ca/` (committed `.gitkeep`) so the Docker build context always contains the CA staging dir.
- Docs: INSTALL.md (new §6 "Network scenarios & proxies"), TROUBLESHOOTING.md (Error 2 reworked to the corporate-CA flow; Error 2c proxy/translation rows), README.md, SECURITY.md, `.env.example`.

### Security

- Corporate MITM CA trust is gated and fail-closed (two-factor: staged cert + explicit `CORP_CA_AUTO_TRUST=1`), with mandatory fingerprint verification — documented in SECURITY.md. The corporate `.crt` need not be committed (build reads the working tree).

## [1.2.1] - 2026-06-17

### Fixed

- **GigaAM alignment frame→word mapping (word scrambling + mass word loss).** `align_chunk` selected each word's frames with `cs <= label < ce`, comparing `torchaudio.forced_align` **token-IDs** against **character positions** — two unrelated number spaces. Because the alignment model is char-level (~40 token-IDs) while char positions grow with chunk length, only roughly the first 6–8 words of each chunk could match any frames; every later word matched zero frames and was silently dropped (`if not frames: continue`), and the surviving words got garbage timecodes that `sort(key=start)` then reordered. Net effect on a 35-min sample: ~⅓ of words emitted, in scrambled order. Frames are now mapped to words **by target position** via `torchaudio.functional.merge_tokens` (one `TokenSpan` per target token, in order); the per-`start` sort is removed (monotonic alignment already yields time order). Alignment now **preserves all ASR text**: out-of-vocabulary words (`B2B-`, `ЦПР`, `ЕКМП`) are time-interpolated between aligned neighbours, and if `forced_align`/`merge_tokens` cannot run (e.g. `target_len > frame_len`) the chunk's words are distributed evenly instead of dropped.
- Removed the word-level `HALLUCINATION_THRESHOLD` drop from alignment: low-confidence words are tagged `low_conf` and emitted (anti-hallucination filtering remains the gateway's opt-in `min_avg_logprob`/`max_chars_per_second`), so word scores now reflect real CTC confidence.
- **Docs (BUG-002b follow-through): start/restart instructions now route through `start.ps1`/`start.sh`, not a bare `docker compose up -d`.** INSTALL.md §3.2/§5, README.md, DEPLOYMENT_INFO.md, and TROUBLESHOOTING.md (incl. the misleading "ensure internet or `HTTP_PROXY`" bootstrap row) told users to run the bare command. `docker-compose.yml` passes the host shell's `HTTP_PROXY`/`HTTPS_PROXY` into `gigaam-service`, so on a host with a **loopback** proxy (`http://127.0.0.1:…`) the cold-start bootstrap stranded with `[Errno 111] Connection refused` (the proxy is unreachable from inside the container). The start scripts strip loopback proxy vars before invoking Compose; docs now point at them and recommend `http://host.docker.internal:<port>` for routing through a host proxy. New guard test `tests/test_gigaam_docs_consistency.py`.

## [1.2.0] - 2026-06-17

### Added

- `scripts/gigaam_smoke.py`: committed end-to-end GigaAM verification (submits a `model=gigaam` job, polls to completion, validates result shape vs `API_transcriptions.md`); validator unit-tested in `tests/test_gigaam_smoke.py`. Concurrency and diarization contracts covered by `tests/test_gigaam_concurrency.py` and `tests/test_gigaam_diarization.py`.

- GigaAM volume-based model cache: `gigaam_model_cache` holds ONNX, HF alignment, Pyannote; bootstrap on first container start via `bootstrap_models.py` + FastAPI lifespan.
- `GET /health` returns 503 with Russian `message` while bootstrapping; `healthcheck.py` for Docker Health.Log; `first_install` flag for empty volume.
- Env: `GIGAAM_OFFLINE_MODE`, `GIGAAM_PREFETCH_DIARIZATION`, `GIGAAM_WEIGHTS_DIR`; proxy vars on `gigaam-service`.
- Env `GIGAAM_DIARIZE_BATCH_SIZE` (default `8`): caps Pyannote segmentation peak VRAM during diarization (`pipeline._segmentation.batch_size`), enabling `diarize=true` on long audio on RTX 3060 6 GB. Lower = less VRAM, slightly slower.
- `scripts/seed_gigaam_cache.sh` for export/import of model cache tar (air-gap).
- Tests: `tests/test_gigaam_bootstrap.py`.
- Env `GIGAAM_DEEPFILTER_DIR` (default `/app/data/deepfilter`): explicit volume path for DeepFilterNet3 weights; overridable in `.env`.

### Changed

- Removed orphaned `gigaam/run_download_models.sh` (legacy build-time HF download via build secret; superseded by runtime bootstrap and `scripts/seed_gigaam_cache.sh`).

- Docs corrected: GigaAM ONNX runtime default is **FP32** (full precision); FP16 is opt-in via `GIGAAM_ONNX_FP16=true`. (`agents.md` §2.5 previously stated FP16; the container exports FP32 by default.)

- GigaAM Docker image: slim build (deps only); removed bake-in of `data/gigaam`, ONNX export, and HF downloads at build time.
- `GIGAAM_ONNX_DIR` default `/app/data/gigaam_onnx` (volume); runtime `HF_HUB_OFFLINE=1` after successful bootstrap.
- Pipeline: `local_files_only` for Wav2Vec2 alignment; offline-aware Pyannote load.
- `build-gigaam.ps1` / `build-gigaam.sh`: no `sync_build_secrets` (HF_TOKEN runtime-only).
- `prepare.py`: optional host prefetch for seed workflows.

### Fixed

- **BUG-004 — DeepFilterNet3 denoise now uses windowed processing; no VRAM OOM on long audio.** Previously `denoise_audio()` passed the entire file as a single tensor to `enhance()` — a 35-min file required ~14.66 GB VRAM, causing `torch.OutOfMemoryError` (tried to allocate 4.80 GiB in `F.conv2d`) on 10 GB cards. Denoise now slides overlapping windows (default 30 s, 2 s overlap) over the 48 kHz waveform, processes each window individually on GPU, and stitches results with a linear crossfade. Peak VRAM is bounded to ~1 GB independent of audio length. On per-window OOM the window is halved and retried; if the minimum window still OOMs, that window falls back to CPU. Two new env knobs: `GIGAAM_DENOISE_CHUNK_SEC` (default 30) and `GIGAAM_DENOISE_OVERLAP_SEC` (default 2).

- **BUG-002b — `build-gigaam.ps1`, `build-gigaam.sh`, `start.ps1`, `start.sh`: loopback proxy vars (`HTTP_PROXY=http://127.0.0.1:…`) are now cleared before invoking Docker.** Docker BuildKit inherits shell proxy env vars; a host-local proxy (`127.0.0.1`) is never reachable by BuildKit or by containers, causing build failures and bootstrap download failures with `[Errno 111] Connection refused`. Non-loopback proxies are left intact.

- **BUG-002 — DeepFilterNet3 weights now persisted on volume and sourced offline from the image (was: HTTP 500 on first transcription / silent denoise disable on every restart).** Previously `verify_deepfilter()` checked only wheel importability, weights landed in the container writable layer (off-volume), and were fetched at request time via a host proxy unreachable from inside the container (`127.0.0.1:…` → `[Errno 111]`). Bootstrap now runs a dedicated `download_deepfilter` step that persists weights into `GIGAAM_DEEPFILTER_DIR` (on the volume). At runtime `init_df()` receives `model_base_dir=<volume-path>` and never makes network requests. `/health` `cached_models` now reports `"deepfilter"` only when weights are actually present on the volume. If `download_deepfilter` fails (e.g. no network in `inference_net`), bootstrap continues with denoise gracefully disabled for that session — weights remain absent from the volume and the step will retry on the next container start.

- **BUG-003 — DeepFilterNet3 weights now baked into the Docker image to eliminate runtime GitHub dependency.** `gigaam-service` runs on `inference_net` (`internal: true`) which has no external DNS, so any runtime attempt to download from `github.com` fails with `Temporary failure in name resolution`. Weights (~50–100 MB) are now downloaded once at `docker build` time (where the build host reaches github) into `/opt/deepfilter/` (outside the volume mount). Bootstrap seeds them from `/opt/deepfilter/` to the volume offline via `seed_deepfilter_from_baked()` — no network needed. The network `download_deepfilter` path remains as a fallback (e.g. for users who build on an isolated machine and use `GIGAAM_BAKED_DEEPFILTER_DIR` to point at a different source). New env: `GIGAAM_BAKED_DEEPFILTER_DIR` (default `/opt/deepfilter`). Additionally, `denoise_audio()` now calls `torch.cuda.synchronize()` immediately after `model.to(device)` to ensure the CUDA context is fully initialised before the first forward pass, eliminating the `cudaErrorNotReady` (code 600) race on cold container starts and after container recreation. A two-attempt retry loop (2 s delay + re-sync between attempts) wraps `enhance()` as defence-in-depth for the residual window after WSL2 restarts. The model and df_state handles are now freed in a `try/finally` block wrapping the entire model-using section: previously `flush_memory()` was only reachable on the success path, so a crash inside `enhance()` left ~9.6 GB stranded in the CUDA allocator's pool; on the next job the stale handles triggered `!handles_.at(i) INTERNAL ASSERT FAILED` at `CUDACachingAllocator.cpp:430`. `docker-compose.yml` now sets `CUDA_VISIBLE_DEVICES` explicitly for both `gigaam-service` (RTX 3080 UUID) and `whisperx-service` (RTX 3050 UUID): Docker's `device_ids`/`count: all` reservations do not inject `CUDA_VISIBLE_DEVICES` under WSL2, leaving both containers seeing `cuda:0 = RTX 3080` with no hardware-level isolation. After four incidents, the confirmed root cause is a race between `df`'s lazy CUDA initialisation and the `expandable_segments` allocator: `df.init_df()` moves model weights to GPU but the allocator's segment-handle map (`handles_`) is not yet fully populated when `enhance()` subsequently allocates feature tensors — `spec.to(device)` arrives before the async segment registration completes and raises either `cudaErrorNotReady` or `!handles_.at(i)`. `denoise_audio()` now executes a mandatory CUDA pre-warmup (allocate a 1-element tensor, run a kernel, synchronize, free) before calling `df.init_df()`, forcing the context and allocator into a stable state. The two-attempt retry loop has been removed: it re-entered an already-corrupted context and converted `cudaErrorNotReady` into the harder `!handles_.at(i)` assert. `PYTORCH_CUDA_ALLOC_CONF` is renamed to `PYTORCH_ALLOC_CONF` (the PyTorch 2.9 name) in both `Dockerfile` and `docker-compose.yml`.

- Test reliability: `tests/test_gigaam_vad.py::test_force_split_point_in_window` was flaky (unseeded random audio + a lower bound that did not match the actual `[min(20s, dur/2), 24.9s]` search window); now seeded and deterministic, with the `_force_split_point` docstring corrected.

- GigaAM concurrency: transcription runs via `asyncio.to_thread` under a process-wide single-flight `asyncio.Lock`. `GET /health` now stays responsive while a job is in flight (Docker healthcheck no longer flaps during long files), and the shared per-stage model singletons are never used by two requests at once.

- GigaAM preprocess diagnosability: ffmpeg failures now log `stderr` at `ERROR` and raise a clear `RuntimeError` with the stderr tail (was an opaque `CalledProcessError`); orchestrator logs stage boundaries.

- GigaAM bootstrap resilience: each model download step retries transient network failures with exponential backoff (`GIGAAM_BOOTSTRAP_RETRIES`, `GIGAAM_BOOTSTRAP_RETRY_DELAY`) before the attempt ends in `failed`, so a single first-start network blip no longer strands the container.

- GigaAM diarization fail-fast: `diarize=true` without `HF_TOKEN` (or without a prefetched Pyannote) now returns a clear **HTTP 400** instead of an opaque 500. `/health` exposes `diarization_available`; bootstrap logs a `WARNING` and stays `healthy` for plain transcription when diarization is unconfigured (`GIGAAM_PREFETCH_DIARIZATION=true` + empty `HF_TOKEN`).

- GigaAM volume bootstrap: `entrypoint.sh` chowns `/app/data` and `/shared_data` before dropping to `oaitt` (fixes `Permission denied` on fresh `gigaam_model_cache`).

- GigaAM denoise: call `init_df(default_model=...)` per DeepFilterNet 0.5.6 API (was invalid `model_name=` kwarg).

- `start.ps1`, `start.sh`: new canonical start scripts that replace bare `docker compose up -d`. Both probe the Docker daemon first and abort with a clear "daemon unreachable" message (instead of a cryptic HTTP 500) when the WSL2 backend is down; `DOCKER_API_VERSION` is pinned only when the CLI API version is strictly newer than the server's (defensive guard, inactive on current Docker Engine 29.5.3 where both sides report 1.54).
- `build-gigaam.ps1`, `build-gigaam.sh`: same daemon preflight + conditional `DOCKER_API_VERSION` pin added before building.
- TROUBLESHOOTING.md: documented the HTTP-500 / crashed-WSL2-backend failure mode (Error 1b) and clarified it is not an API-version incompatibility.

## [1.1.5] - 2026-06-09

### Added

- GigaAM 6-step pipeline: ffmpeg preprocess, optional DeepFilterNet3 denoise (split-path), Silero VAD chunking (≤24.9 s + Force Split), ONNX FP16 ASR, chunked Wav2Vec2 forced alignment with hallucination filter, Pyannote diarization on original audio, IoW backchannel speaker merge.
- `gigaam/pipeline/` modules, `export_onnx.py`, `download_models.py`.
- Env vars: `GIGAAM_ALIGN_MODEL`, `GIGAAM_BATCH_SIZE`, `GIGAAM_DENOISE`, `GIGAAM_DENOISE_MODEL`, `GIGAAM_DENOISE_DEVICE`, `GIGAAM_ONNX_DIR`.
- Unit tests: `test_gigaam_*`, gateway `model=gigaam` routing.

### Changed

- GigaAM ASR: PyTorch `load_model` replaced with ONNX Runtime GPU (`/models/gigaam_onnx`).
- `transformers` bumped to **5.x** (compatible with `huggingface-hub==1.18.0` and `pyannote.audio==4.0.4`; replaces locked `4.40.0`).
- Docker build: ONNX fp32 export + fp16 convert on CPU; HF models via BuildKit secret (`HF_TOKEN` in `docker-compose.yml`); `HF_HUB_OFFLINE=1` at runtime.
- Docker build: staged pip install (`install_deps.sh`) to avoid `resolution-too-deep`; `onnxconverter-common==1.15.0` for protobuf compatibility with `onnxruntime-gpu`.
- Docker build: HF_TOKEN via `secrets/hf_token` + `scripts/sync_build_secrets.py` (Compose build secrets do not reliably read `.env`); `build-gigaam.ps1` / `build-gigaam.sh` helpers.

### Documentation

- [agents.md](agents.md) §2.5, [API_transcriptions.md](API_transcriptions.md), [INSTALL.md](INSTALL.md), [README.md](README.md), [TROUBLESHOOTING.md](TROUBLESHOOTING.md), [.env.example](.env.example).

## [1.1.4] - 2026-06-09

### Changed

- WhisperX alignment model default: `jonatasgrosman/wav2vec2-xls-r-1b-russian` (replaces WhisperX built-in `wav2vec2-large-xlsr-53-russian`).
- New `WHISPERX_ALIGN_MODEL` environment variable; alignment weights load from `/app/data` with `model_cache_only=True`.
- `whisperx/download_models.py` copied into the Docker image; pre-downloads ASR and alignment models.
- GigaAM: replaced static `mwader/static-ffmpeg` and Ubuntu 4.x `libav*` packages with conda-forge `ffmpeg>=7` (8.1.1 shared libs). Pinned `torchcodec==0.9.1`; added `nvidia-npp-cu12`; CUDA wheel via `download.pytorch.org/whl/cu128`; extended `LD_LIBRARY_PATH`.

### Documentation

- [README.md](README.md), [.env.example](.env.example), [agents.md](agents.md), [INSTALL.md](INSTALL.md), [TROUBLESHOOTING.md](TROUBLESHOOTING.md): `WHISPERX_ALIGN_MODEL`, cache/migration notes, VRAM guidance for 1B alignment model; GigaAM conda-forge FFmpeg and TorchCodec 0.9.1.

## [1.1.3] - 2026-06-08

### Changed

- GigaAM Docker image: base upgraded to `pytorch/pytorch:2.9.1-cuda12.8-cudnn9-runtime`; native `pyannote.audio==4.0.4` + `torchcodec==0.9.0` install (removed `--no-deps` workaround and `PYANNOTE_SKIP_DEPENDENCY_CHECK`).
- GigaAM: static `ffmpeg`/`ffprobe` 8.1.1 from `mwader/static-ffmpeg` (replaces `apt-get install ffmpeg`); removed unused `git` apt package. Ubuntu FFmpeg 4.x shared libs + `LD_LIBRARY_PATH` for `torchcodec`.
- Consolidated `gigaam/requirements.txt`; removed `requirements-app.txt` and `pyannote-deps.txt`.

### Documentation

- [INSTALL.md](INSTALL.md): GigaAM host driver **>= 570.26**, CUDA 12.8 toolkit smoke test.
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md): Error 3b for insufficient CUDA driver with GigaAM 12.8 runtime.
- [agents.md](agents.md): updated GigaAM base image and ffmpeg notes.

## [1.1.2] - 2026-06-08

### Changed

- GigaAM Docker image: base switched from `python:3.11-slim-bookworm` to `pytorch/pytorch:2.5.1-cuda12.1-cudnn9-runtime`; PyTorch wheels are no longer downloaded on each build. Added `gigaam/constraints.txt` to pin pre-installed `torch`/`torchaudio`; `pyannote.audio` is installed with `--no-deps` (declared `torch>=2.8` is bypassed via `PYANNOTE_SKIP_DEPENDENCY_CHECK=1`).

### Documentation

- [INSTALL.md](INSTALL.md), [TROUBLESHOOTING.md](TROUBLESHOOTING.md), [agents.md](agents.md): updated build notes for the new GigaAM base image.

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

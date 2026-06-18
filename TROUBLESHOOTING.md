# OAITT-PRO Installation & Troubleshooting Guide

This document provides detailed setup instructions, common errors, and professional troubleshooting procedures for running the OAITT-PRO high-performance transcription and diarization system on NVIDIA GPUs (e.g., RTX 3060 12 GB, RTX 3080 10 GB).

For a step-by-step Ubuntu 24.04 LTS install, see **[INSTALL.md](INSTALL.md)**. Client-facing hostnames and ports are defined only in [`.env.example`](.env.example).

---

## 🚀 1. Host System Requirements

Before running the containers with GPU support, ensure your host has the following prerequisites configured.

### 💻 A. Windows (Docker Desktop + WSL2)
1.  **NVIDIA Windows Driver:** Ensure you have the latest official game-ready or studio driver installed on Windows.
2.  **WSL2:** Verify your Docker Desktop is configured to use the **WSL2-based engine** (Settings -> General -> Use the WSL2 based engine).
3.  **CUDA Support in WSL2:** CUDA support is natively included inside WSL2 from recent Windows 10/11 updates. No additional CUDA toolkit installation is strictly required on the Windows host itself, as Docker containers carry their own CUDA runtimes.

### 🐧 B. Linux (NVIDIA Container Toolkit)
If deploying on a Linux server, you **must** install the `NVIDIA Container Toolkit` to allow Docker to access physical GPU devices.

**How to Install on Debian/Ubuntu:**
```bash
# 1. Configure the production repository:
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg \
  && curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

# 2. Update and install:
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# 3. Configure Docker to use the NVIDIA runtime:
sudo nvidia-container-toolkit daemon reload
sudo systemctl restart docker
```

---

## 🛑 2. Troubleshooting Common Deployment Errors

### ❌ Error 1: "could not select device driver with capabilities: [[gpu]]"
This occurs when you try to run `docker-compose up` but Docker cannot find or access the NVIDIA GPU driver.

*   **Cause:**
    *   The `NVIDIA Container Toolkit` is missing on your Linux host.
    *   Docker Desktop is not using the WSL2 engine, or the WSL2 subsystem lacks connection with the GPU.
*   **Solution:**
    *   **On Linux:** Follow the steps in Section 1.B above to install the toolkit and restart the Docker daemon.
    *   **On Windows:** Restart Docker Desktop. Run `nvidia-smi` inside your Windows command prompt and inside your WSL2 terminal (`wsl`) to confirm that your GPU is recognized.

---

### ❌ Error 1b: `docker` commands return HTTP 500 (crashed WSL2 backend)

**Symptom**: every Docker command — even `docker version` / `docker ps` — fails with:

```text
request returned 500 Internal Server Error for API route and version
.../v1.54/version, check if the server supports the requested API version
```

**This is NOT an API-version incompatibility.** The `/version` endpoint is Docker's version-negotiation handshake and responds even on version mismatches; a genuine too-new-client returns HTTP **400** ("Maximum supported API version is X.XX") and Docker auto-downgrades. A **500 means the Docker Desktop Linux backend crashed or isn't ready** — the "check if the server supports..." text is a generic suffix Docker appends to every 500.

*   **Recovery**:
    1.  Quit Docker Desktop completely (system tray → Quit Docker Desktop — not just close the window).
    2.  In a terminal: `wsl --shutdown` (clears the wedged WSL2 kernel instance).
    3.  Start Docker Desktop, wait for **"Engine running"**.
    4.  Verify: `docker version --format 'server={{.Server.APIVersion}}'` should print a value.

*   **If 500 persists after restart**: WSL2 ran out of disk or RAM — common after large GPU image builds (e.g. the 25 GB PyTorch CUDA base). Check `wsl --status`, free space on the WSL2 vhdx, and raise Docker Desktop resource limits (Settings → Resources).

*   **Prevention**: use `start.ps1` (Windows) or `start.sh` (Linux) instead of bare `docker compose up -d`. These scripts probe the daemon first and fail fast with a clear "daemon unreachable" message instead of letting Compose surface the raw 500.

---

### ❌ Error 2: "SSL: CERTIFICATE_VERIFY_FAILED" (corporate TLS interception — Scenario S3)

`apt-get`, `conda`, `git clone`, the build-time DeepFilterNet bake, or a runtime HuggingFace / Sber-CDN
download fails certificate verification. This is the signature of a **corporate transparent proxy that
re-signs TLS with its own root CA** (MITM) — the public base images do not trust that root.

*   **Cause:** A corporate proxy intercepts HTTPS and presents a substituted certificate chain whose root
    CA is not in the image/host trust store. (pip is partly shielded by its `--trusted-host` flags for
    PyPI/PyTorch, but apt, conda, git, and model downloads are not.)
*   **Solution — detect and trust the corporate CA (verification stays ON):**
    1.  Run the detector, which probes a real download endpoint and stages the intercepting root:
        ```bash
        ./detect-corp-ca.sh          # Windows: .\detect-corp-ca.ps1
        # -> certs/extra-ca/corp-root-<fp>.crt  + prints the SHA-256 fingerprint
        ```
    2.  **Verify the printed fingerprint** with your IT department (this is the security gate — never
        trust a proxy's certificate blindly).
    3.  Set `CORP_CA_AUTO_TRUST=1` in `.env`, then rebuild/start (`./build-gigaam.sh && ./start.sh`). The
        CA is injected into image builds, container runtime, and host `prepare.py`. With the flag at `0`
        (default) no corporate CA is trusted.
    *   **Docker daemon image pulls** need the CA at the host level too: on Docker Desktop the Windows
        Root store (often pushed by GPO) covers it; on Linux, `start.sh` prints the
        `update-ca-certificates` + `systemctl restart docker` command.
    *   `PREPARE_INSECURE_SSL=1` (disables verification entirely) remains a **dev-only** last resort —
        prefer the CA flow above. See [INSTALL.md §6](INSTALL.md#6-network-scenarios--proxies-s1--s2--s3)
        and [SECURITY.md](SECURITY.md#corporate-tls-interception-mitm).

---

### ❌ Error 2b: `resolution-too-deep` / `Dependency resolution exceeded maximum depth` (GigaAM build)

Pip fails while installing `gigaam-service` dependencies in one shot — usually backtracking across `deepfilternet`→`scipy`, `onnxconverter-common`→`protobuf`, and redundant `pyannote.*` pins.

*   **Cause:** A single `pip install -r requirements.txt` with PyTorch index as primary and many overlapping pins creates an exponentially deep resolver graph.
*   **Solution (built into current `gigaam/Dockerfile`):** staged install via `gigaam/install_deps.sh` and split requirement files (`requirements-web.txt`, `requirements-onnx.txt`, `requirements-audio.txt`, `requirements-ml.txt`). ONNX stack uses `onnxconverter-common==1.15.0` (not 1.14.0, which pins `protobuf==3.20.2` and conflicts with `onnxruntime-gpu`).
*   **Local dev:** run `./gigaam/install_deps.sh` from `/app` (or mirror the stages manually) instead of one flat `pip install -r gigaam/requirements.txt`.

### ❌ Error 2c: `gigaam-service` unhealthy / bootstrapping (GigaAM model cache)

On first `docker compose up`, `gigaam_model_cache` is created empty. The container bootstraps models into the volume (15–60+ minutes). `(health: starting)` or `(unhealthy)` with a Russian message in Health.Log is **normal** until bootstrap completes.

*   **Check progress:** `docker inspect --format='{{range .State.Health.Log}}{{.Output}}{{end}}' oaitt-gigaam`
*   **Wait:** healthcheck `start_period` is 3600s.

| Symptom | Cause | Action |
|---------|-------|--------|
| Long `unhealthy`, message mentions download step | Bootstrap in progress | Wait. `inference_net` is `internal: true`, so first-run downloads need egress: on direct internet bootstrap once via `docker compose -f docker-compose.yml -f docker-compose.bootstrap.yml up -d gigaam-service`, then `start.ps1`/`start.sh`. Behind a proxy just use the start scripts — `netprep` auto-translates a `127.0.0.1` proxy to `http://host.docker.internal:<port>` (`OAITT_PROXY_MODE=translated`); a raw `127.0.0.1` `HTTP_PROXY` is unreachable from the container |
| `CERTIFICATE_VERIFY_FAILED` during a download step | Corporate TLS interception (S3) | Run `detect-corp-ca.sh`, verify the fingerprint, set `CORP_CA_AUTO_TRUST=1`, recreate — see Error 2 |
| `failed` after transient network error | Retries (`GIGAAM_BOOTSTRAP_RETRIES`) exhausted | `docker compose restart gigaam-service` — bootstrap resumes idempotently from the manifest |
| `failed` + «volume пуст» + `GIGAAM_OFFLINE_MODE` | Air-gap without seed | Import cache tar (INSTALL.md §3.2) |
| `Permission denied: '/app/data/.gigaam'` on first start | Volume mounted as root, process was `oaitt` | Rebuild with current `entrypoint.sh` (chown before `runuser`); recreate container |
| `ConnectionError` to huggingface.co with full volume | Stale image without offline load | Rebuild; `GIGAAM_OFFLINE_MODE=true` after seed |
| `diarize=true` → HTTP 400 «diarization unavailable»; `/health` shows `diarization_available=false` | `HF_TOKEN` not configured / Pyannote not prefetched | Set `HF_TOKEN`, accept HF license, restart (service stays `healthy` for plain transcription) |

**Seed / export:** `scripts/seed_gigaam_cache.sh` or see INSTALL.md §3.2.

### ❌ Error 2d (legacy): `ERROR: HF_TOKEN required` during `gigaam-service` **build**

Build-time model bake-in was removed. `HF_TOKEN` in `.env` is for **runtime** Pyannote prefetch only. Rebuild with the current slim `gigaam/Dockerfile` — no build secret required.

### ❌ Error 2e: `No module named 'torchaudio.backend'` during DeepFilterNet (build bootstrap)

`deepfilternet==0.5.6` imports `torchaudio.backend.common.AudioMetaData`, removed in **torchaudio 2.9** (base image `pytorch/pytorch:2.9.1`).

*   **Symptom:** Build fails at `download_models.py` → `download_deepfilter()` after Pyannote/Silero succeed.
*   **Fix (built in):** `gigaam/torchaudio_compat.py` registers stubs before `from df.enhance import init_df` (build + runtime denoise).
*   **Verify:** build log shows `deepfilter_torchaudio_compat_applied`, `deepfilter_git_compat_applied`, and `DeepFilterNet cached.`

### ❌ Error 2f: `FileNotFoundError: ... 'git'` during DeepFilterNet `init_df()` (bootstrap)

`deepfilternet` logger calls `git rev-parse` via `df.utils.get_git_root()`; the PyTorch runtime image has no `git` binary.

*   **Symptom:** After `deepfilter_torchaudio_compat_applied`, build fails in `init_df()` → `get_commit_hash()`.
*   **Fix (built in):** `apply_deepfilter_git_compat()` stubs git helpers in `torchaudio_compat.py` (no `apt install git` needed).

### ❌ Error 2g: `init_df() got an unexpected keyword argument 'model_name'` (runtime denoise)

`deepfilternet==0.5.6` exposes `init_df(default_model=...)`, not `model_name=`.

*   **Symptom:** Transcription fails at pipeline step 1b (denoise) with HTTP 500; logs show `TypeError` in `gigaam/pipeline/denoise.py` after ffmpeg preprocess.
*   **Fix (built in):** `denoise_audio()` calls `init_df(default_model=model_name, model_base_dir=...)`; `GIGAAM_DENOISE_MODEL` maps to DeepFilterNet's `default_model` parameter.
*   **Verify:** rebuild `gigaam-service` (`.\build-gigaam.ps1`), recreate the container, run a job with `GIGAAM_DENOISE=true` — logs should show `Denoise complete:` without `TypeError`.

### ❌ Error 2h: DeepFilterNet3 weights missing — denoise silently disabled on every restart

`init_df()` tried to fetch DeepFilterNet3 weights from GitHub at runtime but couldn't reach `github.com` from inside the container.

*   **Root cause:** `gigaam-service` sits on `inference_net` which is `internal: true` (see `docker-compose.yml`). Docker's embedded DNS for internal networks does not forward external hostnames — `github.com` resolves to `Temporary failure in name resolution` by design. The bootstrap `download_deepfilter` step exhausts its retries, logs `DeepFilterNet download failed, denoising disabled for this session`, and continues to `healthy` — but the weights are never written to the volume, so the same failure repeats on every restart.
*   **Why it happened (historical):** Older bootstrap (before v1.2.0) called only `verify_deepfilter()` which checked wheel importability, not actual weight presence. Weights landed in the container writable layer (not the volume) and were lost on recreation.
*   **Fix (built in since v1.2.0):** DeepFilterNet3 weights are **baked into the Docker image** at build time (where the build host can reach `github.com`) into `/opt/deepfilter/`. Bootstrap seeds them from there to the volume offline via `seed_deepfilter_from_baked()` — no network call needed from inside the container. At runtime `init_df()` receives `model_base_dir=<volume-path>` and never makes network requests. The network download path remains as a fallback for scenarios where the image was built without baking (e.g. custom builds without internet on the build host).
*   **Symptom (pre-fix):** `/health` shows `ready=true` but `denoise_enabled=false`; logs contain `denoising disabled for this session` on every startup.
*   **Resolution after upgrade:** rebuild the image (`.\build-gigaam.ps1` / `./build-gigaam.sh`) so that weights are baked in; then recreate the container. Bootstrap log should show `Seeded DeepFilterNet ... from baked image cache (offline)` and `/health` should report `denoise_enabled=true` with `deepfilter` in `cached_models`.
*   **If using `GIGAAM_OFFLINE_MODE=true` without DeepFilter weights in the tar:** bootstrap will fail with a clear `CacheIncompleteError` listing `deepfilter:DeepFilterNet3`. Either include the weights in the tar (re-seed via `scripts/seed_gigaam_cache.sh`) or set `GIGAAM_DENOISE=false`.

### ❌ Error 3: "Read timed out" or "Hash mismatch" during pip download
When building `gigaam-service`, pip may download large PyPI packages (e.g. `pyannote.audio`). Slower networks can cause timeouts.

*   **Note:** The image no longer downloads model weights at build time — only pip dependencies.
*   **Solution:** GigaAM `install_deps.sh` uses `--default-timeout=1000` and `--retries 10`. If needed:
    ```bash
    docker compose build --network=host gigaam-service
    ```

---

### ❌ Error 3b: GigaAM container fails to start GPU / `CUDA driver version is insufficient`
The `gigaam-service` image ships CUDA **12.8** (PyTorch 2.9.1 runtime). Older host drivers (e.g. 525–565) may work for other services but **not** for this container.

*   **Cause:** NVIDIA driver on the host is below the minimum for CUDA 12.8 (typically **< 570.26**).
*   **Solution:**
    *   Upgrade the proprietary NVIDIA driver to **570.26 or newer**, then restart Docker and verify:
        ```bash
        nvidia-smi
        docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi
        ```
    *   Rebuild and restart: `.\start.ps1 --build gigaam-service` (Linux: `./start.sh --build gigaam-service`). The start scripts forward args to Compose and strip loopback proxies first.

---

### ❌ Error 4: "NameError: name 'EOF' is not defined"
*   **Cause:** A shell heredoc or copy-paste artifact was written directly into the python script `whisperx/download_models.py`.
*   **Solution:** This has been **fully resolved** by cleanly editing out the `EOF` line from `download_models.py`.

---

### ❌ Error 5: "CUDA Out-Of-Memory (OOM)" on RTX 3060/3080
Running multiple heavy deep learning models (Whisper Large V3 FP16, GigaAM RNNT, and Pyannote Diarization) at the same time can exceed the GPU's memory limit. Regression tests were run on **RTX 3060 (12 GB)**; **RTX 3080 (10 GB)** has less headroom, especially with `diarize=true` on long files.

*   **Cause:** Both services attempting to hold active models in GPU VRAM, WhisperX batch size too large for available VRAM, or the 1B Wav2Vec2 alignment model (`WHISPERX_ALIGN_MODEL`) using more VRAM than the legacy `wav2vec2-large-xlsr-53-russian` default.
*   **Solution (required):**
    *   Use the **Gateway** only. It implements a strict **async lock** and VRAM exclusivity: before switching engines it calls `POST /unload` on the other service (`torch.cuda.empty_cache()`). Do **not** call inference containers on port 9007 directly.
    *   Lower **`WHISPERX_BATCH_SIZE`** in `.env` (e.g. `4` → `2` → `1`), then restart: `.\start.ps1` (Linux: `./start.sh`). This reduces peak VRAM during transcription **without changing model weights or precision**. On **10 GB** GPUs, start with `2` or `1` if OOM appears after upgrading the alignment model.
*   **Not allowed (quality policy):**
    *   Do **not** set `WHISPERX_COMPUTE_TYPE` to `int8`, `int8_float16`, or any value other than **`float16`** on CUDA. The WhisperX service rejects unsupported compute types at startup.
    *   Do **not** switch to a smaller ASR model without an explicit product decision — that changes accuracy, not just memory tuning.

---

### ❌ Error 6: GigaAM long audio / 25-second limit / alignment OOM
GigaAM ONNX ASR accepts chunks **≤ 25 seconds** only.

*   **Cause:** Raw long audio fed into GigaAM without chunking, or Wav2Vec2 alignment run on full 1h file (emissions matrix OOM).
*   **Solution (built into `gigaam/pipeline/`):**
    *   **Silero VAD** on denoised audio → chunks ≤ **24.9 s**; **Force Split** at min RMS if speech has no pauses.
    *   **ONNX batch ASR** per chunk; **chunked Wav2Vec2 alignment** with `flush_memory` between GPU stages.
    *   **Split-path:** Pyannote on **original** 16 kHz audio; VAD/ASR/alignment on **denoised** audio; IoW merge for speakers.
    *   Set `GIGAAM_DENOISE=false` if denoise over-suppresses clean recordings.
    *   For 10 GB GPUs (RTX 3080): `GIGAAM_BATCH_SIZE=2` or `1`; ensure gateway VRAM exclusivity (`POST /unload` between engines).

### ❌ Error 6b: `CUDA out of memory. Tried to allocate 4.80 GiB` during denoise on long audio

DeepFilterNet3 VRAM OOM when denoising long recordings (≥ ~10 min on 10 GB cards).

* **Symptom:** HTTP 500 with `torch.OutOfMemoryError: CUDA out of memory. Tried to allocate X GiB` in logs; traceback through `enhance.py → deepfilternet3.py → F.conv2d`; occurs during pipeline step 1b (denoise), before VAD/ASR.
* **Cause (pre-fix):** DeepFilterNet3 processed the **entire file as one tensor** — VRAM scaled linearly with duration (~70 KB/STFT-frame × 209 600 frames for 35 min → 14.66 GB required on a 10 GB card).
* **Fix (built in):** Denoise now uses **windowed processing** — the 48 kHz waveform is split into overlapping windows (default 30 s, 2 s crossfade overlap), each processed individually on GPU, then stitched with a linear crossfade. Peak VRAM during denoise stays **under 1 GB** regardless of audio length. On per-window OOM, the window is halved and retried; last resort is CPU for that window.
* **Tuning knobs** (`.env`):
  * `GIGAAM_DENOISE_CHUNK_SEC=30` — window size (seconds); lower = less VRAM.
  * `GIGAAM_DENOISE_OVERLAP_SEC=2` — crossfade overlap (seconds); keeps boundary seamless.
* **If you still see OOM:** lower `GIGAAM_DENOISE_CHUNK_SEC` (e.g. `10`) or set `GIGAAM_DENOISE=false`.

### ❌ Error 6c: `CUDA out of memory` during diarization on long audio (RTX 3060 6 GB)

Pyannote diarization OOM when `diarize=true` on longer recordings, especially on 6 GB cards.

* **Symptom:** HTTP 500 with `CUDA out of memory` in logs during pipeline step 3 (Pyannote), before VAD/ASR. On a 35-min file, peak diarization VRAM is ~9.7 GB at the default segmentation batch size.
* **Cause:** Pyannote's segmentation `Inference` runs at its built-in batch size (~32); peak VRAM scales with that batch size.
* **Fix (built in):** Lower the segmentation batch size via `GIGAAM_DIARIZE_BATCH_SIZE` (default `8`, applied to `pipeline._segmentation.batch_size`). The startup log line `Diarization segmentation batch_size=N (VRAM cap)` confirms it took effect.
* **Tuning knob** (`.env`): `GIGAAM_DIARIZE_BATCH_SIZE=8` — lower = less peak VRAM, slightly slower (~+30–60 s on a 35-min file).
* **If you still see OOM:** lower further (e.g. `4` for RTX 3060 6 GB). If the log shows a `no _segmentation.batch_size` **warning** instead of the confirmation line, the knob did not apply — see `gigaam/pipeline/diarization.py`.

---

## 🌐 3. NAT & Single-Port Deployment via Cloudflare

Если ваш сервер находится за NAT (например, домашний ПК за роутером у провайдера, предоставляющего только один внешний открытый порт, или VPS с ограниченным пулом портов), вы можете настроить систему так, чтобы она была доступна по стандартному HTTPS-адресу без указания нестандартного порта в URL (то есть по `https://transcribe.yourdomain.com` вместо `https://transcribe.yourdomain.com:8443`).

Благодаря тому, что OAITT-PRO использует **DNS-01 челлендж** (через Cloudflare API) для генерации SSL-сертификатов, Certbot **не требует открытых входящих портов 80/443** на вашем хосте для подтверждения владения доменом. Все запросы к Let's Encrypt и Cloudflare выполняются локально через исходящие HTTPS-запросы.

Для работы через один нестандартный порт выполните следующие шаги:

### ⚙️ Шаг A. Настройка переменных окружения (.env)
Отредактируйте файл `.env` на сервере. Укажите нужный вам открытый порт (например, `8443`), который вы будете пробрасывать на роутере:

```env
API_PUBLIC_HOST=transcribe.yourdomain.com
API_UPLOAD_HOST=direct.yourdomain.com
PROXY_PORT_HTTPS=8443
PROXY_PORT_HTTP=8080
```

If large uploads use a dedicated public port (e.g. 3000), set `PROXY_PORT_HTTP=3000` on the host instead of relying on a separate NAT mapping.

Запустите контейнеры:
```bash
docker-compose up -d --build
```
Nginx (фронт-прокси) теперь будет слушать порт `8443` (для HTTPS) и `8080` (для HTTP) на вашей хост-машине.

### 🔌 Шаг B. Проброс порта на роутере (Port Forwarding)
Настройте ваш роутер (или файрвол провайдера/VPS) для перенаправления входящего трафика с внешнего IP-адреса на порт `8443` вашего сервера по протоколу TCP.

### ☁️ Шаг C. Настройка Cloudflare CDN (Origin Rules)
Чтобы пользователи могли обращаться к серверу по красивому адресу без указания порта в URL, используйте встроенный механизм **Origin Rules** в Cloudflare, который на лету перепишет порт назначения с 443 на ваш нестандартный порт.

1. Войдите в **Cloudflare Dashboard**.
2. Перейдите в раздел вашего домена (например, `yourdomain.com`).
3. В левом меню выберите **Rules** ➔ **Origin Rules** (Правила ➔ Правила для источника).
4. Нажмите кнопку **Create rule** (Создать правило).
5. Заполните поля следующим образом:
   * **Rule name (Имя правила):** `Redirect standard 443 to NAT custom port`
   * **If incoming requests match... (Если входящие запросы соответствуют...):**
     * **Field (Поле):** `Hostname` (Имя хоста)
     * **Operator (Оператор):** `equals` (равно)
     * **Value (Значение):** `transcribe.yourdomain.com` (укажите поддомен вашего сервиса)
   * **Then (Тогда):**
     * **Destination Port (Порт назначения):** Выберите **Rewrite to...** (Переписать в...)
     * **Value (Значение):** `8443` (ваш открытый внешний порт за NAT)
6. Нажмите **Deploy** (Развернуть) в правом нижнем углу.

### 🔒 Шаг D. Настройка SSL/TLS в Cloudflare
Для корректной работы проксирования и сквозного шифрования между Cloudflare и вашим Nginx:
1. Перейдите во вкладку **SSL/TLS** ➔ **Overview** (в панели Cloudflare для вашего домена).
2. Переключите режим шифрования в **Full (Strict)**.
   * *Почему Strict?* Так как ваш Nginx за NAT имеет легитимный и валидный wildcard-сертификат, автоматически выпущенный Certbot через DNS-01, соединение между серверами Cloudflare и вашим роутером полностью валидно и безопасно.

---
### 🔄 Как это работает (Схема трафика):
```
[Клиент] --- (HTTPS/Порт 443) ---> [Cloudflare CDN]
                                         │
                 (Перезапись порта 443 ➔ 8443 в Origin Rules)
                                         │
                                         ▼
[Роутер NAT] <--- (HTTPS/Порт 8443) ─────┘
     │
 (Проброс TCP 8443 ➔ Host 8443)
     │
     ▼
[Nginx Docker (oaitt-proxy)]
     │
     ▼
[API Orchestrator (oaitt-gateway:9000)]
```

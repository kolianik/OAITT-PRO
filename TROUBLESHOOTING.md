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

### ❌ Error 2: "SSL: CERTIFICATE_VERIFY_FAILED" during Pip Install
This occurs when Python/pip inside the container fails to verify Let's Encrypt or other SSL certificates when communicating with PyPI or PyTorch's download servers.

*   **Cause:** Guest container certificate store is missing local issuer certificates, or you are behind a corporate proxy/firewall that intercepts SSL traffic.
*   **Solution:**
    *   This has been **automatically resolved** in OAITT-PRO's Dockerfiles by adding `--trusted-host` arguments for `download.pytorch.org`, `pypi.org`, and `files.pythonhosted.org`.
    *   If you install any additional packages inside the containers, append the trusted hosts flags:
        ```bash
        pip install <package_name> --trusted-host pypi.org --trusted-host files.pythonhosted.org
        ```
    *   For host-side `prepare.py` / GigaAM weight downloads behind a corporate TLS proxy, SSL verification is **enabled by default**. Only for local development set `PREPARE_INSECURE_SSL=1` before running `python prepare.py` (never in production).

---

### ❌ Error 3: "Read timed out" or "Hash mismatch" during PyTorch download
When building `gigaam-service`, pip downloads over 1.5 GB of CUDA PyTorch binaries. Slower networks or connection dips can cause pip's socket connection to time out, resulting in corrupted downloads or hash mismatches.

*   **Cause:** Pip's default connection read timeout is very small (15 seconds).
*   **Solution:**
    *   This has been **automatically resolved** in OAITT-PRO by adding `--default-timeout=1000` to GigaAM's `pip install` step. This gives pip up to 16.6 minutes of buffer to stream large wheels smoothly.
    *   If you still hit timeouts, run Docker build with host network configuration to maximize download speeds:
        ```bash
        docker-compose build --network=host
        ```

---

### ❌ Error 4: "NameError: name 'EOF' is not defined"
*   **Cause:** A shell heredoc or copy-paste artifact was written directly into the python script `whisperx/download_models.py`.
*   **Solution:** This has been **fully resolved** by cleanly editing out the `EOF` line from `download_models.py`.

---

### ❌ Error 5: "CUDA Out-Of-Memory (OOM)" on RTX 3060/3080
Running multiple heavy deep learning models (Whisper Large V3 FP16, GigaAM RNNT, and Pyannote Diarization) at the same time can exceed the GPU's memory limit. Regression tests were run on **RTX 3060 (12 GB)**; **RTX 3080 (10 GB)** has less headroom, especially with `diarize=true` on long files.

*   **Cause:** Both services attempting to hold active models in GPU VRAM, or WhisperX batch size too large for available VRAM.
*   **Solution (required):**
    *   Use the **Gateway** only. It implements a strict **async lock** and VRAM exclusivity: before switching engines it calls `POST /unload` on the other service (`torch.cuda.empty_cache()`). Do **not** call inference containers on port 9007 directly.
    *   Lower **`WHISPERX_BATCH_SIZE`** in `.env` (e.g. `4` → `2` → `1`), then restart: `docker compose up -d`. This reduces peak VRAM during transcription **without changing model weights or precision**.
*   **Not allowed (quality policy):**
    *   Do **not** set `WHISPERX_COMPUTE_TYPE` to `int8`, `int8_float16`, or any value other than **`float16`** on CUDA. The WhisperX service rejects unsupported compute types at startup.
    *   Do **not** switch to a smaller ASR model without an explicit product decision — that changes accuracy, not just memory tuning.

---

### ❌ Error 6: GigaAM "ValueError: Too long wav file" (Limit > 25 seconds)
GigaAM's core `.transcribe()` tensor engine is strictly mathematically limited to audio files shorter than 25 seconds. Passing files longer than this causes immediate value errors.

*   **Cause:** Attempting to feed raw long audio arrays into GigaAM's fast tensor encoder.
*   **Solution:**
    *   Our GigaAM Service implements a **custom Pyannote-VAD chunker** inside `gigaam/main.py`.
    *   When an audio file is uploaded, the service runs Pyannote diarization first. It slices the audio into short speech turns belonging to speakers (each guaranteed to be < 20s).
    *   It feeds these short crops directly into GigaAM's GPU tensor memory, and merges the segments back into a single structured response.
    *   **This completely bypasses GigaAM's 25s limitation without double-loading model components, running at maximum GPU speed.**

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

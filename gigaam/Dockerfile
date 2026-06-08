FROM python:3.11-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/data \
    TORCH_HOME=/app/data/torch

RUN groupadd -r -g 1001 oaitt && useradd -r -u 1001 -g oaitt oaitt

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    libgomp1 \
    git \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch with CUDA 12.1 support
RUN pip install --no-cache-dir --default-timeout=1000 \
    --trusted-host download.pytorch.org \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org \
    torch==2.5.1 \
    torchaudio==2.5.1 \
    --index-url https://download.pytorch.org/whl/cu121

# Install requirements
COPY gigaam/requirements.txt .
RUN pip install --no-cache-dir --default-timeout=1000 --retries 10 \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org \
    -r requirements.txt

# Copy vendor/gigaam and install as local package
COPY vendor/gigaam /app/vendor/gigaam
RUN pip install --no-cache-dir --no-deps /app/vendor/gigaam && \
    rm -rf /app/vendor/gigaam

# Copy GigaAM models pre-downloaded on host
COPY data/gigaam/ /app/data/gigaam/

COPY shared/ /app/shared/
COPY gigaam/main.py .

RUN mkdir -p /shared_data && chown -R oaitt:oaitt /app /shared_data

USER oaitt

EXPOSE 9007

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9007"]

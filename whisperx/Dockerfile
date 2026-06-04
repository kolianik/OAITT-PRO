FROM ghcr.io/jim60105/whisperx:latest

USER root

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/data

# Ensure pip is installed (since the base image uninstalls it to save space)
RUN python3 -m ensurepip --upgrade

# Install web-service requirements and pyannote
COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org \
    -r requirements.txt

# Create cache directory
RUN mkdir -p /app/data

# Copy pre-downloader and run it to bake models
COPY download_models.py .
# RUN python download_models.py

# Copy application files
COPY main.py .

EXPOSE 9007

ENTRYPOINT []
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9007"]

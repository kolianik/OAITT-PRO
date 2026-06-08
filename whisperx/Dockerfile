FROM ghcr.io/jim60105/whisperx:latest

USER root

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/data

# Ensure pip is installed (since the base image uninstalls it to save space)
RUN python3 -m ensurepip --upgrade

# Install web-service requirements and pyannote
COPY whisperx/requirements.txt .
RUN python3 -m pip install --no-cache-dir \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org \
    -r requirements.txt

# Create cache and shared directories (base image user: whisperx uid 1001)
RUN mkdir -p /app/data /shared_data

COPY shared/ /app/shared/
COPY whisperx/main.py .

RUN chown -R whisperx:whisperx /app /shared_data

USER whisperx

EXPOSE 9007

ENTRYPOINT []
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9007"]

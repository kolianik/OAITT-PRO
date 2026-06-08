FROM python:3.11-slim

WORKDIR /app

RUN groupadd -r -g 1001 oaitt && useradd -r -u 1001 -g oaitt oaitt

COPY gateway/requirements.txt .
RUN pip install --no-cache-dir \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org \
    -r requirements.txt

COPY shared/ /app/shared/
COPY gateway/ /app/

RUN mkdir -p /shared_data && chown -R oaitt:oaitt /app /shared_data

USER oaitt

EXPOSE 9000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]

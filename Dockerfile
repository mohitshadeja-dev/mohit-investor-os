FROM python:3.11-slim

WORKDIR /app

COPY mohit_investor_os_app.zip /app/app.zip
COPY prepare_frontend.py /app/prepare_frontend.py

RUN apt-get update && apt-get install -y unzip && \
    unzip /app/app.zip -d /app && \
    python /app/prepare_frontend.py && \
    pip install --no-cache-dir -r /app/requirements.txt && \
    mv /usr/local/bin/uvicorn /usr/local/bin/uvicorn-real && \
    printf '%s\n' '#!/bin/sh' \
      'for arg in "$@"; do' \
      '  if [ "$arg" = "\$PORT" ]; then' \
      '    p="${PORT:-8000}"' \
      '    case "$p" in (*[!0-9]*|"") p=8000;; esac' \
      '    exec /usr/local/bin/uvicorn-real app.main:app --host 0.0.0.0 --port "$p"' \
      '  fi' \
      'done' \
      'exec /usr/local/bin/uvicorn-real "$@"' \
      > /usr/local/bin/uvicorn && chmod +x /usr/local/bin/uvicorn

EXPOSE 8000

CMD ["sh", "-c", "p=${PORT:-8000}; case \"$p\" in (*[!0-9]*|\"\") p=8000;; esac; exec /usr/local/bin/uvicorn-real app.main:app --host 0.0.0.0 --port \"$p\""]
